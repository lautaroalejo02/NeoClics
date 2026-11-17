// User intents -> song.edit ops (+ a Spanish label for undo/toasts). Pure.

import { hitOps, placeOps, placed, round6, splitClipOps, tidyClip, type Op } from "./ops";
import type { HitSel, NoteSel, Selection } from "./selection";
import { laneName } from "./selection";
import {
  BEATS_PER_BAR,
  clipBaseLength,
  clipStart0,
  noteName,
  passBeats,
  patternHits,
  trackById,
  trackNotes,
  type ClipJson,
  type NoteJson,
  type SongJson,
} from "./song";

export interface Edit {
  ops: Op[];
  label: string;
  track?: string;
  select?: Selection;
}

const clipPath = (t: string, i: number) => `tracks.${t}.clips[${i}]`;

// ------------------------------------------------------------------ clips

export function moveClip(song: SongJson, track: string, ci: number, dBeats: number): Edit | null {
  const clip = trackById(song, track)?.clips?.[ci];
  if (!clip || Math.abs(dBeats) < 1e-9) return null;
  const to = Math.max(0, clipStart0(clip) + dBeats);
  const ops = placeOps(clipPath(track, ci), clip, to);
  if (!ops.length) return null;
  const bar = Math.floor(to / BEATS_PER_BAR) + 1;
  return { ops, label: `mover el clip al compás ${bar}`, track };
}

/** Right edge: whole passes when the clip repeats, else the pass length (trim). */
export function resizeClipRight(song: SongJson, track: string, ci: number, dBeats: number, stretchMode: boolean, snapStep: number): Edit | null {
  const clip = trackById(song, track)?.clips?.[ci];
  if (!clip) return null;
  const base = clipBaseLength(song, clip);
  const len = passBeats(clip, base);
  const repeat = clip.repeat ?? 1;
  const p = clipPath(track, ci);
  if (stretchMode) {
    const total = len * repeat + dBeats;
    const newLen = Math.max(snapStep || 0.25, total / repeat);
    const ratio = newLen / len;
    const stretch = Math.min(4, Math.max(0.25, (clip.stretch ?? 1) * ratio));
    if (Math.abs(stretch - (clip.stretch ?? 1)) < 1e-6) return null;
    const ops: Op[] = [
      { op: "set", path: `${p}.stretch`, value: round6(stretch), expect: clip.stretch ?? 1 },
      { op: "set", path: `${p}.length_beats`, value: round6(len * (stretch / (clip.stretch ?? 1))), expect: clip.length_beats ?? null },
    ];
    return { ops, label: `estirar el clip ×${round6(stretch)}`, track };
  }
  if (repeat > 1) {
    const n = Math.max(1, Math.round((len * repeat + dBeats) / len));
    if (n === repeat) return null;
    return { ops: [{ op: "set", path: `${p}.repeat`, value: n, expect: repeat }], label: `repetir el clip ${n} veces`, track };
  }
  const newLen = Math.max(snapStep || 0.25, len + dBeats);
  if (Math.abs(newLen - len) < 1e-9) return null;
  return { ops: [{ op: "set", path: `${p}.length_beats`, value: round6(newLen), expect: clip.length_beats ?? null }], label: newLen < len ? "recortar el final del clip" : "alargar el clip", track };
}

/** Left edge: trim the start (keeps the content where it was) or remove passes. */
export function resizeClipLeft(song: SongJson, track: string, ci: number, dBeats: number, snapStep: number): Edit | null {
  const clip = trackById(song, track)?.clips?.[ci];
  if (!clip) return null;
  const base = clipBaseLength(song, clip);
  const len = passBeats(clip, base);
  const repeat = clip.repeat ?? 1;
  const p = clipPath(track, ci);
  const start = clipStart0(clip);
  if (repeat > 1) {
    const k = Math.round(dBeats / len);
    const n = Math.max(1, Math.min(repeat + Math.max(0, -k), repeat - k));
    if (n === repeat) return null;
    const moved = start + (repeat - n) * len;
    return { ops: [...placeOps(p, clip, Math.max(0, moved)), { op: "set", path: `${p}.repeat`, value: n, expect: repeat }], label: `repetir el clip ${n} veces`, track };
  }
  const d = Math.min(len - (snapStep || 0.25), dBeats);
  if (Math.abs(d) < 1e-9 || start + d < 0) return null;
  const stretch = clip.stretch ?? 1;
  const ops: Op[] = [
    ...placeOps(p, clip, start + d),
    { op: "set", path: `${p}.trim_start_beats`, value: round6((clip.trim_start_beats ?? 0) + d / stretch), expect: clip.trim_start_beats ?? null },
    { op: "set", path: `${p}.length_beats`, value: round6(len - d), expect: clip.length_beats ?? null },
  ];
  return { ops, label: d > 0 ? "recortar el inicio del clip" : "abrir el inicio del clip", track };
}

