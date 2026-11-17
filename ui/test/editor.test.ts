import { describe, expect, it } from "vitest";
import { deleteClips, moveClip, moveHits, resizeClipLeft, resizeClipRight, togglePassMute } from "../src/editor/actions";
import { buildArrangement, emptyPieces, type PieceSet } from "../src/editor/arrange";
import { applyOps, getAt, hitOps, sameValue, splitClipOps, type Op } from "../src/editor/ops";
import { selectionLabel, selectionPayload } from "../src/editor/selection";
import { clipPasses, laneBarSteps, parseSteps, patternHits, scalePitchClasses, stable, trackNotes, velChar, wetFingerprint, type SongJson } from "../src/editor/song";
import { defaultView, gridStep, layoutRows, snapBeat, xToBeat, zoomAt } from "../src/editor/timeline";

function song(): SongJson {
  return {
    meta: { title: "t", bpm: 120, key: "F#m", length_bars: 16, seed: 0 },
    sources: [{ id: "src1", file: "sources/a.wav" }],
    chops: [{ id: "c1", source: "src1", stem: "other", start_downbeat: 1, length_beats: 8, warp: { mode: "resample", pitch_semitones: 0 }, fade_ms: 5 }],
    tracks: [
      { id: "sample", clips: [{ chop: "c1", at_bar: 1, repeat: 8 }], fx: [{ id: "lp", type: "lowpass", cutoff_hz: 18000 }, { id: "sc", type: "sidechain", key_track: "drums", depth: 0.5 }], gain_db: -2 },
      {
        id: "drums",
        pattern: {
          steps_per_bar: 16,
          lanes: {
            kick: { sample: "library://k.wav", steps: "x...x...x...x..." },
            clap: { sample: "library://c.wav", steps: "....x.......x..." },
          },
        },
      },
      { id: "bass", instrument: { plugin: "sfz" }, notes: [{ beat: 1, pitch: 40, length_beats: 0.5 }, { beat: 3, pitch: 43, length_beats: 1, velocity: 90 }] },
    ],
    automation: [{ target: "tracks.sample.fx.lp.cutoff_hz", curve: "exp", points: [[1, 800], [5, 18000]] }],
    master: { fx: [] },
  };
}

describe("expansion (mirrors schemas/song.py)", () => {
  it("parses steps and writes velocities", () => {
    expect(parseSteps("x.o9 1-")).toEqual([1, null, 0.6, 1, 1 / 9, null]);
    expect([1, 0.6, 5 / 9, null].map(velChar).join("")).toBe("xo5.");
  });

  it("tiles the pattern, applies one-bar overrides and free hits", () => {
    const s = song();
    const drums = s.tracks![1];
    expect(patternHits(s, drums).filter((h) => h.lane === "kick")).toHaveLength(64);
    drums.pattern!.lanes.kick.overrides = { "7": "x...x.......x..." };
    drums.pattern!.hits = [{ beat: 9.37, lane: "clap", velocity: 0.5 }];
    const hits = patternHits(s, drums);
    const kick7 = hits.filter((h) => h.lane === "kick" && h.bar === 7);
    expect(kick7.map((h) => h.step)).toEqual([0, 4, 12]);
    expect(kick7.every((h) => h.origin === "override")).toBe(true);
    const free = hits.find((h) => h.origin === "free")!;
    expect(free.beat0).toBeCloseTo(8.37, 9);
    expect(free.bar).toBe(3);
    expect(laneBarSteps(drums.pattern!, drums.pattern!.lanes.kick, 7, 1)).toEqual(parseSteps("x...x.......x..."));
    expect(laneBarSteps(drums.pattern!, drums.pattern!.lanes.kick, 6, 1)).toEqual(parseSteps("x...x...x...x..."));
  });

  it("expands clip passes with trims and per-pass overrides", () => {
    const s = song();
    s.tracks![0].clips = [{ chop: "c1", at_bar: 1, repeat: 4, length_beats: 4, trim_start_beats: 2, instances: { "3": { mute: true }, "2": { gain_db: -6, reverse: true } } }];
    const p = clipPasses(s, s.tracks![0]);
    expect(p.map((x) => x.start0)).toEqual([0, 4, 8, 12]);
    expect(p[1]).toMatchObject({ gainDb: -6, reverse: true, materialStart: 2 });
    expect(p[2].mute).toBe(true);
  });

  it("notes, scale and fingerprints", () => {
    const s = song();
    expect(trackNotes(s, s.tracks![2]).map((n) => n.pitch)).toEqual([40, 43]);
    expect([...scalePitchClasses("F#m")].sort((a, b) => a - b)).toEqual([1, 2, 4, 6, 8, 9, 11]);
    const fp = wetFingerprint(s, "sample");
    const g = structuredClone(s);
    g.tracks![0].gain_db = 3; // the fader applies static gain: wet stays valid
    expect(wetFingerprint(g, "sample")).toBe(fp);
    g.tracks![1].pattern!.lanes.kick.steps = "x.......x......."; // sidechain key changed
    expect(wetFingerprint(g, "sample")).not.toBe(fp);
    expect(wetFingerprint(g, "bass")).toBe(wetFingerprint(s, "bass"));
  });
});

