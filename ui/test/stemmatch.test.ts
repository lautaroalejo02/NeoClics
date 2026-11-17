// Level matching through the stem-bus limiter (finding 1).
import { describe, expect, it } from "vitest";
import { limitChannels } from "../src/audio/limiter";
import { matchStemGainLimited, sumStems } from "../src/audio/stemmatch";
import { rms } from "../src/audio/signal";

const SR = 48000;
const db = (x: number) => 20 * Math.log10(x);

/** A real-shaped render: hot kick transients + bass + pad stems (pre-master),
 *  and a "mastered mix" = the sum squashed to a -1 dBFS ceiling at a similar
 *  loudness (what the master chain does). */
function fixture(seconds = 8) {
  const n = SR * seconds;
  const beat = Math.round(SR * 0.23);
  const kick = [new Float32Array(n), new Float32Array(n)];
  const bass = [new Float32Array(n), new Float32Array(n)];
  const pad = [new Float32Array(n), new Float32Array(n)];
  for (let i = 0; i < n; i++) {
    const t = (i % beat) / SR;
    const k = Math.sin(2 * Math.PI * (50 + 100 * Math.exp(-t * 45)) * t) * Math.exp(-t * 14) * 1.1;
    const b = 0.5 * Math.sin((2 * Math.PI * 55 * i) / SR) * (0.6 + 0.4 * Math.sin((2 * Math.PI * 2 * i) / SR));
    const p = 0.12 * Math.sin((2 * Math.PI * 440 * i) / SR) + 0.1 * Math.sin((2 * Math.PI * 660.5 * i) / SR);
    kick[0][i] = kick[1][i] = k;
    bass[0][i] = bass[1][i] = b;
    pad[0][i] = p;
    pad[1][i] = -p * 0.9;
  }
  const stems = [kick, bass, pad];
  const sum = sumStems(stems, 0, n);
  // the master: gain up then limit to -1 dBFS (peaks flattened)
  const hot = sum.map((c) => c.map((v) => v * 1.15));
  const mix = limitChannels(hot, SR, { releaseMs: 120 });
  return { stems, sum, mix };
}

describe("matchStemGainLimited", () => {
  const { stems, sum, mix } = fixture();
  const m = matchStemGainLimited(mix, stems, SR);

  it("the fixture is real-shaped: the RMS-matched stem sum would clip by several dB", () => {
    const g = rms(mix) / rms(sum);
    let peak = 0;
    for (const c of sum) for (const v of c) peak = Math.max(peak, Math.abs(v * g));
    expect(db(peak)).toBeGreaterThan(2);
  });

  it("matches the LIMITED sum to the mix level (<= 0.1 dB), peak at the limiter ceiling", () => {
    expect(Math.abs(m.rmsDeltaDb)).toBeLessThan(0.1);
    expect(m.peakDb).toBeLessThanOrEqual(-0.99);
    expect(m.peakDb).toBeGreaterThan(-1.1);
  });

  it("is a little hotter than a plain RMS match (it pays back what the limiter takes)", () => {
    const plain = rms(mix) / rms(sum);
    expect(m.gain).toBeGreaterThan(plain);
    expect(db(m.gain / plain)).toBeLessThan(3);
  });

  it("silent mix or no stems → unity", () => {
    expect(matchStemGainLimited([new Float32Array(1000)], stems.map((s) => s.map((c) => c.subarray(0, 1000))), SR).gain).toBe(1);
    expect(matchStemGainLimited(mix, [], SR).gain).toBe(1);
  });
});