export function splitAt(song: SongJson, track: string, ci: number, beat0: number): Edit | null {
  const ops = splitClipOps(song, track, ci, beat0);
  if (!ops) return null;
  return { ops, label: "cortar el clip", track };
}

export function duplicateClips(song: SongJson, track: string, cis: number[]): Edit | null {
  const t = trackById(song, track);
  if (!t?.clips) return null;
  const ops: Op[] = [];
  const end = song.meta.length_bars * BEATS_PER_BAR;
  for (const ci of cis) {
    const c = t.clips[ci];
    if (!c) continue;
    const len = passBeats(c, clipBaseLength(song, c)) * (c.repeat ?? 1);
    const to = clipStart0(c) + len;
    if (to + len > end + 1e-9) continue;
    ops.push({ op: "insert", path: `tracks.${track}.clips`, value: tidyClip(placed(c, to)) });
  }
  return ops.length ? { ops, label: cis.length > 1 ? "duplicar los clips" : "duplicar el clip", track } : null;
}

export function deleteClips(song: SongJson, track: string, items: { clip: number; instance: number | null }[]): Edit | null {
  const t = trackById(song, track);
  if (!t?.clips) return null;
  const ops: Op[] = [];
  const whole = [...new Set(items.filter((i) => i.instance == null || (t.clips![i.clip]?.repeat ?? 1) === 1).map((i) => i.clip))].sort((a, b) => b - a);
  const passes = items.filter((i) => i.instance != null && (t.clips![i.clip]?.repeat ?? 1) > 1 && !whole.includes(i.clip));
  for (const pi of passes) {
    const c = t.clips[pi.clip];
    const cur = c.instances?.[String(pi.instance)] ?? null;
    ops.push({ op: "set", path: `${clipPath(track, pi.clip)}.instances.${pi.instance}`, value: { ...(cur ?? {}), mute: true }, expect: cur });
  }
  for (const ci of whole) ops.push({ op: "remove", path: clipPath(track, ci), expect: t.clips[ci] });
  if (!ops.length) return null;
  const label = whole.length && !passes.length ? (whole.length > 1 ? "borrar los clips" : "borrar el clip") : passes.length === 1 ? `silenciar la repetición ${passes[0].instance}` : "silenciar repeticiones";
  return { ops, label, track, select: { kind: "none" } };
}

export function togglePassMute(song: SongJson, track: string, items: { clip: number; instance: number | null }[]): Edit | null {
  const t = trackById(song, track);
  if (!t?.clips) return null;
  const ops: Op[] = [];
  let muting = true;
  for (const it of items) {
    const c = t.clips[it.clip];
    if (!c) continue;
    const insts = it.instance ? [it.instance] : Array.from({ length: c.repeat ?? 1 }, (_, i) => i + 1);
    for (const n of insts) {
      const cur = c.instances?.[String(n)] ?? null;
      const mute = !cur?.mute;
      muting = mute;
      const next = { ...(cur ?? {}), mute };
      if (!mute) delete (next as { mute?: boolean }).mute;
      const path = `${clipPath(track, it.clip)}.instances.${n}`;
      if (Object.keys(next).length === 0) ops.push({ op: "unset", path, expect: cur });
      else ops.push({ op: "set", path, value: next, expect: cur });
    }
  }
  return ops.length ? { ops, label: muting ? "silenciar" : "volver a hacer sonar", track } : null;
}

export function setPass(song: SongJson, track: string, ci: number, instance: number, patch: Record<string, unknown>, label: string): Edit | null {
  const c = trackById(song, track)?.clips?.[ci];
  if (!c) return null;
  const cur = c.instances?.[String(instance)] ?? null;
  const next: Record<string, unknown> = { ...(cur ?? {}), ...patch };
  for (const [k, v] of Object.entries(next)) if (v === false || v === 0 || v == null) delete next[k];
  const path = `${clipPath(track, ci)}.instances.${instance}`;
  const op: Op = Object.keys(next).length ? { op: "set", path, value: next, expect: cur } : { op: "unset", path, expect: cur };
  return { ops: [op], label, track };
}

