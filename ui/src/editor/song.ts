// song.yaml as the editor sees it (normalized JSON from song.state / song.edit)
// and the ONE expansion of "which hit / clip pass sounds when". This mirrors
// src/muestra/schemas/song.py (pattern_hits, clip_passes, lane_bar_steps)
// line for line, with the same floating-point arithmetic, so the browser
// schedules exactly the hits and passes the renderer renders.

export const BEATS_PER_BAR = 4;

export interface WarpJson {
  mode?: "resample" | "stretch";
  pitch_semitones?: number;
  formants?: boolean | null;
}

export interface ChopJson {
  id: string;
  source: string;
  stem?: string;
  start_downbeat: number;
  start_offset_beats?: number;
  length_beats: number;
  warp?: WarpJson;
  fade_ms?: number;
}

export interface InstanceJson {
  mute?: boolean;
  gain_db?: number;
  pitch_semitones?: number;
  reverse?: boolean;
}

export interface ClipJson {
  chop?: string | null;
  sample?: string | null;
  length_beats?: number | null;
  warp?: WarpJson | null;
  at_bar: number;
  offset_beats?: number;
  repeat?: number;
  trim_start_beats?: number;
  stretch?: number;
  gain_db?: number;
  reverse?: boolean;
  fade_in_ms?: number | null;
  fade_out_ms?: number | null;
  instances?: Record<string, InstanceJson>;
}

/** A slice of a chop or of a source played as a lane sound (WP17, schemas.song.LaneSlice). */
export interface SliceJson {
  chop?: string | null;
  source?: string | null;
  stem?: string | null;
  start_downbeat?: number | null;
  start_beats?: number;
  length_beats: number;
  speed?: "project" | "source";
  warp?: WarpJson | null;
}

export interface LaneJson {
  /** a file; exactly one of sample / slice (WP17) */
  sample?: string | null;
  slice?: SliceJson | null;
  steps?: string;
  gain_db?: number;
  pan?: number;
  overrides?: Record<string, string>;
  /** WP17: sampler tuning (resampled), reverse, choke group 1-16 */
  pitch_semitones?: number;
  reverse?: boolean;
  choke?: number | null;
}

export interface FreeHitJson {
  beat: number;
  lane: string;
  velocity?: number;
  sample?: string | null;
  /** WP17 gate: cut after this many beats */
  length_beats?: number | null;
}

export interface PatternJson {
  steps_per_bar?: number;
  bars?: number;
  lanes: Record<string, LaneJson>;
  swing?: number;
  hits?: FreeHitJson[];
}

export interface NoteJson {
  beat: number;
  pitch: number;
  length_beats: number;
  velocity?: number;
}

export interface PhraseJson {
  id: string;
  length_bars: number;
  notes: NoteJson[];
}

export interface PlacementJson {
  phrase: string;
  at_bar?: number | null;
  repeat?: number | null;
  transpose_semitones?: number;
}

export interface FxJson {
  id?: string | null;
  type: string;
  [param: string]: unknown;
}

export interface TrackJson {
  id: string;
  clips?: ClipJson[] | null;
  pattern?: PatternJson | null;
  instrument?: { plugin: string; preset?: string | null; params?: Record<string, number> } | null;
  midi?: string | null;
  notes?: NoteJson[] | null;
  phrases?: PlacementJson[] | null;
  at_bar?: number;
  until_bar?: number | null;
  fx?: FxJson[];
  gain_db?: number;
  pan?: number;
  mute?: boolean;
}

export interface AutomationJson {
  target: string;
  curve?: "lin" | "exp" | "step";
  points: [number, number][];
}

export interface SongJson {
  schema_version?: 0;
  meta: { title: string; bpm: number; key: string; length_bars: number; seed?: number; time_signature?: string };
  sources?: { id: string; file: string }[];
  chops?: ChopJson[];
  phrases?: PhraseJson[];
  tracks?: TrackJson[];
  automation?: AutomationJson[];
  master?: { fx?: FxJson[]; gain_db?: number; target_lufs?: number | null };
}

export type TrackKind = "clips" | "pattern" | "instrument";

export function trackKind(t: TrackJson): TrackKind {
  if (t.clips != null) return "clips";
  if (t.pattern != null) return "pattern";
  return "instrument";
}

export function songBeats(song: SongJson): number {
  return song.meta.length_bars * BEATS_PER_BAR;
}

export function songSeconds(song: SongJson): number {
  return (songBeats(song) * 60) / song.meta.bpm;
}

// ------------------------------------------------------------------ steps

