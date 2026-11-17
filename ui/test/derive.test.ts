import { describe, expect, it } from "vitest";
import type { FeedbackEvent, RenderMeta, VersionInfo } from "../src/api/types";
import { isMixerDefault, laneGain, patchLane, patchTrack, trackGain, trackUsesLanes, EMPTY_MIXER } from "../src/audio/mixer";
import { computePeaks, matchGain, peaksFromPayload, rms, rmsOfSum } from "../src/audio/signal";
import {
  alarmCounts,
  commentCounts,
  isPending,
  latestRatings,
  loudnessOffsets,
  newestVersionTs,
  readableDiff,
  timeAgo,
  versionTree,
} from "../src/state/derive";

describe("version tree", () => {
  const v = (version: string, parent: string | null, created_at = "2026-11-17T10:00:00Z"): VersionInfo => ({ version, parent, created_at });

  it("lists newest first, main line flat, branches indented", () => {
    // v001 → v002 → v003 ; v004 restarted from v002
    const rows = versionTree([v("v001", null), v("v002", "v001"), v("v003", "v002"), v("v004", "v002")], {});
    expect(rows.map((r) => [r.version, r.depth, r.branched])).toEqual([
      ["v004", 0, true],
      ["v003", 1, false],
      ["v002", 0, false],
      ["v001", 0, false],
    ]);
  });

  it("falls back to meta.parent and ignores unknown parents", () => {
    const metas = { v002: { parent: "v001" } as RenderMeta };
    const rows = versionTree([v("v001", null), { version: "v002" }, v("v003", "v999")], metas);
    expect(rows.find((r) => r.version === "v002")?.parent).toBe("v001");
    expect(rows.find((r) => r.version === "v003")?.parent).toBeNull();
  });
});

describe("feedback derivations", () => {
  const ev: FeedbackEvent[] = [
    { ts: "2026-11-17T10:00:00Z", type: "rating", version: "v001", value: "like" },
    { ts: "2026-11-17T10:01:00Z", type: "rating", version: "v001", value: "dislike" },
    { ts: "2026-11-17T10:02:00Z", type: "comment", version: "v001", at_s: 1, text: "a" },
    { ts: "2026-11-17T10:03:00Z", type: "comment", text: "general" },
    { ts: "2026-11-17T10:30:00Z", type: "comment", version: "v002", text: "b" },
  ];

  it("latest rating wins; comments counted per version", () => {
    expect(latestRatings(ev)).toEqual({ v001: "dislike" });
    expect(commentCounts(ev)).toEqual({ v001: 1, v002: 1 });
  });

  it("is pending until a newer version exists", () => {
    const versions = [{ version: "v002", created_at: "2026-11-17T10:10:00Z" }];
    const newest = newestVersionTs({}, versions);
    expect(isPending(ev[2], newest)).toBe(false);
    expect(isPending(ev[4], newest)).toBe(true);
  });
});

describe("loudness match", () => {
  it("only ever turns the louder version down", () => {
    expect(loudnessOffsets({ v001: -14, v002: -10.5 })).toEqual({ v001: 0, v002: -3.5 });
    expect(loudnessOffsets({ v001: -14, v002: null })).toBeNull();
  });
});

describe("readable diff (contract R10 paths)", () => {
  it("names tracks, fx and units in Spanish", () => {
    expect(readableDiff({ path: "tracks.sample.fx.lp.cutoff_hz", from: 18000, to: 1400 })).toEqual({
      where: "pista sample · efecto lp",
      what: "corte",
      from: "18.000 Hz",
      to: "1.400 Hz",
      kind: "changed",
    });
    expect(readableDiff({ path: "chops.c1.warp.pitch_semitones", from: 0, to: 1 })).toMatchObject({ where: "chop c1", what: "afinación", to: "1 st" });
    expect(readableDiff({ path: "meta.bpm", from: 120, to: 124 })).toMatchObject({ where: "canción", what: "tempo", to: "124 BPM" });
    expect(readableDiff({ path: "tracks.drums.pattern.lanes.kick.steps", from: "x...", to: "x.x." })).toMatchObject({ where: "pista drums · kick", what: "patrón" });
    expect(readableDiff({ path: "master.fx[1]", from: null, to: { type: "saturation" } })).toMatchObject({ where: "master · efecto 2", kind: "added", to: "saturation" });
    expect(readableDiff({ path: "tracks.bass.clips[0]", from: { chop: "c1" }, to: null })).toMatchObject({ where: "pista bass · clip 1", kind: "removed" });
  });

  it("relative time reads naturally", () => {
    const now = Date.parse("2026-11-17T12:00:00Z");
    expect(timeAgo("2026-11-17T11:59:50Z", now)).toBe("recién");
    expect(timeAgo("2026-11-17T11:57:00Z", now)).toBe("hace 3 min");
    expect(timeAgo("2026-11-17T09:00:00Z", now)).toBe("hace 3 h");
  });
});