export function setClip(song: SongJson, track: string, ci: number, field: keyof ClipJson, value: unknown, label: string): Edit | null {
  const c = trackById(song, track)?.clips?.[ci];
  if (!c) return null;
  const path = `${clipPath(track, ci)}.${String(field)}`;
  const cur = (c as unknown as Record<string, unknown>)[field as string];
  if (value == null) return { ops: [{ op: "unset", path, expect: cur ?? null }], label, track };
  return { ops: [{ op: "set", path, value, expect: cur ?? null }], label, track };
}

// ------------------------------------------------------------------ hits

export function hitSel(h: { lane: string; bar: number; step: number | null; index: number | null; beat0: number }): HitSel {
  return { lane: h.lane, bar: h.bar, step: h.step, index: h.index, beat0: h.beat0 };
}

/** Add a grid hit, or a free hit at an exact beat (snap off / Alt). */
export function addHit(song: SongJson, track: string, lane: string, beat0: number, grid: { bar: number; step: number } | null, velocity = 1): Edit | null {
  if (grid) {
    const ops = hitOps(song, track, lane, grid.bar, grid.step, velocity);
    return ops.length ? { ops, label: `agregar un golpe de ${laneName(lane)}`, track } : null;
  }
  return {
    ops: [{ op: "insert", path: `tracks.${track}.pattern.hits`, value: { beat: round6(beat0 + 1), lane, ...(velocity !== 1 ? { velocity } : {}) } }],
    label: `agregar un golpe suelto de ${laneName(lane)}`,
    track,
  };
}

function removeHitOps(track: string, h: HitSel, cur: SongJson): Op[] {
  if (h.index != null) {
    const hit = trackById(cur, track)?.pattern?.hits?.[h.index];
    return [{ op: "remove", path: `tracks.${track}.pattern.hits[${h.index}]`, expect: hit }];
  }
  return hitOps(cur, track, h.lane, h.bar, h.step ?? 0, null);
}

/** Apply ops sequentially on a scratch copy (ops built against the evolving song). */
function chain(song: SongJson, builders: ((s: SongJson) => Op[])[]): Op[] {
  // local import to avoid a cycle at module load
  let cur = song;
  const out: Op[] = [];
  for (const b of builders) {
    const ops = b(cur);
    if (!ops.length) continue;
    cur = applyLocal(cur, ops);
    out.push(...ops);
  }
  return out;
}

import { applyOps } from "./ops";
function applyLocal(song: SongJson, ops: Op[]): SongJson {
  return applyOps(song, ops).song;
}

export function deleteHits(song: SongJson, track: string, items: HitSel[]): Edit | null {
  // free hits by descending index so earlier removals don't shift later ones
  const sorted = [...items].sort((a, b) => (b.index ?? -1) - (a.index ?? -1));
  const ops = chain(song, sorted.map((h) => (cur: SongJson) => removeHitOps(track, h, cur)));
  if (!ops.length) return null;
  const lanes = [...new Set(items.map((h) => laneName(h.lane)))].join(" y ");
  return { ops, label: items.length === 1 ? `sacar un golpe de ${lanes}` : `sacar ${items.length} golpes de ${lanes}`, track, select: { kind: "none" } };
}

export function setHitVelocity(song: SongJson, track: string, items: HitSel[], velocity: number): Edit | null {
  const v = Math.min(1, Math.max(0.05, velocity));
  const ops = chain(
    song,
    items.map((h) => (cur: SongJson) => {
      if (h.index != null) {
        const hit = trackById(cur, track)?.pattern?.hits?.[h.index];
        return hit ? [{ op: "set", path: `tracks.${track}.pattern.hits[${h.index}].velocity`, value: round6(v), expect: hit.velocity ?? null }] : [];
      }
      // grid velocities are written as step chars (x / o / 1-9)
      return hitOps(cur, track, h.lane, h.bar, h.step ?? 0, v >= 0.995 ? 1 : v);
    }),
  );
  return ops.length ? { ops, label: "cambiar la fuerza del golpe", track } : null;
}

