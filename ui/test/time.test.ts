import { describe, expect, it } from "vitest";
import {
  barBeatToSeconds,
  barLines,
  defaultLoopAt,
  formatBarBeat,
  formatClock,
  loopFromDrag,
  loopToSeconds,
  mapPositionAB,
  moveLoopEdge,
  playheadAt,
  secondsToBarBeat,
  shiftLoop,
  stepSeek,
  type Timing,
} from "../src/music/time";

const T120: Timing = { bpm: 120, firstBar: 1, durationS: 16 }; // 8 bars of 2 s

describe("seconds <-> bars/beats", () => {
  it("is 1-indexed and 4/4", () => {
    expect(secondsToBarBeat(0, T120)).toMatchObject({ bar: 1, beat: 1 });
    expect(secondsToBarBeat(0.5, T120)).toMatchObject({ bar: 1, beat: 2 });
    expect(secondsToBarBeat(2, T120)).toMatchObject({ bar: 2, beat: 1 });
    expect(secondsToBarBeat(3.75, T120).bar).toBe(2);
    expect(secondsToBarBeat(3.75, T120).beat).toBeCloseTo(4.5);
  });

  it("offsets by the render's first bar (range_bars[0])", () => {
    const t = { bpm: 120, firstBar: 5 };
    expect(secondsToBarBeat(0, t).bar).toBe(5);
    expect(formatBarBeat(2.6, t)).toBe("6.2");
    expect(barBeatToSeconds(6, 2, t)).toBeCloseTo(2.5);
  });

  it("round-trips at odd tempos without float drift on boundaries", () => {
    const t = { bpm: 126.4, firstBar: 1 };
    for (let bar = 1; bar <= 32; bar++) {
      for (let beat = 1; beat <= 4; beat++) {
        const s = barBeatToSeconds(bar, beat, t);
        expect(formatBarBeat(s, t)).toBe(`${bar}.${beat}`);
      }
    }
  });

  it("formats clock time with tenths", () => {
    expect(formatClock(0)).toBe("0:00.0");
    expect(formatClock(9.27)).toBe("0:09.2");
    expect(formatClock(61.05)).toBe("1:01.0");
    expect(formatClock(-3)).toBe("0:00.0");
  });

  it("lists bar lines for the ruler", () => {
    const lines = barLines(T120);
    expect(lines).toHaveLength(9);
    expect(lines[0]).toEqual({ s: 0, bar: 1 });
    expect(lines[8]).toEqual({ s: 16, bar: 9 });
  });
});

describe("loop snapping", () => {
  const max = 32; // beats

  it("snaps a drag outward to whole bars, any direction", () => {
    expect(loopFromDrag(5, 9.5, max)).toEqual({ startBeat: 4, endBeat: 12 });
    expect(loopFromDrag(9.5, 5, max)).toEqual({ startBeat: 4, endBeat: 12 });
  });

  it("is at least one bar and stays inside the render", () => {
    expect(loopFromDrag(4.1, 4.2, max)).toEqual({ startBeat: 4, endBeat: 8 });
    expect(loopFromDrag(30, 40, max)).toEqual({ startBeat: 28, endBeat: 32 });
    expect(loopFromDrag(-3, 2, max)).toEqual({ startBeat: 0, endBeat: 4 });
  });

  it("moves an edge to the nearest bar and never collapses", () => {
    const l = { startBeat: 4, endBeat: 12 };
    expect(moveLoopEdge(l, "end", 17.5, max)).toEqual({ startBeat: 4, endBeat: 16 });
    expect(moveLoopEdge(l, "start", 13, max)).toEqual({ startBeat: 8, endBeat: 12 });
    expect(moveLoopEdge(l, "end", 1, max)).toEqual({ startBeat: 4, endBeat: 8 });
  });

  it("shifts a loop by whole bars and clamps", () => {
    const l = { startBeat: 4, endBeat: 12 };
    expect(shiftLoop(l, 5, max)).toEqual({ startBeat: 8, endBeat: 16 });
    expect(shiftLoop(l, 100, max)).toEqual({ startBeat: 24, endBeat: 32 });
    expect(shiftLoop(l, -100, max)).toEqual({ startBeat: 0, endBeat: 8 });
  });

  it("defaults to two bars from the bar under the playhead", () => {
    expect(defaultLoopAt(5.1, T120)).toEqual({ startBeat: 8, endBeat: 16 });
    expect(defaultLoopAt(15.9, T120)).toEqual({ startBeat: 28, endBeat: 32 }); // last bar: shortened, never past the end
    expect(loopToSeconds({ startBeat: 8, endBeat: 16 }, 120)).toEqual({ start: 4, end: 8 });
  });
});

describe("arrow-key seeking", () => {
  it("steps to the next/previous beat line", () => {
    expect(stepSeek(1.2, 1, 1, T120)).toBeCloseTo(1.5);
    expect(stepSeek(1.2, -1, 1, T120)).toBeCloseTo(1.0);
  });

  it("treats a position just past a line as on it when going back", () => {
    expect(stepSeek(2.03, -1, 4, T120)).toBeCloseTo(0); // 30 ms after bar 2 → bar 1
    expect(stepSeek(2.5, -1, 4, T120)).toBeCloseTo(2); // well inside bar 2 → its start
  });

  it("clamps to the render", () => {
    expect(stepSeek(15.9, 1, 4, T120)).toBe(16);
    expect(stepSeek(0.01, -1, 4, T120)).toBe(0);
  });
});

describe("A/B position mapping", () => {
  it("is the identity for the same timing", () => {
    const b = { ...T120, durationS: 16 };
    expect(mapPositionAB(7.123456789, T120, b)).toBe(7.123456789);
  });

  it("keeps the musical position across tempos", () => {
    const fast: Timing = { bpm: 126, firstBar: 1, durationS: 16 * (120 / 126) };
    const s = mapPositionAB(5, T120, fast); // beat 10 → bar 3 beat 3
    expect(formatBarBeat(s, fast)).toBe("3.3");
    expect(s).toBeCloseTo((10 * 60) / 126, 10);
  });

  it("maps across different rendered ranges by absolute bar", () => {
    const fromBar5: Timing = { bpm: 120, firstBar: 5, durationS: 8 };
    // bar 6 beat 1 in T120 is 10 s; in a render starting at bar 5 it is 2 s
    expect(mapPositionAB(10, T120, fromBar5)).toBeCloseTo(2);
    expect(mapPositionAB(0, T120, fromBar5)).toBe(0); // before its range: clamp
  });

  it("clamps into the shorter version", () => {
    expect(mapPositionAB(15, T120, { bpm: 120, firstBar: 1, durationS: 8 })).toBe(8);
  });
});

describe("playhead semantics (mirrors AudioBufferSourceNode)", () => {
  const loop = { start: 4, end: 8 };
  it("plays linearly without a loop and stops at the end", () => {
    expect(playheadAt(3, 2, null, 16)).toBe(5);
    expect(playheadAt(15, 5, null, 16)).toBe(16);
  });
  it("wraps at loop end back to loop start", () => {
    expect(playheadAt(6, 1, loop, 16)).toBe(7);
    expect(playheadAt(6, 2, loop, 16)).toBe(4);
    expect(playheadAt(6, 7, loop, 16)).toBeCloseTo(5);
  });
  it("plays into the loop from before it", () => {
    expect(playheadAt(1, 2, loop, 16)).toBe(3);
    expect(playheadAt(1, 8, loop, 16)).toBeCloseTo(5);
  });
});
