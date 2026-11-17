// The stem-bus limiter (finding 1): pure DSP, so we can prove the ceiling.
import { describe, expect, it } from "vitest";
import { LIMITER_CEILING_DB, LookaheadLimiter, limitChannels, lookaheadSamples } from "../src/audio/limiter";

const SR = 48000;
const CEIL = 10 ** (LIMITER_CEILING_DB / 20);

/** Deterministic PRNG so failures reproduce. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** Kick-shaped transients (fast decay sines) on a loud bed, up to ±3. */
function loudSignal(seconds: number, seed = 1): Float32Array[] {
  const r = rng(seed);
  const n = Math.round(seconds * SR);
  const chs = [new Float32Array(n), new Float32Array(n)];
  const beat = Math.round(SR * 0.23);
  for (let i = 0; i < n; i++) {
    const t = (i % beat) / SR;
    const kick = Math.sin(2 * Math.PI * (55 + 90 * Math.exp(-t * 40)) * t) * Math.exp(-t * 18) * 3;
    const bed = 0.4 * Math.sin((2 * Math.PI * 220 * i) / SR) + 0.1 * (r() - 0.5);
    chs[0][i] = kick + bed;
    chs[1][i] = kick * 0.9 - bed;
  }
  return chs;
}

describe("LookaheadLimiter", () => {
  it("never exceeds the ceiling, even on +10 dB transients", () => {
    const x = loudSignal(6);
    const y = limitChannels(x, SR);
    let peakIn = 0;
    let peakOut = 0;
    for (let c = 0; c < 2; c++) {
      for (let i = 0; i < x[c].length; i++) {
        peakIn = Math.max(peakIn, Math.abs(x[c][i]));
        peakOut = Math.max(peakOut, Math.abs(y[c][i]));
      }
    }
    expect(peakIn).toBeGreaterThan(2);
    expect(peakOut).toBeLessThanOrEqual(CEIL);
    expect(peakOut).toBeGreaterThan(CEIL * 0.95); // and it is not just attenuating everything
  });

  it("is bit-transparent below the ceiling (after the look-ahead delay)", () => {
    const n = SR;
    const x = new Float32Array(n);
    for (let i = 0; i < n; i++) x[i] = 0.5 * Math.sin((2 * Math.PI * 440 * i) / SR);
    const y = limitChannels([x], SR)[0];
    for (let i = 0; i < n; i++) expect(y[i]).toBe(x[i]);
  });

  it("holds the ceiling for block-wise processing at any block size", () => {
    const x = loudSignal(2, 7);
    const lim = new LookaheadLimiter(SR, 2);
    const out = [new Float32Array(x[0].length), new Float32Array(x[0].length)];
    for (let i = 0; i < x[0].length; ) {
      const len = Math.min(1 + ((i * 7) % 300), x[0].length - i); // ragged blocks (1..300)
      lim.process([x[0].subarray(i, i + len), x[1].subarray(i, i + len)], [out[0].subarray(i, i + len), out[1].subarray(i, i + len)], len);
      i += len;
    }
    for (const ch of out) for (const v of ch) expect(Math.abs(v)).toBeLessThanOrEqual(CEIL);
  });

  it("releases: a single loud hit does not duck the music for long", () => {
    const n = SR;
    const x = new Float32Array(n).fill(0.3);
    x[10000] = 4; // one-sample spike
    const y = limitChannels([x], SR)[0];
    expect(Math.abs(y[10000])).toBeLessThanOrEqual(CEIL);
    // 400 ms later the level is back within 0.3 dB of the original
    expect(y[10000 + Math.round(0.4 * SR)] / 0.3).toBeGreaterThan(0.97);
    // and before the spike the gain ramps down (attack), not a step
    expect(y[10000 - lookaheadSamples(SR) - 5]).toBe(Math.fround(0.3));
  });

  it("links the channels (a hit on one side moves both equally: no image shift)", () => {
    const n = 4000;
    const l = new Float32Array(n).fill(0.2);
    const r = new Float32Array(n).fill(0.2);
    l[2000] = 3;
    const [yl, yr] = limitChannels([l, r], SR);
    expect(yl[1990] / 0.2).toBeCloseTo(yr[1990] / 0.2, 6);
    expect(yr[1990]).toBeLessThan(0.2); // right was ducked too
  });

  it("handles mono input and silence", () => {
    const z = limitChannels([new Float32Array(1000)], SR)[0];
    expect(Math.max(...z.map(Math.abs))).toBe(0);
  });
});
