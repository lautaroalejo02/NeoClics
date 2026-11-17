// What is under the pointer: pure geometry over the same layout draw.ts uses.

import { RULER_H, valueToY } from "./draw";
import { BEATS_PER_BAR, automationFor, clipPasses, patternHits, trackById, trackNotes, type DrumHit, type NoteView, type SongJson } from "./song";
import { beatToX, xToBeat, type Row, type View } from "./timeline";

export type Target =
  | { type: "ruler"; beat: number }
  | { type: "clip"; track: string; clip: number; instance: number; edge: "left" | "right" | null; beat: number }
  | { type: "hit"; track: string; hit: DrumHit; beat: number }
  | { type: "cell"; track: string; lane: string; bar: number; step: number; beat: number }
  | { type: "note"; track: string; note: NoteView; edge: "right" | null; beat: number; pitch: number }
  | { type: "roll"; track: string; beat: number; pitch: number }
  | { type: "point"; track: string; target: string; index: number; beat: number }
  | { type: "auto"; track: string; target: string; beat: number; y: number; h: number }
  | { type: "row"; track: string; beat: number; lane: string | null }
  | { type: "none" };

const EDGE_PX = 7;

export function rowAt(rows: Row[], y: number): Row | null {
  return rows.find((r) => y >= r.y && y < r.y + r.h) ?? null;
}

export function pitchAt(row: Row, y: number): number {
  const rowH = row.contentH / (row.pitchHi - row.pitchLo + 1);
  return Math.max(0, Math.min(127, row.pitchHi - Math.floor((y - row.contentY) / rowH)));
}

/** `x`,`y` in canvas px (y includes the ruler); scrollY = vertical scroll. */
export function hitTest(song: SongJson, view: View, rows: Row[], x: number, y: number, scrollY: number): Target {
  const beat = xToBeat(x, view);
  if (y < RULER_H) return { type: "ruler", beat };
  const cy = y - RULER_H + scrollY;
  const row = rowAt(rows, cy);
  if (!row) return { type: "none" };
  const track = trackById(song, row.trackId);
  if (!track) return { type: "none" };
  // automation lanes
  for (const a of row.autos) {
    if (cy < a.y || cy >= a.y + a.h) continue;
    const au = automationFor(song, a.target);
    if (au) {
      for (let i = 0; i < au.points.length; i++) {
        const [bar, v] = au.points[i];
        const px = beatToX((bar - 1) * BEATS_PER_BAR, view);
        const py = valueToY(v, a.target, a.y, a.h);
        if (Math.hypot(px - x, py - cy) <= 8) return { type: "point", track: row.trackId, target: a.target, index: i, beat };
      }
    }
    return { type: "auto", track: row.trackId, target: a.target, beat, y: a.y, h: a.h };
  }
  if (cy >= row.contentY + row.contentH) return { type: "row", track: row.trackId, beat, lane: null };
  if (row.kind === "clips") {
    const passes = clipPasses(song, track);
    // topmost = last drawn
    for (let i = passes.length - 1; i >= 0; i--) {
      const p = passes[i];
      const x0 = beatToX(p.start0, view);
      const x1 = beatToX(p.end0, view);
      if (x < x0 - 1 || x > x1 + 1) continue;
      const clip = track.clips![p.clipIndex];
      const repeat = clip.repeat ?? 1;
      let edge: "left" | "right" | null = null;
      if (p.instance === 1 && x - x0 <= EDGE_PX && x1 - x0 > EDGE_PX * 3) edge = "left";
      if (p.instance === repeat && x1 - x <= EDGE_PX && x1 - x0 > EDGE_PX * 3) edge = "right";
      return { type: "clip", track: row.trackId, clip: p.clipIndex, instance: p.instance, edge, beat };
    }
    return { type: "row", track: row.trackId, beat, lane: null };
  }
  if (row.kind === "pattern") {
    const p = track.pattern!;
    const spb = p.steps_per_bar ?? 16;
    if (!row.expanded) return { type: "row", track: row.trackId, beat, lane: null };
    const lane = row.lanes.find((l) => cy >= l.y && cy < l.y + l.h);
    if (!lane) return { type: "row", track: row.trackId, beat, lane: null };
    const stepPx = (view.pxPerBeat * BEATS_PER_BAR) / spb;
    const hits = patternHits(song, track).filter((h) => h.lane === lane.name);
    let best: DrumHit | null = null;
    let bestD = Infinity;
    for (const h of hits) {
      const hx = beatToX(h.beat0, view);
      const w = h.origin === "free" ? 8 : Math.max(4, Math.min(stepPx - 2, 22));
      const inside = h.origin === "free" ? Math.abs(x - hx) <= w : x >= hx - 1 && x <= hx + w + 1;
      if (inside && Math.abs(x - hx) < bestD) {
        best = h;
        bestD = Math.abs(x - hx);
      }
    }
    if (best) return { type: "hit", track: row.trackId, hit: best, beat };
    const bar = Math.floor(beat / BEATS_PER_BAR) + 1;
    const step = Math.min(spb - 1, Math.max(0, Math.floor(((beat - (bar - 1) * BEATS_PER_BAR) * spb) / BEATS_PER_BAR)));
    return { type: "cell", track: row.trackId, lane: lane.name, bar, step, beat };
  }
  // instrument
  if (track.midi) return { type: "row", track: row.trackId, beat, lane: null };
  const pitch = pitchAt(row, cy);
  const notes = trackNotes(song, track);
  for (let i = notes.length - 1; i >= 0; i--) {
    const n = notes[i];
    if (n.pitch !== pitch) continue;
    const x0 = beatToX(n.beat - 1, view);
    const x1 = beatToX(n.beat - 1 + n.length, view);
    if (x >= x0 - 1 && x <= x1 + 1) {
      const edge = x1 - x <= Math.min(EDGE_PX, (x1 - x0) / 3) ? "right" : null;
      return { type: "note", track: row.trackId, note: n, edge, beat, pitch };
    }
  }
  return { type: "roll", track: row.trackId, beat, pitch };
}
