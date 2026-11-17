// Look-ahead brickwall limiter for the STEM bus (WP14 finding 1).
//
// Why it exists: with mute/solo/fader touched the app plays the pre-master
// stems, level-matched to the mastered mix by RMS (+3..+10 dB). The stems
// have much more crest than the mastered mix, so the sum peaked at +2..+5
// dBFS and the browser hard-clipped kick transients. This limiter sits on
// the stem bus and guarantees |y| <= ceiling for every sample.
//
// Algorithm (classic, provable): with a look-ahead of D samples the output
// sample x[n-D] is multiplied by g[n] where
//     t[j]  = min(1, ceiling / peak[j])               (per-sample requirement)
//     m[n]  = min(t[n-D .. n])                        (sliding window minimum)
//     r[n]  = min(m[n], r[n-1] + (1 - r[n-1]) * rel)  (exponential release)
//     g[n]  = mean(r[n-D .. n])                       (attack smoothing)
// Every r[n-i] (i = 0..D) is <= m[n-i] <= t[n-D], so g[n] <= t[n-D] and
// |x[n-D] * g[n]| <= ceiling. The attack is a (D+1)-sample linear-ish ramp,
// so there are no gain steps (no zipper distortion); stereo is linked
// (one gain from the louder channel) so the image does not move.
//
// This file is BOTH the pure DSP (used by the engine for level matching and
// by the tests) and the AudioWorklet module (`registerProcessor` below,
// guarded so importing it anywhere else has no side effects). It has no
// imports so it can be bundled stand-alone as a worklet.

export const LIMITER_NAME = "muestra-limiter";
/** Sample-peak ceiling of the stem bus: the same as the mastered mixes. */
export const LIMITER_CEILING_DB = -1;
export const LIMITER_LOOKAHEAD_MS = 5;
export const LIMITER_RELEASE_MS = 60;

export interface LimiterOptions {
  ceilingDb?: number;
  lookaheadMs?: number;
  releaseMs?: number;
}

/** Look-ahead length in samples for a rate (shared with the engine, which
 *  delays the mix path by the same amount so mix <-> stems stay in sync). */
export function lookaheadSamples(sampleRate: number, lookaheadMs = LIMITER_LOOKAHEAD_MS): number {
  return Math.max(1, Math.round((lookaheadMs * sampleRate) / 1000));
}

export class LookaheadLimiter {
  readonly latency: number;
  readonly ceiling: number;
  private readonly D: number;
  private readonly W: number;
  private readonly relCoef: number;
  private readonly nch: number;
  private delay: Float32Array[];
  private pos = 0; // ring position of the r average (size W)
  private dpos = 0; // ring position of the delay line (size D)
  // sliding-window minimum (monotone deque as a ring of values + indices)
  private dqV: Float64Array;
  private dqI: Float64Array;
  private dqHead = 0;
  private dqLen = 0;
  private rRing: Float64Array;
  private rSum: number;
  private rPrev = 1;
  private n = 0;
  /** Deepest gain reduction seen (linear min of g); diagnostics. */
  minGain = 1;

  constructor(sampleRate: number, channels = 2, opts: LimiterOptions = {}) {
    this.D = lookaheadSamples(sampleRate, opts.lookaheadMs);
    this.W = this.D + 1;
    this.latency = this.D;
    this.ceiling = 10 ** ((opts.ceilingDb ?? LIMITER_CEILING_DB) / 20);
    const relS = Math.max(1e-3, (opts.releaseMs ?? LIMITER_RELEASE_MS) / 1000);
    this.relCoef = 1 - Math.exp(-1 / (relS * sampleRate));
    this.nch = channels;
    this.delay = Array.from({ length: channels }, () => new Float32Array(this.D));
    this.dqV = new Float64Array(this.W + 1);
    this.dqI = new Float64Array(this.W + 1);
    this.rRing = new Float64Array(this.W).fill(1);
    this.rSum = this.W;
  }

  reset(): void {
    this.delay.forEach((d) => d.fill(0));
    this.rRing.fill(1);
    this.rSum = this.W;
    this.rPrev = 1;
    this.dqHead = 0;
    this.dqLen = 0;
    this.pos = 0;
    this.dpos = 0;
    this.n = 0;
    this.minGain = 1;
  }