/** Move hits by dBeats (grid hits stay on the grid; free hits move freely). */
export function moveHits(song: SongJson, track: string, items: HitSel[], dBeats: number, free: boolean): Edit | null {
  const t = trackById(song, track);
  const p = t?.pattern;
  if (!p || Math.abs(dBeats) < 1e-9) return null;
  const spb = p.steps_per_bar ?? 16;
  const stepB = BEATS_PER_BAR / spb;
  const all = patternHits(song, t!);
  const vel = (h: HitSel) => all.find((x) => x.lane === h.lane && Math.abs(x.beat0 - h.beat0) < 1e-6)?.velocity ?? 1;
  const end = song.meta.length_bars * BEATS_PER_BAR;
  const builders: ((s: SongJson) => Op[])[] = [];
  const next: HitSel[] = [];
  for (const h of [...items].sort((a, b) => (b.index ?? -1) - (a.index ?? -1))) {
    const to = Math.min(end - 1e-6, Math.max(0, h.beat0 + dBeats));
    if (h.index != null) {
      builders.push((cur) => {
        const hit = trackById(cur, track)?.pattern?.hits?.[h.index!];
        return hit ? [{ op: "set", path: `tracks.${track}.pattern.hits[${h.index}].beat`, value: round6(to + 1), expect: hit.beat }] : [];
      });
      next.push({ ...h, beat0: to, bar: Math.floor(to / BEATS_PER_BAR) + 1 });
      continue;
    }
    const v = vel(h);
    builders.push((cur) => removeHitOps(track, h, cur));
    if (free) {
      builders.push(() => [{ op: "insert", path: `tracks.${track}.pattern.hits`, value: { beat: round6(to + 1), lane: h.lane, ...(v !== 1 ? { velocity: round6(v) } : {}) } }]);
    } else {
      const g = Math.round(to / stepB) * stepB;
      const bar = Math.floor(g / BEATS_PER_BAR + 1e-9) + 1;
      const step = Math.round((g - (bar - 1) * BEATS_PER_BAR) / stepB);
      builders.push((cur) => hitOps(cur, track, h.lane, bar, step, v));
      next.push({ lane: h.lane, bar, step, index: null, beat0: g });
    }
  }
  const ops = chain(song, builders);
  return ops.length ? { ops, label: items.length > 1 ? "mover los golpes" : "mover el golpe", track, select: next.length === items.length ? { kind: "hits", track, items: next } : { kind: "none" } } : null;
}

export function toggleHitMute(song: SongJson, track: string, items: HitSel[]): Edit | null {
  // "mute" a drum hit = remove it (the pattern keeps playing elsewhere)
  return deleteHits(song, track, items);
}

// ------------------------------------------------------------------ notes

export function addNote(song: SongJson, track: string, beat0: number, pitch: number, length: number): Edit | null {
  const t = trackById(song, track);
  if (!t || t.midi) return null;
  const value: NoteJson = { beat: round6(beat0 + 1), pitch, length_beats: round6(length) };
  return { ops: [{ op: "insert", path: `tracks.${track}.notes`, value }], label: `agregar un ${noteName(pitch)}`, track };
}

function notePath(track: string, n: NoteSel): string {
  return n.phrase ? `phrases.${n.phrase}.notes[${n.phraseIndex}]` : `tracks.${track}.notes[${n.index}]`;
}

function noteJson(song: SongJson, track: string, n: NoteSel): NoteJson | undefined {
  if (n.phrase) return song.phrases?.find((p) => p.id === n.phrase)?.notes[n.phraseIndex ?? 0];
  return trackById(song, track)?.notes?.[n.index ?? 0];
}

/** Move/transposes/resize notes (phrase notes edit the phrase itself). */
export function changeNotes(song: SongJson, track: string, items: NoteSel[], d: { beats?: number; pitch?: number; length?: number; velocity?: number }): Edit | null {
  const ops: Op[] = [];
  const seen = new Set<string>();
  for (const n of items) {
    const path = notePath(track, n);
    if (seen.has(path)) continue;
    seen.add(path);
    const cur = noteJson(song, track, n);
    if (!cur) continue;
    const next: NoteJson = { ...cur };
    if (d.beats) next.beat = round6(Math.max(1, cur.beat + d.beats));
    if (d.pitch) next.pitch = Math.min(127, Math.max(0, cur.pitch + d.pitch));
    if (d.length) next.length_beats = round6(Math.max(0.0625, cur.length_beats + d.length));
    if (d.velocity != null) next.velocity = Math.min(127, Math.max(1, Math.round(d.velocity)));
    ops.push({ op: "set", path, value: next, expect: cur });
  }
  if (!ops.length) return null;
  const what = d.pitch ? "transponer" : d.length ? "cambiar el largo de" : d.velocity != null ? "cambiar la fuerza de" : "mover";
  return { ops, label: `${what} ${items.length > 1 ? "las notas" : "la nota"}`, track };
}