/** Velocity per step: x=1, o=0.6, 1-9 = n/9, ./-/_ = rest, spaces ignored. */
export function parseSteps(steps: string | undefined | null): (number | null)[] {
  const out: (number | null)[] = [];
  for (const c of steps ?? "") {
    if (c === " ") continue;
    if (c >= "1" && c <= "9") out.push(Number(c) / 9);
    else if (c === "x" || c === "X") out.push(1);
    else if (c === "o" || c === "O") out.push(0.6);
    else out.push(null);
  }
  return out;
}

/** The step char a velocity is written with (same rule as song.edit's hit op). */
export function velChar(v: number | null | undefined): string {
  if (v == null || v <= 0) return ".";
  if (Math.abs(v - 1) < 1e-9) return "x";
  if (Math.abs(v - 0.6) < 1e-9) return "o";
  return String(Math.min(9, Math.max(1, Math.round(v * 9))));
}

// ------------------------------------------------------------------ hits

export type HitOrigin = "pattern" | "override" | "free";

export interface DrumHit {
  /** 0-based song beats (0 = bar 1 beat 1), swing included */
  beat0: number;
  lane: string;
  velocity: number;
  origin: HitOrigin;
  /** song bar, 1-based */
  bar: number;
  /** 0-based step in the bar; null for free hits */
  step: number | null;
  /** pattern.hits index for free hits */
  index: number | null;
  /** the file it plays; null = the lane's slice (WP17) */
  sample: string | null;
  /** WP17: 0-based beat where the sound is cut (gate or choke), 3 ms fade; null = plays out */
  cut0: number | null;
}

/** WP17: fade (ms) of a gated or choked hit, starting at its cut point (schemas.song.CUT_FADE_MS). */
export const CUT_FADE_MS = 3;

function laneVelocities(p: PatternJson, lane: LaneJson): (number | null)[] {
  const v = parseSteps(lane.steps);
  return v.length ? v : new Array((p.steps_per_bar ?? 16) * (p.bars ?? 1)).fill(null);
}

function overridesOf(lane: LaneJson): Map<number, string> {
  const m = new Map<number, string>();
  for (const [k, v] of Object.entries(lane.overrides ?? {})) m.set(Number(k), v);
  return m;
}

export function trackWindow(song: SongJson, t: TrackJson): { at: number; until: number } {
  return { at: t.at_bar ?? 1, until: t.until_bar ?? song.meta.length_bars + 1 };
}

export function patternHits(song: SongJson, track: TrackJson): DrumHit[] {
  const p = track.pattern;
  if (!p) return [];
  const spb = p.steps_per_bar ?? 16;
  const bars = p.bars ?? 1;
  const step = BEATS_PER_BAR / spb;
  const { at, until } = trackWindow(song, track);
  const lo = (at - 1) * BEATS_PER_BAR;
  const hi = (until - 1) * BEATS_PER_BAR;
  const swing = ((p.swing ?? 0) * step) / 3;
  const out: DrumHit[] = [];
  for (const [name, lane] of Object.entries(p.lanes)) {
    const vel = laneVelocities(p, lane);
    const ov = overridesOf(lane);
    for (let bar0 = at - 1; bar0 < song.meta.length_bars; bar0 += bars) {
      for (let index = 0; index < vel.length; index++) {
        const v = vel[index];
        const songBar = bar0 + Math.floor(index / spb) + 1;
        if (v == null || ov.has(songBar)) continue;
        const beat = bar0 * BEATS_PER_BAR + index * step + (index % 2 ? swing : 0);
        if (lo - 1e-9 <= beat && beat < hi - 1e-9) {
          out.push({ beat0: beat, lane: name, velocity: v, origin: "pattern", bar: songBar, step: index % spb, index: null, sample: lane.sample ?? null, cut0: null });
        }
      }
    }
    for (const songBar of [...ov.keys()].sort((a, b) => a - b)) {
      const vs = parseSteps(ov.get(songBar));
      vs.forEach((v, s) => {
        if (v == null) return;
        const beat = (songBar - 1) * BEATS_PER_BAR + s * step + (s % 2 ? swing : 0);
        if (lo - 1e-9 <= beat && beat < hi - 1e-9) {
          out.push({ beat0: beat, lane: name, velocity: v, origin: "override", bar: songBar, step: s, index: null, sample: lane.sample ?? null, cut0: null });
        }
      });
    }
  }
  (p.hits ?? []).forEach((h, i) => {
    const lane = p.lanes[h.lane];
    const beat = h.beat - 1;
    if (!lane || !(lo - 1e-9 <= beat && beat < hi - 1e-9)) return;
    out.push({
      beat0: beat,
      lane: h.lane,
      velocity: h.velocity ?? 1,
      origin: "free",
      bar: Math.floor(beat / BEATS_PER_BAR + 1e-9) + 1,
      step: null,
      index: i,
      sample: h.sample || lane.sample || null,
      cut0: h.length_beats != null ? beat + h.length_beats : null,
    });
  });
  out.sort((a, b) => a.beat0 - b.beat0 || (a.lane < b.lane ? -1 : a.lane > b.lane ? 1 : 0));
  return applyChokes(p, out);
}