  /** Process `len` frames. `inp`/`out` hold one array per channel (an input
   *  with fewer channels than the limiter reuses its last one; out may be
   *  the same arrays as inp). */
  process(inp: readonly Float32Array[], out: readonly Float32Array[], len: number): void {
    const { D, W, ceiling, relCoef, nch } = this;
    const cap = W + 1;
    for (let i = 0; i < len; i++) {
      // 1. peak of the incoming frame (linked across channels)
      let peak = 0;
      for (let c = 0; c < nch; c++) {
        const src = inp[Math.min(c, inp.length - 1)];
        const v = src ? Math.abs(src[i]) : 0;
        if (v > peak) peak = v;
      }
      const t = peak > ceiling ? ceiling / peak : 1;
      // 2. sliding-window minimum over the last W targets
      const n = this.n++;
      while (this.dqLen > 0) {
        const tail = (this.dqHead + this.dqLen - 1) % cap;
        if (this.dqV[tail] < t) break;
        this.dqLen--;
      }
      const slot = (this.dqHead + this.dqLen) % cap;
      this.dqV[slot] = t;
      this.dqI[slot] = n;
      this.dqLen++;
      while (this.dqI[this.dqHead] <= n - W) {
        this.dqHead = (this.dqHead + 1) % cap;
        this.dqLen--;
      }
      const m = this.dqV[this.dqHead];
      // 3. exponential release (instant attack to the window minimum)
      let r = this.rPrev + (1 - this.rPrev) * relCoef;
      if (m < r) r = m;
      this.rPrev = r;
      // 4. attack smoothing: moving average of the last W values of r
      const p = this.pos;
      const dp = this.dpos;
      this.rSum += r - this.rRing[p];
      this.rRing[p] = r;
      const g = Math.min(1, this.rSum / W);
      if (g < this.minGain) this.minGain = g;
      // 5. apply to the delayed signal (x[n-D]) and store the new frame
      for (let c = 0; c < nch; c++) {
        const src = inp[Math.min(c, inp.length - 1)];
        const dl = this.delay[c];
        const delayed = dl[dp]; // written D frames ago = x[n-D]
        dl[dp] = src ? src[i] : 0;
        const dst = out[Math.min(c, out.length - 1)];
        if (!dst) continue;
        let y = delayed * g;
        // numerical safety net only (exact arithmetic never reaches it)
        if (y > ceiling) y = ceiling;
        else if (y < -ceiling) y = -ceiling;
        dst[i] = y;
      }
      this.pos = p + 1 === W ? 0 : p + 1;
      this.dpos = dp + 1 === D ? 0 : dp + 1;
    }
  }
}

/** Limit whole buffers offline (level matching, tests). Output has the
 *  same length; the look-ahead delay is compensated (the tail is flushed),
 *  so output[i] lines up with input[i]. */
export function limitChannels(chs: readonly Float32Array[], sampleRate: number, opts: LimiterOptions = {}): Float32Array[] {
  const len = chs[0]?.length ?? 0;
  const lim = new LookaheadLimiter(sampleRate, chs.length, opts);
  const D = lim.latency;
  const padded = chs.map((c) => {
    const p = new Float32Array(len + D);
    p.set(c);
    return p;
  });
  const outFull = padded.map(() => new Float32Array(len + D));
  lim.process(padded, outFull, len + D);
  return outFull.map((o) => o.slice(D, D + len));
}

// ---------------------------------------------------------------- worklet

interface WorkletProcessorBase {
  readonly port: MessagePort;
}
declare const AudioWorkletProcessor: { new (options?: unknown): WorkletProcessorBase };
declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;

if (typeof registerProcessor === "function" && typeof AudioWorkletProcessor !== "undefined") {
  class LimiterProcessor extends AudioWorkletProcessor {
    private lim: LookaheadLimiter;
    constructor(options?: { processorOptions?: LimiterOptions }) {
      super(options);
      this.lim = new LookaheadLimiter(sampleRate, 2, options?.processorOptions ?? {});
    }
    process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
      const inp = inputs[0];
      const out = outputs[0];
      if (!out || out.length === 0) return true;
      if (!inp || inp.length === 0) {
        // no connected input: feed silence so the tail rings out
        const z = new Float32Array(out[0].length);
        this.lim.process([z, z], out, out[0].length);
      } else {
        this.lim.process(inp, out, out[0].length);
      }
      return true;
    }
  }
  registerProcessor(LIMITER_NAME, LimiterProcessor);
}
