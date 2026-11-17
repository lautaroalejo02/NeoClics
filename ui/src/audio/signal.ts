// Small signal helpers that work on raw channel arrays (so they are
// testable without an AudioContext).

export type Channels = readonly Float32Array[];

/** Max samples inspected per measurement: keeps cost bounded on long files. */
const MAX_SAMPLES = 400_000;

/** RMS over all channels (strided sampling for long buffers). */
export function rms(chs: Channels): number {
  return rmsOfSum([chs]);
}

/** RMS of the sample-wise SUM of several equally long multichannel signals.
 *  A mono signal is reused for every channel of a stereo one. */
export function rmsOfSum(signals: readonly Channels[]): number {
  if (signals.length === 0) return 0;
  const len = Math.min(...signals.map((s) => s[0]?.length ?? 0));
  if (len === 0) return 0;
  const nch = Math.max(...signals.map((s) => s.length));
  const stride = Math.max(1, Math.floor((len * nch) / MAX_SAMPLES));
  let acc = 0;
  let n = 0;
  for (let c = 0; c < nch; c++) {
    const arrs = signals.map((s) => s[Math.min(c, s.length - 1)]);
    for (let i = 0; i < len; i += stride) {
      let v = 0;
      for (let k = 0; k < arrs.length; k++) v += arrs[k][i];
      acc += v * v;
      n++;
    }
  }
  return n ? Math.sqrt(acc / n) : 0;
}

/** Linear gain that makes `from` as loud (RMS) as `to`, clamped to ±18 dB.
 *  Silent inputs give unity. */
export function matchGain(toRms: number, fromRms: number): number {
  if (!(fromRms > 1e-7) || !(toRms > 1e-7)) return 1;
  const g = toRms / fromRms;
  const lim = 10 ** (18 / 20);
  return Math.min(lim, Math.max(1 / lim, g));
}

/** Min/max peaks per bucket, merged across channels (waveform drawing). */
export function computePeaks(chs: Channels, buckets: number): Float32Array {
  const len = chs[0]?.length ?? 0;
  const out = new Float32Array(buckets * 2);
  if (len === 0) return out;
  const per = len / buckets;
  for (let b = 0; b < buckets; b++) {
    const s = Math.floor(b * per);
    const e = Math.max(s + 1, Math.min(len, Math.floor((b + 1) * per)));
    let mn = 0;
    let mx = 0;
    for (const ch of chs) {
      for (let i = s; i < e; i++) {
        const v = ch[i];
        if (v < mn) mn = v;
        else if (v > mx) mx = v;
      }
    }
    out[b * 2] = mn;
    out[b * 2 + 1] = mx;
  }
  return out;
}

/** Convert the optional /peaks payload ([ch][bucket] = [min,max]) into the
 *  same merged Float32Array layout as computePeaks. */
export function peaksFromPayload(peaks: [number, number][][]): Float32Array {
  const n = peaks[0]?.length ?? 0;
  const out = new Float32Array(n * 2);
  for (let b = 0; b < n; b++) {
    let mn = 0;
    let mx = 0;
    for (const ch of peaks) {
      const p = ch[b];
      if (!p) continue;
      if (p[0] < mn) mn = p[0];
      if (p[1] > mx) mx = p[1];
    }
    out[b * 2] = mn;
    out[b * 2 + 1] = mx;
  }
  return out;
}