describe("ops (mirror of editor/ops.py)", () => {
  const roundtrip = (ops: Op[]) => {
    const s = song();
    const r = applyOps(s, ops);
    const back = applyOps(r.song, r.inverse);
    // an insert into a list that did not exist leaves it empty: the same song
    const prune = (v: unknown): unknown =>
      Array.isArray(v) ? v.map(prune) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).filter(([, x]) => !(x && typeof x === "object" && !Object.keys(x).length)).map(([k, x]) => [k, prune(x)])) : v;
    expect(stable(prune(back.song))).toBe(stable(prune(s)));
    return r;
  };

  it("set / unset / insert / remove / move and their inverses", () => {
    roundtrip([{ op: "set", path: "tracks.sample.gain_db", value: -6 }]);
    roundtrip([{ op: "set", path: "tracks.drums.pan", value: 0.3 }]);
    roundtrip([{ op: "unset", path: "tracks.sample.gain_db" }]);
    roundtrip([{ op: "insert", path: "tracks.drums.pattern.hits", value: { beat: 2.5, lane: "kick" } }]);
    roundtrip([{ op: "remove", path: "tracks.sample.clips[0]" }]);
    roundtrip([{ op: "move", path: "tracks.sample.fx[0]", to: 1 }]);
    roundtrip([{ op: "remove", path: "tracks.sample.fx.sc" }]);
    const r = roundtrip([{ op: "set", path: "automation.tracks.sample.fx.lp.cutoff_hz.points[1]", value: [6, 12000] }]);
    expect(getAt(r.song, "automation.tracks.sample.fx.lp.cutoff_hz.points[1]")).toEqual([6, 12000]);
    roundtrip([{ op: "set", path: "tracks.sample.clips[0].instances.3", value: { mute: true } }]);
  });

  it("refuses an op whose expect no longer holds", () => {
    expect(() => applyOps(song(), [{ op: "set", path: "tracks.sample.gain_db", value: 1, expect: 5 }])).toThrow();
    expect(sameValue({ a: 1.0000000001 }, { a: 1 })).toBe(true);
  });

  it("hit ops write and drop the per-bar override", () => {
    const s = song();
    const rm = hitOps(s, "drums", "kick", 7, 8, null);
    expect(rm).toEqual([{ op: "set", path: "tracks.drums.pattern.lanes.kick.overrides.7", value: "x...x.......x..." }]);
    const s2 = applyOps(s, rm).song;
    const back = hitOps(s2, "drums", "kick", 7, 8, 1);
    expect(back[0]).toMatchObject({ op: "unset", path: "tracks.drums.pattern.lanes.kick.overrides" });
    expect(stable(applyOps(s2, back).song)).toBe(stable(applyOps(s, []).song));
    expect(hitOps(s, "drums", "kick", 3, 1, 0.6)[0]).toMatchObject({ value: "xo..x...x...x..." });
  });

  it("splits a repeated clip keeping every pass where it was", () => {
    const s = song();
    const before = clipPasses(s, s.tracks![0]).map((p) => [p.start0, p.end0]);
    const ops = splitClipOps(s, "sample", 0, 10)!;
    const after = applyOps(s, ops).song;
    const passes = clipPasses(after, after.tracks![0]);
    expect(passes.slice(0, 3).map((p) => [p.start0, p.length, p.materialStart])).toEqual([
      [0, 8, 0],
      [8, 2, 0],
      [10, 6, 2],
    ]);
    expect(passes.reduce((n, p) => n + p.length, 0)).toBe(before.length * 8);
  });
});