/** Cut each hit at the next STRICTLY later hit of its lane's choke group (mirror of _apply_chokes). */
export function applyChokes(p: PatternJson, hits: DrumHit[]): DrumHit[] {
  const groups = new Map<string, number>();
  for (const [name, lane] of Object.entries(p.lanes)) if (lane.choke) groups.set(name, lane.choke);
  if (!groups.size) return hits;
  const byGroup = new Map<number, number[]>();
  hits.forEach((h, i) => {
    const g = groups.get(h.lane);
    if (g != null) {
      const l = byGroup.get(g) ?? [];
      l.push(i);
      byGroup.set(g, l);
    }
  });
  const out = hits.slice();
  for (const idx of byGroup.values()) {
    for (let k = 0; k < idx.length; k++) {
      const i = idx[k];
      let later: number | null = null;
      for (let j = k + 1; j < idx.length; j++) {
        if (out[idx[j]].beat0 > out[i].beat0 + 1e-9) {
          later = out[idx[j]].beat0;
          break;
        }
      }
      if (later != null && (out[i].cut0 == null || later < out[i].cut0!)) out[i] = { ...out[i], cut0: later };
    }
  }
  return out;
}

/** A lane/pad sound without gain/pan (editor.pieces SoundSpec). */
export interface SoundJson {
  sample?: string | null;
  slice?: SliceJson | null;
  pitch_semitones?: number;
  reverse?: boolean;
}

function normWarp(w: WarpJson | null | undefined): unknown {
  return w ? { mode: w.mode ?? "resample", pitch_semitones: w.pitch_semitones ?? 0, formants: w.formants ?? null } : null;
}

export function normSlice(s: SliceJson | null | undefined): unknown {
  if (!s) return null;
  return {
    chop: s.chop ?? null,
    source: s.source ?? null,
    stem: s.stem ?? null,
    start_downbeat: s.start_downbeat ?? null,
    start_beats: s.start_beats ?? 0,
    length_beats: s.length_beats,
    speed: s.speed ?? "project",
    warp: normWarp(s.warp),
  };
}

/** True when the sound is a plain file (its buffer is the song.pieces one-shot). */
export function plainSound(s: SoundJson): boolean {
  return !!s.sample && !s.slice && !s.pitch_semitones && !s.reverse;
}

/** Key of a rendered voice (song.pieces voices) — same for the request and the echo. */
export function voiceKey(s: SoundJson): string {
  return stable({ sample: s.sample ?? null, slice: normSlice(s.slice), pitch: s.pitch_semitones ?? 0, reverse: !!s.reverse });
}

/** The sound one hit of a lane plays. */
export function hitSound(lane: LaneJson, hitSample: string | null): SoundJson {
  return { sample: hitSample, slice: hitSample ? null : (lane.slice ?? null), pitch_semitones: lane.pitch_semitones ?? 0, reverse: !!lane.reverse };
}

/** Velocities of one SONG bar of a lane (override, else the tiled pattern). */
export function laneBarSteps(p: PatternJson, lane: LaneJson, bar: number, atBar: number): (number | null)[] {
  const ov = overridesOf(lane);
  if (ov.has(bar)) return parseSteps(ov.get(bar));
  const spb = p.steps_per_bar ?? 16;
  const vel = laneVelocities(p, lane);
  const tile = (((bar - atBar) % (p.bars ?? 1)) + (p.bars ?? 1)) % (p.bars ?? 1);
  return vel.slice(tile * spb, (tile + 1) * spb);
}

/** The chars of one tile bar as written (keeps the author's notation). */
export function laneTileChars(p: PatternJson, lane: LaneJson, bar: number, atBar: number): string {
  const spb = p.steps_per_bar ?? 16;
  const bars = p.bars ?? 1;
  let steps = (lane.steps ?? "").replace(/ /g, "");
  if (!steps) steps = ".".repeat(spb * bars);
  const tile = (((bar - atBar) % bars) + bars) % bars;
  return steps.slice(tile * spb, (tile + 1) * spb);
}

