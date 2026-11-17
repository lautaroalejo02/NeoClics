// Song + pieces -> the Arrangement the browser plays. Pure (no Web Audio
// calls besides reading AudioBuffer durations), so it is unit-tested.

import type { ArrEvent, Arrangement, ArrTrack } from "../audio/arrangement";
import {
  BEATS_PER_BAR,
  automationValue,
  chopById,
  clipPasses,
  instrumentFingerprint,
  patternHits,
  songSeconds,
  stable,
  trackKind,
  trackNotes,
  trackWindow,
  wetFingerprint,
  type ClipPass,
  type SongJson,
  type TrackJson,
} from "./song";

export interface MaterialPiece {
  chop: string | null;
  sample: string | null;
  start: number;
  length: number;
  stretch: number;
  pitch: number;
  tailFrames: number;
  base: boolean;
  /** stable JSON of the chop (or clip warp+sample) this material was rendered from */
  sourceKey: string;
  buffer: AudioBuffer;
}

export interface PieceSet {
  sampleRate: number;
  materials: MaterialPiece[];
  oneshots: Map<string, AudioBuffer>;
  /** per track: wet stem + the wet fingerprint it was rendered for */
  wet: Map<string, { fp: string; buffer: AudioBuffer }>;
  /** per instrument track: dry stem + fingerprint */
  inst: Map<string, { fp: string; buffer: AudioBuffer }>;
}

export function emptyPieces(sampleRate = 48000): PieceSet {
  return { sampleRate, materials: [], oneshots: new Map(), wet: new Map(), inst: new Map() };
}

export type TrackAudioState = "exact" | "preview" | "waiting" | "silent";

export interface TrackStatus {
  state: TrackAudioState;
  /** clip passes / hits that have no audio yet */
  missing: number;
  /** the instrument stem is from an older version of the notes */
  stale: boolean;
}

export function materialKey(song: SongJson, p: ClipPass, clipWarp: unknown): string {
  if (p.chop) return stable(chopById(song, p.chop) ?? null);
  return stable({ sample: p.sample, warp: clipWarp ?? null });
}

/** The material that can play this pass, and where in it the pass starts. */
export function findMaterial(pieces: PieceSet, song: SongJson, track: TrackJson, p: ClipPass): { m: MaterialPiece; offsetBeats: number } | null {
  const clip = track.clips?.[p.clipIndex];
  const key = materialKey(song, p, clip?.warp);
  const near = (a: number, b: number) => Math.abs(a - b) < 1e-6;
  for (const m of pieces.materials) {
    if (m.sourceKey !== key || m.chop !== p.chop || m.sample !== p.sample) continue;
    if (m.base) {
      if (p.stretch === 1 && p.pitch === 0 && p.materialStart >= -1e-9 && p.materialStart + p.length <= m.length + 1e-9) {
        return { m, offsetBeats: p.materialStart };
      }
      continue;
    }
    if (near(m.start, p.materialStart) && near(m.length, p.length) && near(m.stretch, p.stretch) && near(m.pitch, p.pitch)) return { m, offsetBeats: 0 };
  }
  return null;
}

function fadeSec(ms: number | null, fallbackS: number): number {
  return ms == null ? fallbackS : ms / 1000;
}

export interface BuildOptions {
  solo: Set<string>;
}

