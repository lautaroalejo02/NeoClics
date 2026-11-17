// Pads data (mirror of src/muestra/schemas/pads.py) and the pure logic around
// it: quantise (same math as muestra.pads.process, shared test vectors),
// latency compensation, choke bookkeeping and take merging. No Web Audio here.

import type { SliceJson, SoundJson } from "../editor/song";

export interface PadNoteJson {
  track: string;
  pitches: number[];
}

export interface PadJson {
  key: string;
  name: string;
  color?: string | null;
  sample?: string | null;
  slice?: SliceJson | null;
  note?: PadNoteJson | null;
  gain_db?: number;
  pan?: number;
  pitch_semitones?: number;
  reverse?: boolean;
  choke?: number | null;
  mode?: "oneshot" | "gate";
  velocity?: number;
  accent?: number;
  lane?: string | null;
}

export interface PadBankJson {
  id: string;
  name: string;
  pads: PadJson[];
}

export interface PadsFileJson {
  schema_version?: 0;
  active_bank: string;
  banks: PadBankJson[];
}

export type QuantizeGrid = "off" | "1/16" | "1/8" | "1/8t" | "1/16t";

export interface QuantizeJson {
  grid: QuantizeGrid;
  strength: number;
  swing: number;
}

export interface TakeEventJson {
  beat: number;
  pad: string;
  velocity: number;
  length_beats?: number | null;
  cycle: number;
}

export interface TakeJson {
  schema_version?: 0;
  id?: string;
  name: string;
  created_at?: string;
  updated_at?: string;
  status: "recording" | "done";
  bpm: number;
  origin_beat: number;
  loop_beats?: number | null;
  length_beats: number;
  bank?: string | null;
  pads: Record<string, PadJson>;
  events: TakeEventJson[];
  quantize: QuantizeJson;
  latency_ms?: number;
  commits?: { at: string; track: string; at_bar: number; repeat: number; hits: number; notes?: number }[];
  note?: string | null;
}

export const DEFAULT_QUANTIZE: QuantizeJson = { grid: "off", strength: 1, swing: 0 };

export function padSound(p: PadJson): SoundJson | null {
  if (p.note) return null;
  return { sample: p.sample ?? null, slice: p.slice ?? null, pitch_semitones: p.pitch_semitones ?? 0, reverse: !!p.reverse };
}

// ------------------------------------------------------------------ quantise

/** Presses up to a 64th note before beat 0 are an early beat 1 (= muestra.pads.process.PICKUP_BEATS). */
export const PICKUP_BEATS = 0.0625;

export const GRID_BEATS: Record<QuantizeGrid, number | null> = {
  off: null,
  "1/16": 0.25,
  "1/8": 0.5,
  "1/8t": 1 / 3,
  "1/16t": 1 / 6,
};

export const GRID_LABEL: Record<QuantizeGrid, string> = {
  off: "libre (sin cuantizar)",
  "1/16": "1/16",
  "1/8": "1/8",
  "1/8t": "1/8 tresillo",
  "1/16t": "1/16 tresillo",
};

/** Half-up rounding (= Python's _round in muestra.pads.process). */
export function roundTo(x: number, digits: number): number {
  const k = 10 ** digits;
  return Math.floor(x * k + 0.5) / k;
}

function gridPoint(i: number, step: number, swing: number, straight: boolean): number {
  return i * step + (straight && i % 2 !== 0 ? (swing * step) / 3 : 0);
}

/** `beat` moved `strength` of the way to the nearest (swung) grid point; folded
 *  into [0, wrap) for loop takes. Same numbers as muestra.pads.process.quantize_beat. */
export function quantizeBeat(beat: number, q: QuantizeJson, wrap?: number | null): number {
  const step = GRID_BEATS[q.grid];
  let target = beat;
  if (step != null && q.strength > 0) {
    const straight = q.grid === "1/16" || q.grid === "1/8";
    const i0 = Math.floor(beat / step);
    let best: [number, number] | null = null;
    for (const i of [i0 - 1, i0, i0 + 1, i0 + 2]) {
      const p = gridPoint(i, step, q.swing, straight);
      const d = Math.abs(p - beat);
      if (best == null || d < best[0] - 1e-12) best = [d, p];
    }
    target = best![1];
  }
  let out = beat + q.strength * (target - beat);
  if (wrap) out = ((((out + PICKUP_BEATS) % wrap) + wrap) % wrap) - PICKUP_BEATS;
  const r = roundTo(out, 6);
  return r === 0 ? 0 : r;
}

