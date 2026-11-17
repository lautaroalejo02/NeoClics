// Musical-time math. Pure functions, unit-tested (test/time.test.ts).
//
// Conventions (docs/contracts.md): bars and beats are 1-indexed, v0 is 4/4
// only (BEATS_PER_BAR = 4). A render covers `range_bars = [start, end)`, so
// second 0 of a render's audio is bar `start`, beat 1 — every conversion
// takes that first bar into account.

export const BEATS_PER_BAR = 4;

/** Timing of one rendered version. */
export interface Timing {
  bpm: number;
  /** First bar of the render (range_bars[0]); audio t=0 is this bar's beat 1. */
  firstBar: number;
  /** Render length in seconds. */
  durationS: number;
}

export function beatsToSeconds(beats: number, bpm: number): number {
  return (beats * 60) / bpm;
}

export function secondsToBeats(seconds: number, bpm: number): number {
  return (seconds * bpm) / 60;
}

export function barSeconds(bpm: number): number {
  return beatsToSeconds(BEATS_PER_BAR, bpm);
}

/** Seconds into the render → 1-indexed absolute bar + 1-indexed (fractional) beat. */
export function secondsToBarBeat(seconds: number, t: Pick<Timing, "bpm" | "firstBar">) {
  const rel = Math.max(0, secondsToBeats(seconds, t.bpm));
  // tiny epsilon so 1.9999999 beats (float noise) reads as beat 3, not 2
  const beats = rel + 1e-9;
  const barIdx = Math.floor(beats / BEATS_PER_BAR);
  const beat = beats - barIdx * BEATS_PER_BAR + 1;
  return { bar: t.firstBar + barIdx, beat, relBeats: rel };
}

/** 1-indexed absolute bar/beat → seconds into the render. Fractional beats ok. */
export function barBeatToSeconds(bar: number, beat: number, t: Pick<Timing, "bpm" | "firstBar">): number {
  return beatsToSeconds((bar - t.firstBar) * BEATS_PER_BAR + (beat - 1), t.bpm);
}

/** "5.3" — compás.tiempo (both 1-indexed). */
export function formatBarBeat(seconds: number, t: Pick<Timing, "bpm" | "firstBar">): string {
  const { bar, beat } = secondsToBarBeat(seconds, t);
  return `${bar}.${Math.floor(beat)}`;
}

/** "0:09.2" — minutes:seconds.tenths. */
export function formatClock(seconds: number): string {
  const s = Math.max(0, seconds);
  const tenthsTotal = Math.floor(s * 10 + 1e-6);
  const m = Math.floor(tenthsTotal / 600);
  const sec = Math.floor((tenthsTotal % 600) / 10);
  const tenth = tenthsTotal % 10;
  return `${m}:${String(sec).padStart(2, "0")}.${tenth}`;
}

/** Snap a beat count to the nearest multiple of `gridBeats`. */
export function snapBeats(beats: number, gridBeats: number): number {
  return Math.round(beats / gridBeats) * gridBeats;
}

/** Total beats in a render. */
export function totalBeats(t: Timing): number {
  return secondsToBeats(t.durationS, t.bpm);
}

/** Loop region in render-relative BEATS (0 = render start). Musical units
 *  keep a loop meaningful across versions with different bpm. */
export interface LoopBeats {
  startBeat: number;
  endBeat: number;
}

/**
 * Loop region from a drag between two points (beats, any order), snapped
 * to `gridBeats` (default one bar) and clamped to the render. The region is
 * always at least one grid unit long.
 */
export function loopFromDrag(
  aBeat: number,
  bBeat: number,
  maxBeats: number,
  gridBeats: number = BEATS_PER_BAR,
): LoopBeats {
  const lo = Math.min(aBeat, bBeat);
  const hi = Math.max(aBeat, bBeat);
  const maxGrid = Math.max(gridBeats, Math.floor(maxBeats / gridBeats + 1e-9) * gridBeats);
  let start = Math.floor(lo / gridBeats + 1e-9) * gridBeats;
  let end = Math.ceil(hi / gridBeats - 1e-9) * gridBeats;
  if (end - start < gridBeats) end = start + gridBeats;
  if (end > maxGrid) {
    end = maxGrid;
    start = Math.min(start, end - gridBeats);
  }
  start = Math.max(0, start);
  return { startBeat: start, endBeat: end };
}

/** Move one loop edge to `beat`, snapped, keeping >= one grid unit. */
export function moveLoopEdge(
  loop: LoopBeats,
  edge: "start" | "end",
  beat: number,
  maxBeats: number,
  gridBeats: number = BEATS_PER_BAR,
): LoopBeats {
  const maxGrid = Math.max(gridBeats, Math.floor(maxBeats / gridBeats + 1e-9) * gridBeats);
  const s = Math.min(Math.max(0, snapBeats(beat, gridBeats)), maxGrid);
  if (edge === "start") {
    return { startBeat: Math.min(s, loop.endBeat - gridBeats), endBeat: loop.endBeat };
  }
  return { startBeat: loop.startBeat, endBeat: Math.max(s, loop.startBeat + gridBeats) };
}