export function buildArrangement(song: SongJson, pieces: PieceSet, opts: BuildOptions): { arr: Arrangement; status: Record<string, TrackStatus> } {
  const spb = 60 / song.meta.bpm;
  const duration = songSeconds(song);
  const sr = pieces.sampleRate;
  const events: ArrEvent[] = [];
  const tracks: ArrTrack[] = [];
  const triggers: Record<string, number[]> = {};
  const status: Record<string, TrackStatus> = {};
  const barToS = (bar: number) => (bar - 1) * BEATS_PER_BAR * spb;
  for (const t of song.tracks ?? []) {
    const kind = trackKind(t);
    const wetEntry = pieces.wet.get(t.id);
    const wet = wetEntry && wetEntry.fp === wetFingerprint(song, t.id) ? wetEntry.buffer : null;
    const inst = kind === "instrument" ? pieces.inst.get(t.id) : undefined;
    const lanes: ArrTrack["lanes"] = {};
    for (const [name, lane] of Object.entries(t.pattern?.lanes ?? {})) lanes[name] = { gainDb: lane.gain_db ?? 0, pan: lane.pan ?? 0 };
    const automation: ArrTrack["automation"] = {};
    for (const a of song.automation ?? []) {
      const m = new RegExp(`^tracks\\.${t.id}\\.fx\\.([^.]+)\\.(cutoff_hz|gain_db)$`).exec(a.target);
      if (m) automation[`${m[1]}.${m[2]}`] = (s: number) => automationValue(a, s / (BEATS_PER_BAR * spb) + 1);
    }
    let missing = 0;
    const { until } = trackWindow(song, t);
    const endS = Math.min(duration, barToS(until));
    if (kind === "pattern") {
      const hits = patternHits(song, t);
      const seen = new Map<string, number>();
      for (const h of hits) {
        const buf = pieces.oneshots.get(h.sample);
        if (!buf) {
          missing++;
          continue;
        }
        const time = h.beat0 * spb;
        const base = `${t.id}|h|${h.lane}|${h.beat0.toFixed(6)}|${h.velocity.toFixed(4)}|${h.sample}`;
        const n = seen.get(base) ?? 0;
        seen.set(base, n + 1);
        events.push({
          key: n ? `${base}#${n}` : base,
          track: t.id,
          lane: h.lane,
          time,
          buffer: buf,
          offset: 0,
          duration: Math.max(0.001, Math.min(buf.duration, endS - time)),
          tail: 0,
          gain: h.velocity,
          fadeIn: null,
          fadeOut: buf.duration > endS - time ? { len: Math.min(0.005, endS - time), shape: "cos", inTail: false } : null,
        });
      }
      if (!t.mute) {
        const all: number[] = [];
        for (const h of hits) {
          const s = h.beat0 * spb;
          (triggers[`${t.id}.${h.lane}`] ??= []).push(s);
          all.push(s);
        }
        triggers[t.id] = [...new Set(all)].sort((a, b) => a - b);
      }
    } else if (kind === "clips") {
      const passes = clipPasses(song, t).filter((p) => !p.mute);
      const order = [...passes].sort((a, b) => a.start0 - b.start0);
      const seamS = (p: ClipPass) => (p.chop ? (chopById(song, p.chop)?.fade_ms ?? 5) : 5) / 1000;
      const explicitIn = (p: ClipPass) => p.fadeInMs != null || p.reverse;
      const explicitOut = (p: ClipPass) => p.fadeOutMs != null || p.reverse;
      const next = new Map<ClipPass, ClipPass>();
      const prev = new Map<ClipPass, ClipPass>();
      for (const a of order) {
        if (explicitOut(a)) continue;
        const b = order.find((x) => x !== a && !prev.has(x) && !explicitIn(x) && Math.abs(x.start0 - a.end0) < 1e-6);
        if (b && !next.has(a)) {
          next.set(a, b);
          prev.set(b, a);
        }
      }
      // loop wrap: the last pass's tail crossfades into bar 1 (render loops)
      const first = order.find((p) => Math.abs(p.start0) < 1e-9 && !prev.has(p) && !explicitIn(p));
      const last = [...order].reverse().find((p) => p.end0 >= song.meta.length_bars * BEATS_PER_BAR - 1e-9 && !next.has(p) && !explicitOut(p));
      if (first && last && first !== last) {
        next.set(last, first);
        prev.set(first, last);
      }
      const seen = new Map<string, number>();
      for (const p of passes) {
        const clip = t.clips![p.clipIndex];
        const found = findMaterial(pieces, song, t, p);
        if (!found) {
          missing++;
          continue;
        }
        const xf = Math.min(seamS(p), found.m.tailFrames / sr);
        const linkedIn = prev.has(p);
        const linkedOut = next.has(p);
        const dur = p.length * spb;
        const inLen = linkedIn ? xf : Math.min(fadeSec(p.fadeInMs, p.reverse ? xf : seamS(p)), dur / 2);
        const outLen = linkedOut ? xf : Math.min(fadeSec(p.fadeOutMs, p.reverse ? xf : seamS(p)), dur / 2);
        const base = `${t.id}|c|${p.chop ?? p.sample}|${p.start0.toFixed(6)}|${p.length.toFixed(6)}|${p.materialStart.toFixed(6)}|${p.stretch}|${p.pitch}|${p.reverse}|${p.gainDb}|${linkedIn}|${linkedOut}|${stable(clip.warp ?? null)}`;
        const n = seen.get(base) ?? 0;
        seen.set(base, n + 1);
        events.push({
          key: n ? `${base}#${n}` : base,
          track: t.id,
          time: p.start0 * spb,
          buffer: found.m.buffer,
          offset: found.offsetBeats * spb,
          duration: dur,
          tail: linkedOut ? xf : 0,
          gain: 10 ** (p.gainDb / 20),
          reverse: p.reverse,
          fadeIn: inLen > 0.0004 ? { len: inLen, shape: linkedIn ? "eq" : "cos" } : null,
          fadeOut: outLen > 0.0004 ? { len: outLen, shape: linkedOut ? "eq" : "cos", inTail: linkedOut } : null,
          sustained: true,
        });
      }
    } else {
      if (!t.mute) triggers[t.id] = trackNotes(song, t).map((n) => (n.beat - 1) * spb);
      if (!wet && !inst) missing++;
    }
    const fpInst = kind === "instrument" ? instrumentFingerprint(song, t) : "";
    status[t.id] = {
      state: wet ? "exact" : missing && kind !== "instrument" ? "waiting" : kind === "instrument" && !inst ? "silent" : "preview",
      missing,
      stale: !!inst && inst.fp !== fpInst && !wet,
    };
    // automated gain/pan REPLACE the static value (it lives in the wet stem)
    const targets = new Set((song.automation ?? []).map((a) => a.target));
    tracks.push({
      id: t.id,
      gainDb: targets.has(`tracks.${t.id}.gain_db`) ? 0 : (t.gain_db ?? 0),
      pan: targets.has(`tracks.${t.id}.pan`) ? 0 : (t.pan ?? 0),
      mute: !!t.mute,
      solo: opts.solo.has(t.id),
      wet,
      dryStem: inst?.buffer ?? null,
      lanes,
      fx: t.fx ?? [],
      automation,
    });
  }
  events.sort((a, b) => a.time - b.time);
  return { arr: { duration, tracks, events, triggers }, status };
}