export interface ProcessedEvent {
  beat: number;
  rawBeat: number;
  pad: string;
  velocity: number;
  lengthBeats: number | null;
  cycle: number;
}

export interface CleanupJson {
  dedupe_ms: number;
  velocity: "as_played" | "fixed" | "even";
  velocity_value: number;
}

export const NO_CLEANUP: CleanupJson = { dedupe_ms: 0, velocity: "as_played", velocity_value: 0.8 };

/** Mirror of muestra.pads.process.process_events (dedupe -> quantise -> velocity). */
export function processEvents(take: TakeJson, q: QuantizeJson = take.quantize, c: CleanupJson = NO_CLEANUP): ProcessedEvent[] {
  let events = [...take.events].sort((a, b) => a.beat - b.beat || (a.pad < b.pad ? -1 : a.pad > b.pad ? 1 : 0));
  if (c.dedupe_ms > 0) {
    const win = (c.dedupe_ms / 1000) * (take.bpm / 60);
    const kept: TakeEventJson[] = [];
    const last = new Map<string, number>();
    for (const e of events) {
      const j = last.get(e.pad);
      if (j != null && e.beat - kept[j].beat < win - 1e-12) {
        if (e.velocity > kept[j].velocity) kept[j] = { ...kept[j], velocity: e.velocity };
        continue;
      }
      last.set(e.pad, kept.length);
      kept.push(e);
    }
    events = kept;
  }
  const mean = events.length ? events.reduce((s, e) => s + e.velocity, 0) / events.length : 0;
  const out = events.map((e) => {
    let v = e.velocity;
    if (c.velocity === "fixed") v = c.velocity_value;
    else if (c.velocity === "even") v = mean + (v - mean) * 0.5;
    v = Math.min(1, Math.max(0.01, roundTo(v, 4)));
    return { beat: quantizeBeat(e.beat, q, take.loop_beats), rawBeat: e.beat, pad: e.pad, velocity: v, lengthBeats: e.length_beats == null ? null : roundTo(e.length_beats, 6), cycle: e.cycle };
  });
  out.sort((a, b) => a.beat - b.beat || (a.pad < b.pad ? -1 : a.pad > b.pad ? 1 : 0));
  return out;
}

/** Beats one pass of a take occupies (its loop, else its length rounded up to bars). */
export function takeSpan(take: TakeJson): number {
  if (take.loop_beats) return take.loop_beats;
  return Math.max(4, Math.ceil(take.length_beats / 4 - 1e-9) * 4);
}

// ------------------------------------------------------------------ latency

export interface OutTs {
  contextTime: number;
  performanceTime: number;
}

/**
 * ctx time of the audio that was coming out of the speakers at the moment of
 * a key event (performance.now domain, `event.timeStamp`). With the context's
 * output timestamp (sample at contextTime left the device at performanceTime)
 * this is exact up to the device's reporting; without it, fall back to
 * currentTime minus the reported output latency.
 */
export function heardCtxTime(eventMs: number, ts: OutTs | null, nowCtx: number, nowPerfMs: number, outputLatencyS: number): number {
  if (ts && ts.contextTime > 0 && ts.performanceTime > 0) return ts.contextTime + (eventMs - ts.performanceTime) / 1000;
  return nowCtx - (nowPerfMs - eventMs) / 1000 - outputLatencyS;
}

/** Song seconds (loop-unwrapped: seconds since the loop start, passes counted)
 *  the human HEARD of the base at heard-ctx time `c`. */
export function heardUnwrapped(c: number, run: { when: number; offset: number; loopStart: number; busLatency: number }): number {
  return run.offset - run.loopStart + (c - run.busLatency - run.when);
}

/** Where a press lands in a take: loop takes fold into the cycle and count
 *  passes from the cycle the recording started in. */
export function placePress(
  unwrappedS: number,
  rec: { loopStartS: number; loopLenS: number | null; originBeat: number; startCycle: number },
  bpm: number,
): { beat: number; cycle: number } {
  const spb = 60 / bpm;
  if (rec.loopLenS) {
    const L = rec.loopLenS;
    const k = Math.floor(unwrappedS / L);
    const into = unwrappedS - k * L;
    let beat = into / spb;
    let cycle = k - rec.startCycle + 1;
    if (beat >= L / spb - PICKUP_BEATS) {
      // a hair before the loop start: an early beat 1 of the next pass
      beat -= L / spb;
      cycle += 1;
    }
    return { beat, cycle: Math.max(1, cycle) };
  }
  return { beat: (rec.loopStartS + unwrappedS) / spb - rec.originBeat, cycle: 1 };
}

