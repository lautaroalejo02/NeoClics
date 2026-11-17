// Source-grid math for the source view (mirrors project.chop_times: one grid).

import type { Analysis } from "../api/types";
import { round6 } from "./ops";
import type { ChopJson } from "./song";


/** Seconds of a fractional position along the analysed beat grid. */
export function beatTime(beats: number[], pos: number): number {
  if (!beats.length) return 0;
  if (beats.length === 1) return beats[0];
  const i = Math.max(0, Math.min(beats.length - 2, Math.floor(pos)));
  return beats[i] + (pos - i) * (beats[i + 1] - beats[i]);
}

/** Fractional grid position of a time (inverse of beatTime). */
export function timeBeat(beats: number[], t: number): number {
  if (beats.length < 2) return 0;
  let lo = 0;
  let hi = beats.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (beats[mid] <= t) lo = mid;
    else hi = mid;
  }
  const i = t < beats[0] ? 0 : t > beats[beats.length - 1] ? beats.length - 2 : lo;
  return i + (t - beats[i]) / (beats[i + 1] - beats[i]);
}

/** Beat index nearest each downbeat (the chop's "one grid", as the renderer). */
export function downbeatIndex(a: Pick<Analysis, "beats_s" | "downbeats_s">, d: number): number {
  const t = a.downbeats_s[d - 1];
  let best = 0;
  for (let i = 1; i < a.beats_s.length; i++) if (Math.abs(a.beats_s[i] - t) < Math.abs(a.beats_s[best] - t)) best = i;
  return best;
}

/** [startPos, endPos) of a chop on the source grid. */
export function chopSpan(a: Pick<Analysis, "beats_s" | "downbeats_s">, c: ChopJson): [number, number] {
  const s = downbeatIndex(a, c.start_downbeat) + (c.start_offset_beats ?? 0);
  return [s, s + c.length_beats];
}

/** start_downbeat + offset that place a chop at grid position `pos`. */
export function anchorFor(a: Pick<Analysis, "beats_s" | "downbeats_s">, pos: number): { start_downbeat: number; start_offset_beats: number } {
  let d = 1;
  for (let k = 1; k <= a.downbeats_s.length; k++) if (downbeatIndex(a, k) <= pos + 1e-6) d = k;
  return { start_downbeat: d, start_offset_beats: round6(pos - downbeatIndex(a, d)) };
}

