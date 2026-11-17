import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { applyChokes, patternHits, voiceKey, type SongJson } from "../src/editor/song";
import { PAD_KEYS, ROWS, capLabel, classifyKey, kitOrder, kitSlot } from "../src/pads/keymap";
import {
  calibrate,
  chokeTargets,
  heardCtxTime,
  heardUnwrapped,
  latencyVerdict,
  mergePass,
  placePress,
  placedBeats,
  processEvents,
  quantizeBeat,
  takeSpan,
  undoLastPass,
  type QuantizeGrid,
  type TakeJson,
} from "../src/pads/model";

const cases = JSON.parse(readFileSync(resolve(__dirname, "../../fixtures/pads/quantize-cases.json"), "utf8")).cases as {
  beat: number;
  grid: QuantizeGrid;
  strength: number;
  swing: number;
  wrap?: number;
  out: number;
}[];

describe("key map (physical codes)", () => {
  it("has 24 pads on three letter rows, bottom row first", () => {
    expect(PAD_KEYS).toHaveLength(24);
    expect(PAD_KEYS.slice(0, 8)).toEqual(ROWS[2]);
    expect(new Set(PAD_KEYS).size).toBe(24);
  });
  it("maps by code, not by the printed letter (AZERTY: the A key reports KeyQ)", () => {
    expect(classifyKey({ code: "KeyQ" })).toEqual({ kind: "pad", code: "KeyQ", accent: false });
    expect(classifyKey({ code: "KeyZ", shiftKey: true })).toEqual({ kind: "pad", code: "KeyZ", accent: true });
    const azerty = new Map([["KeyQ", "a"], ["KeyZ", "w"]]);
    expect(capLabel("KeyQ", azerty)).toBe("A");
    expect(capLabel("KeyZ", azerty)).toBe("W");
    expect(capLabel("Comma", null)).toBe(",");
  });
  it("never captures Ctrl/Cmd/Alt combos and maps the transport keys", () => {
    expect(classifyKey({ code: "KeyZ", ctrlKey: true })).toBeNull();
    expect(classifyKey({ code: "KeyZ", metaKey: true })).toBeNull();
    expect(classifyKey({ code: "Digit3" })).toEqual({ kind: "bank", index: 2 });
    expect(classifyKey({ code: "Space" })?.kind).toBe("transport");
    expect(classifyKey({ code: "Enter" })?.kind).toBe("record");
    expect(classifyKey({ code: "Backspace" })?.kind).toBe("undoPass");
    expect(classifyKey({ code: "Escape" })?.kind).toBe("exit");
    expect(classifyKey({ code: "KeyL" })).toBeNull(); // L stays the editor's loop
    expect(classifyKey({ code: "KeyP" })).toBeNull();
  });
  it("lays a kit out like a drum machine", () => {
    const used = new Set<number>();
    const slots: Record<string, number> = {};
    for (const n of kitOrder(["perc", "ohat", "clap", "chat", "kick", "crash"])) {
      slots[n] = kitSlot(n, used);
      used.add(slots[n]);
    }
    expect(PAD_KEYS[slots.kick]).toBe("KeyZ");
    expect(PAD_KEYS[slots.clap]).toBe("KeyX"); // no snare: the clap sits next to the kick
    expect(PAD_KEYS[slots.chat]).toBe("KeyV");
    expect(PAD_KEYS[slots.ohat]).toBe("KeyB");
  });
});