/** Median offset of taps vs the clicks they answer (calibration helper). */
export function calibrate(tapsCtx: number[], clicksCtx: number[], maxDevS = 0.3): { offsetS: number; spreadS: number; n: number } | null {
  const d: number[] = [];
  for (const t of tapsCtx) {
    let best = Infinity;
    for (const c of clicksCtx) if (Math.abs(t - c) < Math.abs(best)) best = t - c;
    if (Math.abs(best) <= maxDevS) d.push(best);
  }
  if (d.length < 4) return null;
  // drop the first two answers (people settle in) when there are enough
  const use = d.length >= 8 ? d.slice(2) : d;
  const sorted = [...use].sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length / 2)];
  const dev = use.map((x) => Math.abs(x - med)).sort((a, b) => a - b);
  return { offsetS: med, spreadS: dev[Math.floor(dev.length / 2)], n: use.length };
}

export type LatencyLevel = "ok" | "warn" | "bad";

/** Honest verdict for the indicator (Spanish). */
export function latencyVerdict(outputS: number, calibratedS: number | null): { level: LatencyLevel; text: string } {
  const total = Math.max(outputS, calibratedS ?? 0);
  const ms = Math.round(total * 1000);
  if (total >= 0.08) {
    return {
      level: "bad",
      text: `La salida de audio tiene mucha demora (≈${ms} ms): parecen auriculares Bluetooth o una salida inalámbrica. Para tocar a tiempo necesitás auriculares con cable o los parlantes de la compu. Lo que grabes igual queda donde lo tocaste si calibrás.`,
    };
  }
  if (total >= 0.035) return { level: "warn", text: `Demora de la salida ≈${ms} ms: se puede tocar, pero se nota. Calibrá para que la grabación caiga donde tocaste.` };
  return { level: "ok", text: `Demora de la salida ≈${ms} ms: bien para tocar.` };
}

/** Per-device key for the stored calibration: the output device + its latency class. */
export function deviceKey(sinkId: string | null | undefined, outputS: number): string {
  return `${sinkId || "default"}|${Math.round(outputS * 100)}`;
}

// ------------------------------------------------------------------ choke

export interface LiveVoice {
  id: number;
  pad: string;
  group: number | null;
  start: number;
}

/** Voices a new press of a pad in `group` cuts: every voice of the same group
 *  that started strictly earlier (the renderer's rule). No group: none. */
export function chokeTargets(active: LiveVoice[], group: number | null | undefined, when: number): number[] {
  if (!group) return [];
  return active.filter((v) => v.group === group && v.start < when - 1e-9).map((v) => v.id);
}

// ------------------------------------------------------------------ takes

/** Overdub: a pass adds its events (sorted); nothing is replaced. */
export function mergePass(take: TakeJson, events: TakeEventJson[]): TakeJson {
  const all = [...take.events, ...events].sort((a, b) => a.beat - b.beat || a.cycle - b.cycle);
  return { ...take, events: all };
}

/** "Deshacer la última pasada": drop the events of the highest cycle. */
export function undoLastPass(take: TakeJson): { take: TakeJson; removed: number; cycle: number | null } {
  if (!take.events.length) return { take, removed: 0, cycle: null };
  const last = Math.max(...take.events.map((e) => e.cycle));
  const kept = take.events.filter((e) => e.cycle !== last);
  return { take: { ...take, events: kept }, removed: take.events.length - kept.length, cycle: last };
}

/** Pad snapshot of a take: every pad played (or all pads of the bank). */
export function snapshotPads(pads: PadJson[]): Record<string, PadJson> {
  const out: Record<string, PadJson> = {};
  for (const p of pads) out[p.key] = { ...p };
  return out;
}

/** Song beats (1-indexed) of a take placed at a bar, `repeat` passes (display). */
export function placedBeats(take: TakeJson, atBar: number, repeat: number, q: QuantizeJson = take.quantize): { beat: number; pad: string; velocity: number }[] {
  const span = takeSpan(take);
  const out: { beat: number; pad: string; velocity: number }[] = [];
  const ev = processEvents(take, q);
  for (let k = 0; k < repeat; k++)
    for (const e of ev) {
      if (e.beat < -PICKUP_BEATS - 1e-9 || e.beat >= span - 1e-9) continue;
      out.push({ beat: roundTo((atBar - 1) * 4 + k * span + e.beat + 1, 6), pad: e.pad, velocity: e.velocity });
    }
  return out;
}
