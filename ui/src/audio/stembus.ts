// The stem bus (WP14 finding 1): what the pre-master stem sum passes through
// before it reaches the speakers, so that mute/solo/fader moves can never
// clip the output.
//
//   worklet  look-ahead brickwall limiter (src/audio/limiter.ts) as an
//            AudioWorklet. Exactly -1 dBFS peak, the closest to the mastered
//            mixes on the 20 real renders (see ui/README.md, scripts/stem-bench.mjs).
//   native   DynamicsCompressorNode (fast ratio-20 "limiter" setting) followed
//            by a WaveShaper that hard-bounds the output at 0.99. Used where
//            AudioWorklet does not exist: AudioWorklet needs a SECURE context,
//            so a phone that opens http://192.168.x.y:8765 over plain Wi-Fi
//            gets this one. Not as precise, but it cannot clip either.
//
// Both paths report their latency; the engine delays the mix path by the
// same amount so the mix <-> stems crossfade stays phase-aligned.

import limiterUrl from "./limiter?worker&url";
import { LIMITER_NAME, lookaheadSamples } from "./limiter";

export type BusKind = "worklet" | "native";

export interface StemBus {
  kind: BusKind;
  input: AudioNode;
  output: AudioNode;
  /** Delay (s) the bus adds; the mix path is delayed by the same amount. */
  latencyS: number;
}

/** Hard bound of the native chain (the shaper's flat top): < 0 dBFS. */
export const NATIVE_BOUND = 0.99;
const NATIVE_KNEE = 0.891; // -1 dBFS: below this the shaper is transparent
/** Chrome/WebKit/Gecko compressors all pre-delay by 6 ms. */
const NATIVE_LATENCY_S = 0.006;

export const NATIVE_COMPRESSOR = { threshold: -6, knee: 2, ratio: 20, attack: 0.001, release: 0.1 } as const;

/** Transfer curve of the safety shaper: identity up to -1 dBFS, then a
 *  tanh shoulder that never exceeds NATIVE_BOUND (and the curve end clamps
 *  anything beyond +-1 to it). */
export function softCeilingCurve(n = 2049): Float32Array<ArrayBuffer> {
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    const a = Math.abs(x);
    const y = a <= NATIVE_KNEE ? a : NATIVE_KNEE + (NATIVE_BOUND - NATIVE_KNEE) * Math.tanh((a - NATIVE_KNEE) / (NATIVE_BOUND - NATIVE_KNEE));
    curve[i] = Math.sign(x) * y;
  }
  return curve;
}

/** Register the limiter AudioWorklet module on `ctx`. Resolves false (never
 *  rejects) where AudioWorklet is missing (insecure context, old browser)
 *  or the module cannot load. */
export async function loadLimiterModule(ctx: BaseAudioContext): Promise<boolean> {
  const aw = (ctx as BaseAudioContext & { audioWorklet?: AudioWorklet }).audioWorklet;
  if (!aw || typeof aw.addModule !== "function" || typeof AudioWorkletNode === "undefined") return false;
  try {
    await aw.addModule(limiterUrl);
    return true;
  } catch {
    return false;
  }
}

export function createStemBus(ctx: BaseAudioContext, kind: BusKind): StemBus {
  if (kind === "worklet") {
    const node = new AudioWorkletNode(ctx, LIMITER_NAME, {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [2],
      channelCount: 2,
      channelCountMode: "explicit",
    });
    return { kind, input: node, output: node, latencyS: lookaheadSamples(ctx.sampleRate) / ctx.sampleRate };
  }
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = NATIVE_COMPRESSOR.threshold;
  comp.knee.value = NATIVE_COMPRESSOR.knee;
  comp.ratio.value = NATIVE_COMPRESSOR.ratio;
  comp.attack.value = NATIVE_COMPRESSOR.attack;
  comp.release.value = NATIVE_COMPRESSOR.release;
  const shaper = ctx.createWaveShaper();
  shaper.curve = softCeilingCurve();
  shaper.oversample = "2x";
  comp.connect(shaper);
  return { kind, input: comp, output: shaper, latencyS: NATIVE_LATENCY_S };
}
