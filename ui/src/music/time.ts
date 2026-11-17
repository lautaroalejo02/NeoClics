// Musical-time math. Positions in the UI are shown as bars.beats
// (1-indexed, like the contracts); internally everything is seconds.
// v0 is 4/4 only (contracts.md, BEATS_PER_BAR = 4).

export const BEATS_PER_BAR = 4;

export function beatsToSeconds(beats: number, bpm: number): number {
  return (beats * 60) / bpm;
}

export function secondsToBeats(seconds: number, bpm: number): number {
  return (seconds * bpm) / 60;
}

/** Seconds → 1-indexed {bar, beat} (beat may be fractional). */
export function secondsToBarBeat(
  seconds: number,
  bpm: number,
  beatsPerBar: number = BEATS_PER_BAR,
): { bar: number; beat: number; totalBeats: number } {
  const totalBeats = Math.max(0, secondsToBeats(seconds, bpm));
  const bar = Math.floor(totalBeats / beatsPerBar) + 1;
  const beat = (totalBeats % beatsPerBar) + 1;
  return { bar, beat, totalBeats };
}

/** 1-indexed bar/beat → seconds. Accepts fractional beats. */
export function barBeatToSeconds(
  bar: number,
  beat: number,
  bpm: number,
  beatsPerBar: number = BEATS_PER_BAR,
): number {
  return beatsToSeconds((bar - 1) * beatsPerBar + (beat - 1), bpm);
}

/** "3.2" style position (compás.beat). */
export function formatBarBeat(
  seconds: number,
  bpm: number,
  beatsPerBar: number = BEATS_PER_BAR,
): string {
  const { bar, beat } = secondsToBarBeat(seconds, bpm, beatsPerBar);
  return `${bar}.${Math.floor(beat)}`;
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  const whole = Math.floor(rem);
  const tenth = Math.floor((rem - whole) * 10);
  return `${m}:${String(whole).padStart(2, "0")}.${tenth}`;
}

/** Snap a beat position to the nearest bar boundary (loop points). */
export function snapBeatToBar(
  totalBeats: number,
  beatsPerBar: number = BEATS_PER_BAR,
): number {
  return Math.round(totalBeats / beatsPerBar) * beatsPerBar;
}

/** Snap seconds to the nearest bar boundary, returning seconds. */
export function snapSecondsToBar(
  seconds: number,
  bpm: number,
  beatsPerBar: number = BEATS_PER_BAR,
): number {
  return beatsToSeconds(
    snapBeatToBar(secondsToBeats(seconds, bpm), beatsPerBar),
    bpm,
  );
}

/**
 * Map a playhead position from version A to version B for gapless A/B.
 * Positions are MUSICAL: we map beats, not seconds, so a version rendered
 * at a different bpm still lands on the same musical point. Versions of
 * the same length and bpm map 1:1. Result is clamped into B's duration.
 */
export function mapPositionAB(
  secondsInA: number,
  a: { bpm: number; durationS: number },
  b: { bpm: number; durationS: number },
): number {
  if (a.bpm === b.bpm && a.durationS === b.durationS) {
    return Math.min(Math.max(0, secondsInA), b.durationS);
  }
  const beats = secondsToBeats(secondsInA, a.bpm);
  const out = beatsToSeconds(beats, b.bpm);
  return Math.min(Math.max(0, out), b.durationS);
}

/** Loop region in seconds ↔ normalized for storage/UI. */
export interface LoopRegion {
  start: number;
  end: number;
}

export function clampLoop(
  region: LoopRegion,
  duration: number,
  minBeats: number,
  bpm: number,
): LoopRegion {
  const minLen = beatsToSeconds(minBeats, bpm);
  let { start, end } = region;
  start = Math.min(Math.max(0, start), duration);
  end = Math.min(Math.max(0, end), duration);
  if (end - start < minLen) end = Math.min(duration, start + minLen);
  if (end - start < minLen) start = Math.max(0, end - minLen);
  return { start, end };
}