describe("quantise (same vectors as muestra.pads.process)", () => {
  it.each(cases)("$beat on $grid s=$strength sw=$swing wrap=$wrap -> $out", (c) => {
    expect(quantizeBeat(c.beat, { grid: c.grid, strength: c.strength, swing: c.swing }, c.wrap)).toBeCloseTo(c.out, 9);
  });
  it("is non-destructive: processing keeps the raw beat", () => {
    const take = mkTake([{ beat: 1.13, pad: "KeyZ", velocity: 0.8, cycle: 1 }], { grid: "1/16", strength: 1, swing: 0 });
    const [e] = processEvents(take);
    expect(e.beat).toBe(1.25);
    expect(e.rawBeat).toBe(1.13);
    expect(take.events[0].beat).toBe(1.13);
  });
  it("dedupes double triggers and evens velocities like the backend", () => {
    const take = mkTake([
      { beat: 0, pad: "KeyZ", velocity: 0.6, cycle: 1 },
      { beat: 0.02, pad: "KeyZ", velocity: 0.9, cycle: 1 },
      { beat: 0.02, pad: "KeyX", velocity: 0.4, cycle: 1 },
      { beat: 1, pad: "KeyZ", velocity: 1, cycle: 1 },
    ]);
    const ev = processEvents(take, take.quantize, { dedupe_ms: 30, velocity: "as_played", velocity_value: 0.8 });
    expect(ev.map((e) => [e.beat, e.pad, e.velocity])).toEqual([
      [0, "KeyZ", 0.9],
      [0.02, "KeyX", 0.4],
      [1, "KeyZ", 1],
    ]);
  });
  it("places a loop take N times from a bar", () => {
    const take = { ...mkTake([{ beat: 3.97, pad: "KeyZ", velocity: 0.8, cycle: 2 }], { grid: "1/16", strength: 1, swing: 0 }), loop_beats: 4, length_beats: 4 };
    expect(takeSpan(take)).toBe(4);
    expect(placedBeats(take, 3, 2).map((h) => h.beat)).toEqual([9, 13]);
  });
});

describe("latency compensation", () => {
  it("maps the key event to the ctx time that was audible then", () => {
    // sample at ctx 10.000 s left the speakers at perf 5000 ms
    const ts = { contextTime: 10, performanceTime: 5000 };
    expect(heardCtxTime(5012, ts, 10.03, 5020, 0.02)).toBeCloseTo(10.012, 9);
    // without an output timestamp: currentTime minus elapsed minus output latency
    expect(heardCtxTime(5012, null, 10.03, 5020, 0.02)).toBeCloseTo(10.03 - 0.008 - 0.02, 9);
  });
  it("subtracts the stem bus delay and folds loops into cycles", () => {
    const run = { when: 100, offset: 2, loopStart: 2, busLatency: 0.005 };
    // heard 100.505 -> 0.5 s into the loop
    expect(heardUnwrapped(100.505, run)).toBeCloseTo(0.5, 9);
    const rec = { loopStartS: 2, loopLenS: 4, originBeat: 4, startCycle: 0 };
    expect(placePress(0.5, rec, 120)).toEqual({ beat: 1, cycle: 1 });
    expect(placePress(4.5, rec, 120)).toEqual({ beat: 1, cycle: 2 });
    // a press 10 ms before the loop start belongs to the end of the cycle
    const early = placePress(-0.01, rec, 120);
    expect(early.cycle).toBe(1);
    expect(early.beat).toBeCloseTo(7.98, 6);
    // linear takes: song beats from the origin (negative during the count-in)
    expect(placePress(1.0, { loopStartS: 0, loopLenS: null, originBeat: 8, startCycle: 0 }, 120).beat).toBeCloseTo(-6, 9);
  });
  it("calibrates from taps (median, first two answers dropped)", () => {
    const clicks = Array.from({ length: 12 }, (_, i) => 1 + i * 0.6);
    const taps = clicks.map((c, i) => c + 0.04 + (i % 2 ? 0.004 : -0.004) + (i < 2 ? 0.1 : 0));
    const r = calibrate(taps, clicks)!;
    expect(r.offsetS).toBeCloseTo(0.044, 3);
    expect(r.n).toBe(10);
    expect(calibrate([1, 2], clicks)).toBeNull();
  });
  it("says Bluetooth-like latency out loud", () => {
    expect(latencyVerdict(0.012, null).level).toBe("ok");
    expect(latencyVerdict(0.05, null).level).toBe("warn");
    const bad = latencyVerdict(0.19, null);
    expect(bad.level).toBe("bad");
    expect(bad.text).toMatch(/auriculares con cable/);
  });
});

