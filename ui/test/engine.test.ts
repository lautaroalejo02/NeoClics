// AudioEngine scheduling against a recording fake of the Web Audio API.
// We cannot listen here; we verify the arithmetic that makes playback
// sample-synchronous: every source of a run starts with the same
// (when, offset), and an A/B switch starts the incoming version exactly on
// the outgoing version's position at the switch time.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AudioEngine, type DeckSpec } from "../src/audio/engine";
import { EMPTY_MIXER, patchLane, patchTrack } from "../src/audio/mixer";

type Ev = [string, ...number[]];

class FakeParam {
  value: number;
  events: Ev[] = [];
  constructor(v: number) {
    this.value = v;
  }
  setValueAtTime(v: number, t: number) {
    this.events.push(["set", v, t]);
    this.value = v;
  }
  linearRampToValueAtTime(v: number, t: number) {
    this.events.push(["lin", v, t]);
    this.value = v;
  }
  setTargetAtTime(v: number, t: number, tau: number) {
    this.events.push(["target", v, t, tau]);
    this.value = v;
  }
  setValueCurveAtTime(c: Float32Array, t: number, d: number) {
    this.events.push(["curve", c[0], c[c.length - 1], t, d]);
    this.value = c[c.length - 1];
  }
  cancelScheduledValues(t: number) {
    this.events.push(["cancel", t]);
  }
  cancelAndHoldAtTime(t: number) {
    this.events.push(["hold", t]);
  }
}

class FakeNode {
  ctx: FakeCtx;
  outs: FakeNode[] = [];
  constructor(ctx: FakeCtx) {
    this.ctx = ctx;
  }
  connect(n: FakeNode) {
    this.outs.push(n);
    return n;
  }
  disconnect() {
    this.outs = [];
  }
}

class FakeGain extends FakeNode {
  gain = new FakeParam(1);
}

class FakeSource extends FakeNode {
  buffer: FakeBuffer | null = null;
  loop = false;
  loopStart = 0;
  loopEnd = 0;
  onended: (() => void) | null = null;
  started: [number, number] | null = null;
  stoppedAt: number | null = null;
  start(when: number, offset: number) {
    this.started = [when, offset];
    this.ctx.sources.push(this);
  }
  stop(at = 0) {
    this.stoppedAt = at;
  }
}

class FakeBuffer {
  constructor(
    public path: string,
    public duration: number,
    public sampleRate: number,
    public numberOfChannels = 2,
  ) {}
  get length() {
    return Math.round(this.duration * this.sampleRate);
  }
  getChannelData() {
    // a constant signal so level matching has something to measure
    return new Float32Array(this.length).fill(this.path.includes("stems") ? 0.25 : 0.5);
  }
}

class FakeCtx {
  static last: FakeCtx | null = null;
  currentTime = 10;
  sampleRate: number;
  state = "suspended";
  destination = new FakeNode(this);
  sources: FakeSource[] = [];
  outputLatency = 0;
  onstatechange: (() => void) | null = null;
  constructor(opts?: { sampleRate?: number }) {
    this.sampleRate = opts?.sampleRate ?? 48000;
    FakeCtx.last = this;
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    return Promise.resolve();
  }
  createGain() {
    return new FakeGain(this);
  }
  createBufferSource() {
    return new FakeSource(this);
  }
  decodeAudioData(buf: ArrayBuffer) {
    const path = new TextDecoder().decode(buf);
    return Promise.resolve(new FakeBuffer(path, 16, this.sampleRate));
  }
}

const g = globalThis as Record<string, unknown>;
let savedCtx: unknown;

beforeEach(() => {
  savedCtx = g.AudioContext;
  g.AudioContext = FakeCtx;
});
afterEach(() => {
  g.AudioContext = savedCtx;
});