describe("actions", () => {
  it("move / trim / repeat / mute passes", () => {
    const s = song();
    s.tracks![0].clips![0].repeat = 2;
    const mv = moveClip(s, "sample", 0, 6)!;
    expect(applyOps(s, mv.ops).song.tracks![0].clips![0]).toMatchObject({ at_bar: 2, offset_beats: 2 });
    const right = resizeClipRight(s, "sample", 0, 8, false, 0.25)!;
    expect(applyOps(s, right.ops).song.tracks![0].clips![0].repeat).toBe(3);
    const s1 = song();
    s1.tracks![0].clips![0].repeat = 1;
    const trim = resizeClipLeft(s1, "sample", 0, 2, 0.25)!;
    expect(applyOps(s1, trim.ops).song.tracks![0].clips![0]).toMatchObject({ trim_start_beats: 2, length_beats: 6, offset_beats: 2 });
    const stretch = resizeClipRight(s1, "sample", 0, 8, true, 0.25)!;
    expect(applyOps(s1, stretch.ops).song.tracks![0].clips![0]).toMatchObject({ stretch: 2, length_beats: 16 });
    const del = deleteClips(s, "sample", [{ clip: 0, instance: 2 }])!;
    expect(applyOps(s, del.ops).song.tracks![0].clips![0].instances).toEqual({ "2": { mute: true } });
    const mute = togglePassMute(s, "sample", [{ clip: 0, instance: 1 }])!;
    expect(applyOps(s, mute.ops).song.tracks![0].clips![0].instances).toEqual({ "1": { mute: true } });
  });

  it("moves grid hits on the grid and free hits freely", () => {
    const s = song();
    const e = moveHits(s, "drums", [{ lane: "kick", bar: 2, step: 0, index: null, beat0: 4 }], 0.5, false)!;
    const out = applyOps(s, e.ops).song;
    expect(out.tracks![1].pattern!.lanes.kick.overrides).toEqual({ "2": "..x.x...x...x..." });
  });
});

describe("arrangement", () => {
  it("keeps event keys stable for untouched events and skips missing audio", () => {
    const s = song();
    const fakeBuf = (sec: number) => ({ duration: sec, sampleRate: 48000, length: sec * 48000, numberOfChannels: 2 }) as unknown as AudioBuffer;
    const pieces: PieceSet = { ...emptyPieces(), oneshots: new Map([["library://k.wav", fakeBuf(0.3)], ["library://c.wav", fakeBuf(0.3)]]) };
    const a = buildArrangement(s, pieces, { solo: new Set() });
    expect(a.status.sample.state).toBe("waiting"); // no chop material yet
    const kicks = a.arr.events.filter((e) => e.lane === "kick");
    expect(kicks).toHaveLength(64);
    const edited = applyOps(s, hitOps(s, "drums", "kick", 7, 8, null)).song;
    const b = buildArrangement(edited, pieces, { solo: new Set() });
    const keysA = new Set(a.arr.events.map((e) => e.key));
    const keysB = new Set(b.arr.events.map((e) => e.key));
    expect([...keysA].filter((k) => !keysB.has(k))).toHaveLength(1);
    expect([...keysB].filter((k) => !keysA.has(k))).toHaveLength(0);
    expect(a.arr.triggers["drums.kick"]).toHaveLength(64);
  });
});

describe("timeline + selection", () => {
  it("snaps, zooms around the pointer and fills the height", () => {
    expect(snapBeat(5.13, "16th")).toBe(5.25);
    expect(snapBeat(5.13, "16th", true)).toBe(5.13);
    expect(snapBeat(6.1, "bar")).toBe(8);
    expect(gridStep(40)).toBe(0.25);
    const v = defaultView();
    const z = zoomAt(v, 2, 300);
    expect(xToBeat(300, z)).toBeCloseTo(xToBeat(300, v), 6);
    const s = song();
    const plain = layoutRows(s, v);
    const filled = layoutRows(s, v, plain.height * 2);
    expect(filled.height).toBeGreaterThan(plain.height * 1.5);
    expect(plain.rows[1].lanes.map((l) => l.name)).toEqual(["kick", "clap"]);
  });

  it("describes a selection in Spanish and with song.edit paths", () => {
    const s = song();
    const sel = { kind: "hits" as const, track: "drums", items: [{ lane: "kick", bar: 7, step: 8, index: null, beat0: 26 }] };
    expect(selectionLabel(s, sel)).toBe("bombo · compás 7, tercer tiempo");
    const p = selectionPayload(s, sel)!;
    expect(p).toMatchObject({ track: "drums", lane: "kick", start_beat: 27, paths: ["tracks.drums.pattern.lanes.kick.overrides.7"] });
    expect((p.hits as unknown[])[0]).toEqual({ lane: "kick", bar: 7, step: 8, beat: 27, index: null });
  });
});
