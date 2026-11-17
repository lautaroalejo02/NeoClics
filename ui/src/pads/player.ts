// The pads' sound: one BufferSource per press, started at once (start(0) =
// the next render quantum), no awaits and no React on the way. Choke groups
// and gate release fade the cut voice over 3 ms (the renderer's CUT_FADE_MS,
// same raised-cosine shape), so retriggers and chokes never click.

import { panLaw } from "../audio/arrangement";
import { CUT_FADE_MS } from "../editor/song";
import { chokeTargets, type LiveVoice } from "./model";

const FADE_S = CUT_FADE_MS / 1000;

function fadeCurve(from: number, n = 32): Float32Array {
  const c = new Float32Array(n);
  for (let i = 0; i < n; i++) c[i] = from * (0.5 + 0.5 * Math.cos((Math.PI * i) / (n - 1)));
  return c;
}

interface Voice extends LiveVoice {
  srcs: AudioBufferSourceNode[];
  gain: GainNode;
  level: number;
  ended: boolean;
}

export interface PressInfo {
  id: number;
  /** ctx time the sound was scheduled at */
  when: number;
  /** ms from the key event to src.start() (input -> scheduled) */
  schedMs: number;
}

export class PadsPlayer {
  readonly ctx: BaseAudioContext;
  readonly out: GainNode;
  private voices = new Map<number, Voice>();
  private nextId = 1;
  /** last input->scheduled measurements (ms) */
  readonly sched: number[] = [];

  constructor(ctx: BaseAudioContext, dest: AudioNode) {
    this.ctx = ctx;
    this.out = ctx.createGain();
    this.out.connect(dest);
  }

  /** Overall level (dB), e.g. the editor mix's level match. */
  setLevelDb(db: number): void {
    this.out.gain.value = 10 ** (db / 20);
  }

  /**
   * Play `buffers` (one per note of a chord, else one) now. `gain` = velocity x
   * pad gain; `pan` uses the renderer's pan law with the editor's dry -3 dB
   * (so a pad sounds like its committed lane in the arrangement).
   */
  press(pad: string, buffers: AudioBuffer[], opts: { gain: number; pan: number; group: number | null; eventMs?: number; when?: number }): PressInfo {
    const ctx = this.ctx;
    const now = ctx.currentTime;
    const when = opts.when ?? now;
    for (const id of chokeTargets([...this.voices.values()], opts.group, when + 1e-6)) this.cut(id, when);
    const g = ctx.createGain();
    const [l, r] = panLaw(opts.pan);
    const k = opts.gain * Math.SQRT1_2;
    const split = ctx.createChannelSplitter(2);
    const merge = ctx.createChannelMerger(2);
    const lg = ctx.createGain();
    const rg = ctx.createGain();
    lg.gain.value = l * k;
    rg.gain.value = r * k;
    g.connect(split);
    split.connect(lg, 0);
    split.connect(rg, 1);
    lg.connect(merge, 0, 0);
    rg.connect(merge, 0, 1);
    merge.connect(this.out);
    const srcs: AudioBufferSourceNode[] = [];
    for (const b of buffers) {
      const src = ctx.createBufferSource();
      src.buffer = b;
      src.channelCountMode = "explicit";
      src.channelCount = 2;
      src.connect(g);
      src.start(when);
      srcs.push(src);
    }
    const schedMs = opts.eventMs != null && typeof performance !== "undefined" ? performance.now() - opts.eventMs : 0;
    const id = this.nextId++;
    const voice: Voice = { id, pad, group: opts.group, start: when, srcs, gain: g, level: 1, ended: false };
    this.voices.set(id, voice);
    let left = srcs.length;
    for (const s of srcs)
      s.onended = () => {
        if (--left > 0) return;
        voice.ended = true;
        this.voices.delete(id);
        try {
          merge.disconnect();
        } catch {
          /* */
        }
      };
    if (opts.eventMs != null) {
      this.sched.push(schedMs);
      if (this.sched.length > 500) this.sched.shift();
    }
    return { id, when, schedMs };
  }

  /** Gate release / choke: 3 ms fade from `at`, then stop. */
  cut(id: number, at: number = this.ctx.currentTime): void {
    const v = this.voices.get(id);
    if (!v) return;
    this.voices.delete(id);
    const p = v.gain.gain;
    const t = Math.max(at, this.ctx.currentTime);
    const anyP = p as AudioParam & { cancelAndHoldAtTime?: (t: number) => void };
    if (typeof anyP.cancelAndHoldAtTime === "function") anyP.cancelAndHoldAtTime(t);
    else p.cancelScheduledValues(t);
    try {
      p.setValueCurveAtTime(fadeCurve(v.level), t, FADE_S);
    } catch {
      p.setValueAtTime(0, t + FADE_S);
    }
    for (const s of v.srcs) {
      try {
        s.stop(t + FADE_S + 0.001);
      } catch {
        /* */
      }
    }
  }