function spec(version: string, bpm = 120, firstBar = 1): DeckSpec {
  return {
    version,
    bpm,
    firstBar,
    durationS: 16,
    sampleRate: 48000,
    mix: `renders/${version}/mix.wav`,
    tracks: [
      { id: "sample", stem: `renders/${version}/stems/sample.wav`, lanes: [] },
      {
        id: "drums",
        stem: `renders/${version}/stems/drums.wav`,
        lanes: [
          { id: "kick", stem: `renders/${version}/stems/drums.kick.wav` },
          { id: "clap", stem: `renders/${version}/stems/drums.clap.wav` },
        ],
      },
      { id: "bass", stem: `renders/${version}/stems/bass.wav`, lanes: [] },
    ],
  };
}

const flush = async () => {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};

function makeEngine(budget?: number) {
  const fetched: string[] = [];
  const engine = new AudioEngine({
    fetchFile: async (p) => {
      fetched.push(p);
      return new TextEncoder().encode(p).buffer as ArrayBuffer;
    },
    memoryBudget: budget,
  });
  return { engine, fetched };
}

function startsOf(ctx: FakeCtx, version: string) {
  return ctx.sources.filter((s) => s.buffer?.path.includes(`/${version}/`) && s.stoppedAt == null);
}

describe("AudioEngine", () => {
  it("starts the mix and every stem with the same (when, offset)", async () => {
    const { engine } = makeEngine();
    engine.register(spec("v001"));
    engine.setInitial("v001");
    await engine.prepare("v001", { stems: true });
    await flush();
    engine.seek(3.3);
    engine.play();
    const ctx = FakeCtx.last!;
    const run = startsOf(ctx, "v001");
    expect(run.map((s) => s.buffer!.path).sort()).toEqual([
      "renders/v001/mix.wav",
      "renders/v001/stems/bass.wav",
      "renders/v001/stems/drums.wav",
      "renders/v001/stems/sample.wav",
    ]);
    const [when, offset] = run[0].started!;
    expect(when).toBeGreaterThan(ctx.currentTime);
    expect(offset).toBe(3.3);
    for (const s of run) expect(s.started).toEqual([when, offset]);
    expect(engine.getSnapshot().listening).toBe("mix");
  });

  it("A/B: the incoming version starts exactly where the outgoing one is at T", async () => {
    const { engine } = makeEngine();
    engine.register(spec("v001"));
    engine.register(spec("v002"));
    engine.setInitial("v001");
    engine.setPartner("v002");
    await engine.prepare("v001", { stems: true });
    await engine.prepare("v002", { stems: true });
    await flush();
    engine.play();
    const ctx = FakeCtx.last!;
    const t0 = startsOf(ctx, "v001")[0].started![0];
    ctx.currentTime = t0 + 5.123456; // 5.123456 s into playback
    await engine.select("v002");
    const sw = engine.lastSwitch!;
    expect(sw.from).toBe("v001");
    expect(sw.to).toBe("v002");
    // position of v001 at T, exactly
    expect(sw.fromPos).toBeCloseTo(sw.at - t0, 12);
    expect(sw.toOffset).toBe(sw.fromPos); // same timing → identity
    const incoming = startsOf(ctx, "v002");
    expect(incoming.length).toBe(4);
    for (const s of incoming) expect(s.started).toEqual([sw.at, sw.toOffset]);
    // outgoing sources stop after the crossfade, not before
    const outgoing = ctx.sources.filter((s) => s.buffer?.path.includes("/v001/"));
    for (const s of outgoing) expect(s.stoppedAt).toBeGreaterThan(sw.at + 0.02 - 1e-9);
    expect(engine.getSnapshot().active).toBe("v002");
    expect(engine.getSnapshot().partner).toBe("v001");
  });

  it("A/B across tempos maps by bar/beat, not seconds", async () => {
    const { engine } = makeEngine();
    engine.register(spec("v001", 120));
    engine.register(spec("v002", 126));
    engine.setInitial("v001");
    await engine.prepare("v001");
    await engine.prepare("v002");
    engine.play();
    const ctx = FakeCtx.last!;
    const t0 = startsOf(ctx, "v001")[0].started![0];
    ctx.currentTime = t0 + 4 - 0.012; // so that T ≈ 4 s = bar 3 beat 1 at 120
    await engine.select("v002");
    const sw = engine.lastSwitch!;
    expect(sw.toOffset).toBeCloseTo((sw.fromPos * 120) / 126, 12);
  });

  it("mute/solo switches to the level-matched stem sum without rebuilding sources", async () => {
    const { engine } = makeEngine();
    engine.register(spec("v001"));
    engine.setInitial("v001");
    await engine.prepare("v001", { stems: true });
    await flush();
    engine.play();
    const ctx = FakeCtx.last!;
    const before = ctx.sources.length;
    engine.setMixer(patchTrack(EMPTY_MIXER, "drums", { mute: true }));
    expect(ctx.sources.length).toBe(before); // no restart, no new sources
    expect(engine.getSnapshot().listening).toBe("stems");
    // stems are 0.25 constant x3 → sum 0.75; mix 0.5 → match gain 2/3
    const stemG = (engine as unknown as { decks: Map<string, { stemG: FakeGain; mixG: FakeGain }> }).decks.get("v001")!;
    expect(stemG.stemG.gain.value).toBeCloseTo(0.5 / 0.75, 6);
    expect(stemG.mixG.gain.value).toBe(0);
    engine.setMixer(EMPTY_MIXER);
    expect(engine.getSnapshot().listening).toBe("mix");
    expect(stemG.mixG.gain.value).toBe(1);
  });

  it("lane stems load on demand and join the running clock", async () => {
    const { engine, fetched } = makeEngine();
    engine.register(spec("v001"));
    engine.setInitial("v001");
    await engine.prepare("v001", { stems: true });
    await flush();
    expect(fetched.some((p) => p.includes("drums.kick"))).toBe(false);
    engine.play();
    const ctx = FakeCtx.last!;
    const [w0, o0] = startsOf(ctx, "v001")[0].started!;
    ctx.currentTime += 2.5;
    engine.setMixer(patchLane(EMPTY_MIXER, "drums", "kick", { solo: true }));
    await flush();
    const lanes = ctx.sources.filter((s) => s.buffer?.path.includes("drums.kick") || s.buffer?.path.includes("drums.clap"));
    expect(lanes).toHaveLength(2);
    for (const s of lanes) {
      const [w, o] = s.started!;
      // started later, at the offset the run's clock says → aligned
      expect(o - o0).toBeCloseTo(w - w0, 12);
    }
  });

  it("seek and loop edits restart the run behind a fade", async () => {
    const { engine } = makeEngine();
    engine.register(spec("v001"));
    engine.setInitial("v001");
    await engine.prepare("v001");
    engine.play();
    const ctx = FakeCtx.last!;
    engine.seek(8);
    const run = startsOf(ctx, "v001");
    expect(run).toHaveLength(1);
    expect(run[0].started![1]).toBe(8);
    engine.setLoop({ startBeat: 8, endBeat: 16 }); // bars 3-4 = 4..8 s
    const looped = startsOf(ctx, "v001");
    expect(looped[0].loop).toBe(true);
    expect(looped[0].loopStart).toBe(4);
    expect(looped[0].loopEnd).toBe(8);
    // the playhead was at/after the loop end → restarts at loop start
    expect(looped[0].started![1]).toBe(4);
  });

  it("evicts least-recently-used decoded versions over budget, never A/B", async () => {
    const perVersion = 16 * 48000 * 2 * 4; // one 16 s stereo float buffer
    const { engine } = makeEngine(perVersion * 2.5);
    for (const v of ["v001", "v002", "v003"]) engine.register(spec(v));
    engine.setInitial("v003");
    engine.setPartner("v002");
    await engine.prepare("v001");
    await engine.prepare("v002");
    await engine.prepare("v003");
    await flush();
    expect(engine.isReady("v003")).toBe(true);
    expect(engine.isReady("v002")).toBe(true);
    expect(engine.isReady("v001")).toBe(false);
  });
});