export function deleteNotes(song: SongJson, track: string, items: NoteSel[]): Edit | null {
  const byList = new Map<string, number[]>();
  for (const n of items) {
    const list = n.phrase ? `phrases.${n.phrase}.notes` : `tracks.${track}.notes`;
    const i = n.phrase ? (n.phraseIndex ?? 0) : (n.index ?? 0);
    byList.set(list, [...new Set([...(byList.get(list) ?? []), i])]);
  }
  const ops: Op[] = [];
  for (const [list, idx] of byList) {
    for (const i of idx.sort((a, b) => b - a)) {
      const cur = list.startsWith("phrases.") ? song.phrases?.find((p) => `phrases.${p.id}.notes` === list)?.notes[i] : trackById(song, track)?.notes?.[i];
      ops.push({ op: "remove", path: `${list}[${i}]`, expect: cur });
    }
  }
  return ops.length ? { ops, label: items.length > 1 ? `borrar ${items.length} notas` : "borrar la nota", track, select: { kind: "none" } } : null;
}

export function noteSelOf(n: { index: number | null; phrase: string | null; phraseIndex: number | null; beat: number; pitch: number }): NoteSel {
  return { index: n.index, phrase: n.phrase, phraseIndex: n.phraseIndex, beat: n.beat, pitch: n.pitch };
}

/** Notes of the track inside a beat/pitch box (marquee). */
export function notesIn(song: SongJson, track: string, b0: number, b1: number, p0: number, p1: number): NoteSel[] {
  const t = trackById(song, track);
  if (!t) return [];
  return trackNotes(song, t)
    .filter((n) => n.beat - 1 < b1 && n.beat - 1 + n.length > b0 && n.pitch >= p0 && n.pitch <= p1)
    .map(noteSelOf);
}

// ------------------------------------------------------------------ automation

export function addPoint(song: SongJson, target: string, bar: number, value: number): Edit {
  const a = song.automation?.find((x) => x.target === target);
  if (!a) {
    return { ops: [{ op: "insert", path: "automation", value: { target, curve: "lin", points: [[round6(bar), value], [round6(bar), value]] } }], label: "agregar automatización" };
  }
  const index = a.points.filter((p) => p[0] <= bar).length;
  return { ops: [{ op: "insert", path: `automation.${target}.points`, index, value: [round6(bar), round6(value)] }], label: "agregar un punto de automatización" };
}

export function movePoint(song: SongJson, target: string, index: number, bar: number, value: number): Edit | null {
  const a = song.automation?.find((x) => x.target === target);
  const cur = a?.points[index];
  if (!a || !cur) return null;
  const lo = index > 0 ? a.points[index - 1][0] : 1;
  const hi = index < a.points.length - 1 ? a.points[index + 1][0] : song.meta.length_bars + 1;
  const b = Math.min(hi, Math.max(lo, bar));
  return { ops: [{ op: "set", path: `automation.${target}.points[${index}]`, value: [round6(b), round6(value)], expect: cur }], label: "mover un punto de automatización" };
}

export function deletePoints(song: SongJson, target: string, items: number[]): Edit | null {
  const a = song.automation?.find((x) => x.target === target);
  if (!a) return null;
  if (a.points.length - items.length < 2) {
    return { ops: [{ op: "remove", path: `automation.${target}`, expect: a }], label: "sacar la automatización", select: { kind: "none" } };
  }
  const ops: Op[] = [...items].sort((x, y) => y - x).map((i) => ({ op: "remove", path: `automation.${target}.points[${i}]`, expect: a.points[i] }) as Op);
  return { ops, label: items.length > 1 ? "borrar puntos de automatización" : "borrar un punto de automatización", select: { kind: "none" } };
}

// ------------------------------------------------------------------ clipboard

export type Clipboard = { kind: "clips"; track: string; clips: ClipJson[]; origin: number } | { kind: "hits"; track: string; hits: { lane: string; dBeat: number; velocity: number }[]; origin: number } | { kind: "notes"; track: string; notes: NoteJson[]; origin: number };