// ------------------------------------------------------------------ clips

export interface ClipPass {
  clipIndex: number;
  /** 1-based repeat number */
  instance: number;
  start0: number;
  end0: number;
  length: number;
  materialStart: number;
  stretch: number;
  gainDb: number;
  pitch: number;
  reverse: boolean;
  mute: boolean;
  fadeInMs: number | null;
  fadeOutMs: number | null;
  chop: string | null;
  sample: string | null;
}

export function chopById(song: SongJson, id: string | null | undefined): ChopJson | undefined {
  return id ? song.chops?.find((c) => c.id === id) : undefined;
}

/** Grid length of one pass (Clip.pass_beats). */
export function passBeats(clip: ClipJson, chopLength: number): number {
  if (clip.length_beats != null) return clip.length_beats;
  return Math.max(0, (chopLength - (clip.trim_start_beats ?? 0)) * (clip.stretch ?? 1));
}

export function clipBaseLength(song: SongJson, clip: ClipJson): number {
  if (clip.chop) return chopById(song, clip.chop)?.length_beats ?? 0;
  return clip.length_beats ?? 0;
}

export function clipStart0(clip: ClipJson): number {
  return (clip.at_bar - 1) * BEATS_PER_BAR + (clip.offset_beats ?? 0);
}

export function clipPasses(song: SongJson, track: TrackJson): ClipPass[] {
  if (!track.clips) return [];
  const out: ClipPass[] = [];
  track.clips.forEach((clip, ci) => {
    let base: number;
    if (clip.chop) {
      const chop = chopById(song, clip.chop);
      if (!chop) return;
      base = chop.length_beats;
    } else base = clip.length_beats ?? 0;
    const length = passBeats(clip, base);
    if (length <= 0) return;
    const start = clipStart0(clip);
    const repeat = clip.repeat ?? 1;
    for (let r = 0; r < repeat; r++) {
      const o = clip.instances?.[String(r + 1)];
      out.push({
        clipIndex: ci,
        instance: r + 1,
        start0: start + r * length,
        end0: start + (r + 1) * length,
        length,
        materialStart: clip.trim_start_beats ?? 0,
        stretch: clip.stretch ?? 1,
        gainDb: (clip.gain_db ?? 0) + (o?.gain_db ?? 0),
        pitch: o?.pitch_semitones ?? 0,
        reverse: !!clip.reverse !== !!o?.reverse,
        mute: !!o?.mute,
        fadeInMs: clip.fade_in_ms ?? null,
        fadeOutMs: clip.fade_out_ms ?? null,
        chop: clip.chop ?? null,
        sample: clip.sample ?? null,
      });
    }
  });
  return out;
}

// ------------------------------------------------------------------ notes

export interface NoteView {
  beat: number; // 1-indexed
  pitch: number;
  length: number;
  velocity: number;
  /** index into track.notes, or null when it comes from a phrase/MIDI */
  index: number | null;
  phrase: string | null;
  /** index inside phrases[].notes when phrase != null */
  phraseIndex: number | null;
}

/** Inline notes + expanded phrases (Song.track_notes), with their origin. */
export function trackNotes(song: SongJson, track: TrackJson): NoteView[] {
  const out: NoteView[] = (track.notes ?? []).map((n, i) => ({
    beat: n.beat,
    pitch: n.pitch,
    length: n.length_beats,
    velocity: n.velocity ?? 100,
    index: i,
    phrase: null,
    phraseIndex: null,
  }));
  const phrases = new Map((song.phrases ?? []).map((p) => [p.id, p]));
  const { at, until } = trackWindow(song, track);
  const endBeat = (until - 1) * BEATS_PER_BAR + 1;
  for (const pl of track.phrases ?? []) {
    const ph = phrases.get(pl.phrase);
    if (!ph) continue;
    const start = pl.at_bar ?? at;
    const span = ph.length_bars * BEATS_PER_BAR;
    const first = (start - 1) * BEATS_PER_BAR + 1;
    const passes = pl.repeat ?? Math.max(0, Math.ceil((endBeat - first) / span));
    for (let k = 0; k < passes; k++) {
      const origin = first + k * span;
      ph.notes.forEach((n, ni) => {
        const beat = origin + n.beat - 1;
        if (beat >= endBeat - 1e-9) return;
        const pitch = n.pitch + (pl.transpose_semitones ?? 0);
        if (pitch < 0 || pitch > 127) return;
        out.push({ beat, pitch, length: n.length_beats, velocity: n.velocity ?? 100, index: null, phrase: ph.id, phraseIndex: ni });
      });
    }
  }
  return out.sort((a, b) => a.beat - b.beat || a.pitch - b.pitch);
}