/** Shift a whole loop by `deltaBeats`, snapped, kept inside the render. */
export function shiftLoop(
  loop: LoopBeats,
  deltaBeats: number,
  maxBeats: number,
  gridBeats: number = BEATS_PER_BAR,
): LoopBeats {
  const len = loop.endBeat - loop.startBeat;
  const maxGrid = Math.max(gridBeats, Math.floor(maxBeats / gridBeats + 1e-9) * gridBeats);
  let start = snapBeats(loop.startBeat + deltaBeats, gridBeats);
  start = Math.min(Math.max(0, start), Math.max(0, maxGrid - len));
  return { startBeat: start, endBeat: start + len };
}

/** Default loop when the user turns looping on: the bar under the playhead
 *  plus the next one (2 bars), or the whole render if shorter. */
export function defaultLoopAt(posS: number, t: Timing): LoopBeats {
  const max = totalBeats(t);
  const beat = secondsToBeats(posS, t.bpm);
  const start = Math.floor(beat / BEATS_PER_BAR) * BEATS_PER_BAR;
  return loopFromDrag(start, start + 2 * BEATS_PER_BAR, max);
}

export function loopToSeconds(loop: LoopBeats, bpm: number): { start: number; end: number } {
  return { start: beatsToSeconds(loop.startBeat, bpm), end: beatsToSeconds(loop.endBeat, bpm) };
}

/**
 * Seek target for the arrow keys: the next (dir=+1) or previous (dir=-1)
 * grid line strictly past the current position. A position sitting a hair
 * after a line (< 60 ms) counts as ON it when moving backwards, so a
 * double tap of ← walks back through the bars while playing.
 */
export function stepSeek(posS: number, dir: 1 | -1, gridBeats: number, t: Timing): number {
  const beat = secondsToBeats(posS, t.bpm);
  const tol = secondsToBeats(0.06, t.bpm);
  let target: number;
  if (dir > 0) {
    target = (Math.floor(beat / gridBeats + 1e-6) + 1) * gridBeats;
  } else {
    const line = Math.floor(beat / gridBeats + 1e-6) * gridBeats;
    target = beat - line > tol ? line : line - gridBeats;
  }
  const s = beatsToSeconds(target, t.bpm);
  return Math.min(Math.max(0, s), t.durationS);
}

/**
 * Map a playhead position from version A to version B for A/B, keeping the
 * same MUSICAL point: absolute song beats, not seconds, so a version
 * rendered at another bpm or from another first bar lands on the same bar
 * and beat. Same timing maps 1:1 (exact, no float drift). Clamped to B.
 */
export function mapPositionAB(secondsInA: number, a: Timing, b: Timing): number {
  if (a.bpm === b.bpm && a.firstBar === b.firstBar) {
    return Math.min(Math.max(0, secondsInA), b.durationS);
  }
  const absBeat = (a.firstBar - 1) * BEATS_PER_BAR + secondsToBeats(secondsInA, a.bpm);
  const rel = absBeat - (b.firstBar - 1) * BEATS_PER_BAR;
  return Math.min(Math.max(0, beatsToSeconds(rel, b.bpm)), b.durationS);
}

/**
 * Position of a looping playback after `elapsed` seconds that started at
 * `offset`. Mirrors AudioBufferSourceNode semantics: play linearly from
 * the offset, and once the playhead reaches loop end jump back to loop
 * start (a start before the loop plays into it). Without a loop the
 * position clamps at `duration`.
 */
export function playheadAt(
  offset: number,
  elapsed: number,
  loop: { start: number; end: number } | null,
  duration: number,
): number {
  const raw = offset + Math.max(0, elapsed);
  if (loop && loop.end > loop.start && offset < loop.end) {
    if (raw < loop.end) return raw;
    const len = loop.end - loop.start;
    return loop.start + ((raw - loop.end) % len);
  }
  return Math.min(raw, duration);
}

/** Bar lines (render-relative seconds) for a ruler, with labels. */
export function barLines(t: Timing): { s: number; bar: number }[] {
  const out: { s: number; bar: number }[] = [];
  const bs = barSeconds(t.bpm);
  const n = Math.floor(t.durationS / bs + 1e-6);
  for (let i = 0; i <= n; i++) out.push({ s: i * bs, bar: t.firstBar + i });
  return out;
}

/** dB ↔ linear. */
export function dbToGain(db: number): number {
  return 10 ** (db / 20);
}
export function gainToDb(g: number): number {
  return 20 * Math.log10(Math.max(1e-12, g));
}