export function copySelection(song: SongJson, sel: Selection): Clipboard | null {
  if (sel.kind === "clips") {
    const t = trackById(song, sel.track);
    const clips = [...new Set(sel.items.map((i) => i.clip))].map((i) => t?.clips?.[i]).filter((c): c is ClipJson => !!c);
    if (!clips.length) return null;
    return { kind: "clips", track: sel.track, clips, origin: Math.min(...clips.map(clipStart0)) };
  }
  if (sel.kind === "hits") {
    const t = trackById(song, sel.track);
    const all = t ? patternHits(song, t) : [];
    const origin = Math.floor(Math.min(...sel.items.map((h) => h.beat0)) / BEATS_PER_BAR) * BEATS_PER_BAR;
    return { kind: "hits", track: sel.track, origin, hits: sel.items.map((h) => ({ lane: h.lane, dBeat: h.beat0 - origin, velocity: all.find((x) => x.lane === h.lane && Math.abs(x.beat0 - h.beat0) < 1e-6)?.velocity ?? 1 })) };
  }
  if (sel.kind === "notes") {
    const notes = sel.items.map((n) => noteJson(song, sel.track, n)).filter((n): n is NoteJson => !!n);
    if (!notes.length) return null;
    return { kind: "notes", track: sel.track, notes, origin: Math.min(...notes.map((n) => n.beat - 1)) };
  }
  return null;
}

export function paste(song: SongJson, clip: Clipboard, track: string, at: number): Edit | null {
  const t = trackById(song, track) ?? trackById(song, clip.track);
  if (!t) return null;
  if (clip.kind === "clips" && t.clips) {
    const ops: Op[] = clip.clips.map((c) => ({ op: "insert", path: `tracks.${t.id}.clips`, value: tidyClip(placed(c, Math.max(0, at + clipStart0(c) - clip.origin))) }) as Op);
    return { ops, label: "pegar", track: t.id };
  }
  if (clip.kind === "hits" && t.pattern) {
    const spb = t.pattern.steps_per_bar ?? 16;
    const stepB = BEATS_PER_BAR / spb;
    const base = Math.floor(at / BEATS_PER_BAR) * BEATS_PER_BAR;
    const ops = chain(
      song,
      clip.hits
        .filter((h) => t.pattern!.lanes[h.lane])
        .map((h) => (cur: SongJson) => {
          const b = base + h.dBeat;
          const g = Math.round(b / stepB) * stepB;
          if (Math.abs(g - b) > 1e-6) return [{ op: "insert", path: `tracks.${t.id}.pattern.hits`, value: { beat: round6(b + 1), lane: h.lane, velocity: h.velocity } }];
          const bar = Math.floor(g / BEATS_PER_BAR + 1e-9) + 1;
          if (bar > song.meta.length_bars) return [];
          return hitOps(cur, t.id, h.lane, bar, Math.round((g - (bar - 1) * BEATS_PER_BAR) / stepB), h.velocity);
        }),
    );
    return ops.length ? { ops, label: "pegar los golpes", track: t.id } : null;
  }
  if (clip.kind === "notes" && t.instrument && !t.midi) {
    const ops: Op[] = clip.notes.map((n) => ({ op: "insert", path: `tracks.${t.id}.notes`, value: { ...n, beat: round6(at + (n.beat - 1 - clip.origin) + 1) } }) as Op);
    return { ops, label: "pegar las notas", track: t.id };
  }
  return null;
}

// ------------------------------------------------------------------ track

export function setTrackField(song: SongJson, track: string, field: "gain_db" | "pan" | "mute", value: number | boolean, label: string): Edit | null {
  const t = trackById(song, track);
  if (!t) return null;
  const cur = (t as unknown as Record<string, unknown>)[field] ?? null;
  return { ops: [{ op: "set", path: `tracks.${track}.${field}`, value, expect: cur }], label, track };
}

export function setFxParam(song: SongJson, track: string | null, fxIndex: number, param: string, value: unknown): Edit | null {
  const chain = track ? trackById(song, track)?.fx : song.master?.fx;
  const fx = chain?.[fxIndex];
  if (!fx) return null;
  const base = track ? `tracks.${track}.fx` : "master.fx";
  const path = fx.id ? `${base}.${fx.id}.${param}` : `${base}[${fxIndex}].${param}`;
  return { ops: [{ op: "set", path, value, expect: (fx as Record<string, unknown>)[param] ?? null }], label: `ajustar ${fx.type}`, track: track ?? undefined };
}