describe("alarms", () => {
  it("counts by severity", () => {
    const m = { alarms: [{ severity: "warn" }, { severity: "warn" }, { severity: "error" }] } as never;
    expect(alarmCounts(m)).toEqual({ info: 0, warn: 2, error: 1 });
    expect(alarmCounts(null)).toEqual({ info: 0, warn: 0, error: 0 });
  });
});

describe("mixer rules", () => {
  it("untouched mixer plays the post-master mix", () => {
    expect(isMixerDefault(EMPTY_MIXER)).toBe(true);
    expect(isMixerDefault(patchTrack(EMPTY_MIXER, "drums", { mute: false, gainDb: 0.01 }))).toBe(true);
    expect(isMixerDefault(patchTrack(EMPTY_MIXER, "drums", { mute: true }))).toBe(false);
    expect(isMixerDefault(patchTrack(EMPTY_MIXER, "bass", { gainDb: -3 }))).toBe(false);
    expect(isMixerDefault(patchLane(EMPTY_MIXER, "drums", "kick", { solo: true }))).toBe(false);
  });

  it("mute and solo combine like a console", () => {
    let m = patchTrack(EMPTY_MIXER, "drums", { mute: true });
    expect(trackGain(m, "drums")).toBe(0);
    expect(trackGain(m, "bass")).toBe(1);
    m = patchTrack(m, "bass", { solo: true });
    expect(trackGain(m, "sample")).toBe(0);
    expect(trackGain(m, "bass")).toBe(1);
    m = patchTrack(m, "bass", { gainDb: -6 });
    expect(trackGain(m, "bass")).toBeCloseTo(0.501, 3);
    m = patchTrack(m, "drums", { solo: true }); // solo + mute: mute wins
    expect(trackGain(m, "drums")).toBe(0);
  });

  it("lane solo is scoped to its track", () => {
    const m = patchLane(EMPTY_MIXER, "drums", "kick", { solo: true });
    expect(trackUsesLanes(m, "drums")).toBe(true);
    expect(trackUsesLanes(m, "bass")).toBe(false);
    expect(laneGain(m, "drums", "kick")).toBe(1);
    expect(laneGain(m, "drums", "clap")).toBe(0);
    expect(laneGain(m, "perc", "clap")).toBe(1);
  });
});

describe("signal helpers", () => {
  const sine = (n: number, a: number) => Float32Array.from({ length: n }, (_, i) => a * Math.sin((2 * Math.PI * i) / 64));

  it("rms and rms of a sum", () => {
    const s = sine(6400, 1);
    expect(rms([s, s])).toBeCloseTo(Math.SQRT1_2, 3);
    expect(rmsOfSum([[s], [s]])).toBeCloseTo(2 * Math.SQRT1_2, 3);
    expect(rmsOfSum([[s], [Float32Array.from(s, (x) => -x)]])).toBe(0);
  });

  it("level match gain is clamped and safe on silence", () => {
    expect(matchGain(0.5, 0.25)).toBeCloseTo(2);
    expect(matchGain(1, 1e-3)).toBeCloseTo(10 ** (18 / 20));
    expect(matchGain(0.5, 0)).toBe(1);
  });

  it("peaks merge channels into min/max buckets", () => {
    const p = computePeaks([Float32Array.from([0, 0.5, -0.25, 0]), Float32Array.from([0, -0.75, 0, 0.1])], 2);
    expect(Array.from(p)).toEqual([-0.75, 0.5, -0.25, 0.10000000149011612]);
    expect(Array.from(peaksFromPayload([[[-0.5, 0.25]], [[-0.1, 0.75]]]))).toEqual([-0.5, 0.75]);
  });
});