  /** Release every sounding voice of a pad (gate pads). */
  release(pad: string, at?: number): void {
    for (const v of [...this.voices.values()]) if (v.pad === pad) this.cut(v.id, at);
  }

  stopAll(): void {
    for (const id of [...this.voices.keys()]) this.cut(id);
  }

  get activeCount(): number {
    return this.voices.size;
  }

  dispose(): void {
    this.stopAll();
    this.out.disconnect();
  }
}

/** Short click buffers for the metronome / count-in / calibration (synthesized). */
export function clickBuffer(ctx: BaseAudioContext, accent: boolean): AudioBuffer {
  const sr = ctx.sampleRate;
  const n = Math.round(sr * 0.03);
  const b = ctx.createBuffer(2, n, sr);
  const f = accent ? 1760 : 1175;
  const a = accent ? 0.5 : 0.32;
  for (let c = 0; c < 2; c++) {
    const d = b.getChannelData(c);
    for (let i = 0; i < n; i++) {
      const t = i / sr;
      const env = Math.min(1, i / (sr * 0.0005)) * Math.exp(-t / 0.006);
      d[i] = a * env * Math.sin(2 * Math.PI * f * t);
    }
  }
  return b;
}

/**
 * Metronome on the ctx clock. `clock()` gives the editor transport (null when
 * stopped); `countIn` adds clicks before a planned start. Clicks are scheduled
 * 150 ms ahead from a 25 ms timer (like the arrangement player).
 */
export class Metronome {
  private timer: ReturnType<typeof setInterval> | null = null;
  private cursor = 0;
  private hi: AudioBuffer;
  private lo: AudioBuffer;
  private out: GainNode;
  private scheduled: AudioBufferSourceNode[] = [];
  /** ctx times of every click scheduled (tests, calibration) */
  readonly clicks: number[] = [];
  enabled = true;

  constructor(
    private ctx: BaseAudioContext,
    dest: AudioNode,
    private clock: () => { when: number; offset: number; loopStart: number; loopEnd: number; busLatency: number } | null,
    private bpm: () => number,
  ) {
    this.hi = clickBuffer(ctx, true);
    this.lo = clickBuffer(ctx, false);
    this.out = ctx.createGain();
    this.out.gain.value = 0.9;
    this.out.connect(dest);
  }

  private click(at: number, accent: boolean): void {
    const s = this.ctx.createBufferSource();
    s.buffer = accent ? this.hi : this.lo;
    s.connect(this.out);
    s.start(at);
    this.scheduled.push(s);
    this.clicks.push(at);
    if (this.clicks.length > 2000) this.clicks.splice(0, 1000);
    s.onended = () => {
      this.scheduled = this.scheduled.filter((x) => x !== s);
    };
  }

  /** Count-in clicks ending at ctx time `start` (`bars` of 4, accented downbeat). */
  countIn(start: number, bars: number): void {
    const spb = 60 / this.bpm();
    const n = bars * 4;
    for (let i = 0; i < n; i++) this.click(start - (n - i) * spb, i % 4 === 0);
  }

  start(): void {
    if (this.timer != null) return;
    this.cursor = this.ctx.currentTime;
    this.timer = setInterval(() => this.tick(), 25);
    this.tick();
  }

  stop(): void {
    if (this.timer != null) clearInterval(this.timer);
    this.timer = null;
    for (const s of this.scheduled) {
      try {
        s.stop();
      } catch {
        /* */
      }
    }
    this.scheduled = [];
  }

  /** Schedule clicks on transport beats in [cursor, now + 150 ms). */
  tick(horizonS = 0.15): void {
    const r = this.clock();
    const now = this.ctx.currentTime;
    const to = now + horizonS;
    if (!r || !this.enabled) {
      this.cursor = to;
      return;
    }
    const from = Math.max(this.cursor, r.when);
    if (to <= from) return;
    const spb = 60 / this.bpm();
    const L = r.loopEnd - r.loopStart;
    // u(t) = seconds since the loop start, unwrapped; heard = scheduled + busLatency
    const u = (t: number) => r.offset - r.loopStart + (t - r.when);
    const u0 = u(from);
    const u1 = u(to);
    for (let p = Math.floor(u0 / L); p <= Math.floor(u1 / L); p++) {
      const a = Math.max(u0, p * L);
      const b = Math.min(u1, (p + 1) * L);
      const sA = r.loopStart + (a - p * L);
      const sB = r.loopStart + (b - p * L);
      for (let beat = Math.ceil(sA / spb - 1e-9); beat * spb < sB - 1e-9; beat++) {
        const t = r.when + (p * L + beat * spb - r.loopStart) - (r.offset - r.loopStart) + r.busLatency;
        if (t >= now - 0.002) this.click(Math.max(t, now), beat % 4 === 0);
      }
    }
    this.cursor = to;
  }

  dispose(): void {
    this.stop();
    this.out.disconnect();
  }
}