describe("choke", () => {
  it("cuts earlier voices of the same group only", () => {
    const active = [
      { id: 1, pad: "KeyB", group: 1, start: 1.0 },
      { id: 2, pad: "KeyV", group: 1, start: 1.5 },
      { id: 3, pad: "KeyZ", group: null, start: 1.2 },
      { id: 4, pad: "KeyN", group: 2, start: 1.1 },
    ];
    expect(chokeTargets(active, 1, 1.5)).toEqual([1]); // same-instant voice keeps sounding
    expect(chokeTargets(active, 1, 2)).toEqual([1, 2]);
    expect(chokeTargets(active, null, 2)).toEqual([]);
  });
  it("mirrors the renderer's cut points in the editor expansion", () => {
    const song: SongJson = {
      meta: { title: "t", bpm: 120, key: "C", length_bars: 2 },
      tracks: [
        {
          id: "p",
          pattern: {
            lanes: { chat: { sample: "c.wav", choke: 1 }, ohat: { sample: "o.wav", choke: 1 }, kick: { sample: "k.wav" } },
            hits: [
              { beat: 1, lane: "ohat" },
              { beat: 1.5, lane: "chat" },
              { beat: 2, lane: "kick", length_beats: 0.25 },
              { beat: 3, lane: "ohat" },
              { beat: 3, lane: "chat" },
            ],
          },
        },
      ],
    };
    const cut = Object.fromEntries(patternHits(song, song.tracks![0]).map((h) => [`${h.lane}@${h.beat0}`, h.cut0]));
    expect(cut).toEqual({ "ohat@0": 0.5, "chat@0.5": 2, "kick@1": 1.25, "ohat@2": null, "chat@2": null });
    expect(applyChokes({ lanes: {} }, [])).toEqual([]);
  });
  it("keys voices identically for the request and the server's echo", () => {
    const req = { slice: { chop: "c1", length_beats: 1, start_beats: 2 } };
    const echo = { sample: null, slice: { chop: "c1", source: null, stem: null, start_downbeat: null, start_beats: 2, length_beats: 1, speed: "project" as const, warp: null }, pitch_semitones: 0, reverse: false };
    expect(voiceKey(req)).toBe(voiceKey(echo));
  });
});

describe("take overdub", () => {
  it("each pass adds; undo removes only the last pass", () => {
    let t = mkTake([{ beat: 0, pad: "KeyZ", velocity: 0.8, cycle: 1 }]);
    t = mergePass(t, [{ beat: 1, pad: "KeyX", velocity: 0.8, cycle: 2 }, { beat: 0.5, pad: "KeyV", velocity: 0.8, cycle: 2 }]);
    t = mergePass(t, [{ beat: 2, pad: "KeyX", velocity: 1, cycle: 3 }]);
    expect(t.events.map((e) => e.beat)).toEqual([0, 0.5, 1, 2]);
    const u = undoLastPass(t);
    expect(u.removed).toBe(1);
    expect(u.cycle).toBe(3);
    const u2 = undoLastPass(u.take);
    expect(u2.take.events.map((e) => e.pad)).toEqual(["KeyZ"]);
    expect(undoLastPass(mkTake([])).removed).toBe(0);
  });
});

function mkTake(events: TakeJson["events"], quantize: TakeJson["quantize"] = { grid: "off", strength: 1, swing: 0 }): TakeJson {
  return {
    name: "t",
    status: "done",
    bpm: 120,
    origin_beat: 0,
    length_beats: 8,
    pads: { KeyZ: { key: "KeyZ", name: "k", sample: "k.wav" }, KeyX: { key: "KeyX", name: "s", sample: "s.wav" }, KeyV: { key: "KeyV", name: "h", sample: "h.wav" } },
    events,
    quantize,
  };
}