// ------------------------------------------------------------------ automation

export function automationFor(song: SongJson, target: string): AutomationJson | undefined {
  return song.automation?.find((a) => a.target === target);
}

/** Value of an automation at a bar position (lin/exp/step, holds outside). */
export function automationValue(a: AutomationJson, bar: number): number {
  const pts = a.points;
  if (!pts.length) return 0;
  let i = -1;
  for (let k = 0; k < pts.length; k++) if (pts[k][0] <= bar) i = k;
  const left = Math.max(0, Math.min(i, pts.length - 1));
  if (i < 0) return pts[0][1];
  if ((a.curve ?? "lin") === "step") return pts[left][1];
  const right = Math.min(left + 1, pts.length - 1);
  const span = pts[right][0] - pts[left][0];
  const amount = span > 0 ? Math.min(1, Math.max(0, (bar - pts[left][0]) / span)) : 1;
  const [va, vb] = [pts[left][1], pts[right][1]];
  if (a.curve === "exp" && va !== 0 && vb / va > 0) return va * Math.exp(Math.log(vb / va) * amount);
  return va + (vb - va) * amount;
}

export function trackAutomations(song: SongJson, trackId: string): AutomationJson[] {
  return (song.automation ?? []).filter((a) => a.target.startsWith(`tracks.${trackId}.`));
}

// ------------------------------------------------------------------ fingerprints

/** JSON with sorted keys (dicts) so equal songs give equal strings. */
export function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o)
      .filter((k) => o[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable(o[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(v ?? null);
}

/** Tracks a track's wet render depends on: itself + its sidechain keys. */
export function withKeys(song: SongJson, trackId: string): Set<string> {
  const byId = new Map((song.tracks ?? []).map((t) => [t.id, t]));
  const sel = new Set([trackId]);
  const stack = [trackId];
  while (stack.length) {
    const t = byId.get(stack.pop()!);
    for (const fx of t?.fx ?? []) {
      const key = fx.type === "sidechain" ? String(fx.key_track) : null;
      if (key && byId.has(key) && !sel.has(key)) {
        sel.add(key);
        stack.push(key);
      }
    }
  }
  return sel;
}

/** What a track's WET render depends on (mirror of editor/pieces.py wet_song):
 *  equal fingerprints = the wet stem still matches the song. */
export function wetFingerprint(song: SongJson, trackId: string): string {
  const sel = withKeys(song, trackId);
  const targets = new Set((song.automation ?? []).map((a) => a.target));
  const tracks = (song.tracks ?? [])
    .filter((t) => sel.has(t.id))
    .map((t) => {
      if (t.id !== trackId) return t;
      const n: TrackJson = { ...t, mute: false };
      if (!targets.has(`tracks.${t.id}.gain_db`)) n.gain_db = 0;
      if (!targets.has(`tracks.${t.id}.pan`)) n.pan = 0;
      return n;
    });
  const automation = (song.automation ?? []).filter((a) => [...sel].some((s) => a.target.startsWith(`tracks.${s}.`)));
  return stable({ meta: song.meta, chops: song.chops ?? [], phrases: song.phrases ?? [], sources: song.sources ?? [], tracks, automation });
}

/** What an instrument track's dry stem depends on. */
export function instrumentFingerprint(song: SongJson, track: TrackJson): string {
  return stable({ meta: song.meta, phrases: song.phrases ?? [], t: { ...track, fx: [], gain_db: 0, pan: 0, mute: false } });
}

export function trackById(song: SongJson, id: string): TrackJson | undefined {
  return song.tracks?.find((t) => t.id === id);
}

// ------------------------------------------------------------------ names

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function noteName(pitch: number): string {
  return `${NOTE_NAMES[((pitch % 12) + 12) % 12]}${Math.floor(pitch / 12) - 1}`;
}

/** Pitch classes of the song's key (meta.key like "F#m" / "Bb"). */
export function scalePitchClasses(key: string): Set<number> {
  const m = /^([A-G])([#b]?)(m?)$/.exec(key.trim());
  if (!m) return new Set();
  const base = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[m[1] as "C"];
  const tonic = (base + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) + 12) % 12;
  const steps = m[3] ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
  return new Set(steps.map((s) => (tonic + s) % 12));
}

export function tonicOf(key: string): number | null {
  const m = /^([A-G])([#b]?)/.exec(key.trim());
  if (!m) return null;
  const base = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[m[1] as "C"];
  return (base + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) + 12) % 12;
}
