// Level matching of the stem sum to the mastered mix (WP14 finding 1).
//
// The stems are pre-master: summed and RMS-matched to mix.wav they are
// +3..+10 dB hot and have a much higher crest, so the stem bus limits them.
// Matching must therefore use the LIMITED level: the gain G is chosen so
// that bus(G * sum) has the same RMS as the mix, otherwise the limiter
// would make stem mode a little quieter than the mix and the mix <-> stems
// toggle would jump.

import { limitChannels } from "./limiter";
import { matchGain, rms, type Channels } from "./signal";
import { createStemBus, loadLimiterModule, type BusKind } from "./stembus";

/** Longest excerpt measured (s). Long songs use a centred window. */
export const MATCH_MAX_S = 40;
const MAX_GAIN = 10 ** (18 / 20);

function excerpt(chs: Channels, start: number, len: number): Float32Array[] {
  return chs.map((c) => c.subarray(start, start + len));
}

/** Sample-wise sum of stems over an excerpt (mono stems feed every channel). */
export function sumStems(stems: readonly Channels[], start: number, len: number): Float32Array[] {
  const nch = Math.max(1, ...stems.map((s) => s.length));
  const out = Array.from({ length: nch }, () => new Float32Array(len));
  for (const s of stems) {
    for (let c = 0; c < nch; c++) {
      const src = s[Math.min(c, s.length - 1)];
      const o = out[c];
      for (let i = 0; i < len; i++) o[i] += src[start + i];
    }
  }
  return out;
}

function window(len: number, sampleRate: number): { start: number; len: number } {
  const max = Math.round(MATCH_MAX_S * sampleRate);
  if (len <= max) return { start: 0, len };
  return { start: Math.floor((len - max) / 2), len: max };
}

export interface StemMatch {
  /** Linear gain to put in front of the stem bus. */
  gain: number;
  /** Diagnostics: peak/RMS of the matched, limited sum vs the mix (dB). */
  rmsDeltaDb: number;
  peakDb: number;
  /** How many dB the limiter takes off the loudest moments of the sum. */
  iterations: number;
}

/** Pure/offline matching through the worklet limiter's own DSP. */
export function matchStemGainLimited(mix: Channels, stems: readonly Channels[], sampleRate: number): StemMatch {
  const len = Math.min(mix[0]?.length ?? 0, ...stems.map((s) => s[0]?.length ?? 0));
  if (len === 0 || stems.length === 0) return { gain: 1, rmsDeltaDb: 0, peakDb: -Infinity, iterations: 0 };
  const w = window(len, sampleRate);
  const target = rms(excerpt(mix, w.start, w.len));
  const sum = sumStems(stems, w.start, w.len);
  let g = matchGain(target, rms(sum));
  if (g === 1 && !(target > 1e-7)) return { gain: 1, rmsDeltaDb: 0, peakDb: -Infinity, iterations: 0 };
  let it = 0;
  let out: Float32Array[] = sum;
  for (; it < 5; it++) {
    const scaled = sum.map((c) => {
      const o = new Float32Array(c.length);
      for (let i = 0; i < c.length; i++) o[i] = c[i] * g;
      return o;
    });
    out = limitChannels(scaled, sampleRate);
    const r = rms(out);
    if (!(r > 1e-9)) break;
    const err = target / r;
    if (Math.abs(20 * Math.log10(err)) < 0.05) break;
    g = Math.min(MAX_GAIN, Math.max(1 / MAX_GAIN, g * err));
  }
  let peak = 0;
  for (const c of out) for (let i = 0; i < c.length; i++) peak = Math.max(peak, Math.abs(c[i]));
  return {
    gain: g,
    rmsDeltaDb: 20 * Math.log10(Math.max(rms(out), 1e-9) / Math.max(target, 1e-9)),
    peakDb: 20 * Math.log10(Math.max(peak, 1e-9)),
    iterations: it,
  };
}

/** Matching for the NATIVE bus: its compressor has browser-defined makeup
 *  gain, so measure the real chain in an OfflineAudioContext (same nodes as
 *  the live graph) and trim. Falls back to the plain RMS match when offline
 *  rendering is not available. Async: rendering happens off the main thread. */
export async function matchStemGainNative(mix: Channels, stems: readonly AudioBuffer[], sampleRate: number): Promise<number> {
  const len = Math.min(mix[0]?.length ?? 0, ...stems.map((s) => s.length));
  const chans = (b: AudioBuffer) => Array.from({ length: b.numberOfChannels }, (_, i) => b.getChannelData(i));
  const w = window(len, sampleRate);
  const target = rms(excerpt(mix, w.start, w.len));
  const analytic = matchGain(target, rms(sumStems(stems.map(chans), w.start, w.len)));
  const Off = (globalThis as { OfflineAudioContext?: typeof OfflineAudioContext }).OfflineAudioContext;
  if (!Off || !(target > 1e-7)) return analytic;
  try {
    let g = analytic;
    for (let it = 0; it < 2; it++) {
      const r = await renderChainRms(Off, "native", stems, w, sampleRate, g);
      if (!(r > 1e-9)) break;
      const err = target / r;
      g = Math.min(MAX_GAIN, Math.max(1 / MAX_GAIN, g * err));
      if (Math.abs(20 * Math.log10(err)) < 0.05) break;
    }
    return g;
  } catch {
    return analytic;
  }
}

async function renderChainRms(
  Off: typeof OfflineAudioContext,
  kind: BusKind,
  stems: readonly AudioBuffer[],
  w: { start: number; len: number },
  sampleRate: number,
  gain: number,
): Promise<number> {
  const ctx = new Off(2, w.len, sampleRate);
  if (kind === "worklet" && !(await loadLimiterModule(ctx))) throw new Error("no worklet");
  const bus = createStemBus(ctx, kind);
  const g = ctx.createGain();
  g.gain.value = gain;
  g.connect(bus.input);
  bus.output.connect(ctx.destination);
  for (const b of stems) {
    const s = ctx.createBufferSource();
    s.buffer = b;
    s.connect(g);
    s.start(0, w.start / sampleRate, w.len / sampleRate);
  }
  const out = await ctx.startRendering();
  // skip the bus latency at the head
  const skip = Math.min(out.length >> 1, Math.round(bus.latencyS * sampleRate) + 16);
  return rms([out.getChannelData(0).subarray(skip), out.getChannelData(1).subarray(skip)]);
}
