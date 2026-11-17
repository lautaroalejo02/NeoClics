// The editor's arrangement player: plays the WORKING song itself from its
// pieces (chop materials, one-shots, instrument stems, per-track wet renders),
// scheduled on the Web Audio clock by a look-ahead scheduler that does not
// depend on React. Edits swap the event list; already-scheduled events that
// no longer exist are faded out and new ones inside the scheduled window are
// added, so playback never restarts.
//
// Per track:   dry events ─► dryIn ─► [filters ▸ gain fx ▸ sidechain≈] ─► dryOut(1/√2) ┐
//              wet stem   ─────────────────────────────────────────────► wetOut ───────┴► fader(L,R) ─► bus
// The fader applies gain_db and the renderer's pan law RELATIVE to centre
// (wet stems are rendered at centre, -3 dB law included; dry signal is
// pre-scaled by 1/√2 so it gets the full law). See muestra.editor.pieces.

import type { FxJson } from "../editor/song";

export interface ArrEvent {
  /** stable identity: unchanged events keep their key across edits */
  key: string;
  track: string;
  /** lane bus for drum hits (gain/pan of the lane) */
  lane?: string;
  /** song seconds */
  time: number;
  buffer: AudioBuffer;
  /** seconds into the buffer */
  offset: number;
  /** audible length (s), not counting the tail */
  duration: number;
  /** extra seconds after duration that carry the seam crossfade */
  tail: number;
  gain: number;
  reverse?: boolean;
  /** fade-in shape: "eq" = equal-power (seam), "cos" = raised cosine, null = none */
  fadeIn: { len: number; shape: "eq" | "cos" } | null;
  fadeOut: { len: number; shape: "eq" | "cos"; inTail: boolean } | null;
  /** long material (clip passes) restarts mid-way if moved under the playhead */
  sustained?: boolean;
}

export interface ArrLane {
  gainDb: number;
  pan: number;
}

export interface ArrTrack {
  id: string;
  gainDb: number;
  pan: number;
  mute: boolean;
  solo: boolean;
  /** wet stem (true sound with fx) used instead of the dry events */
  wet: AudioBuffer | null;
  /** instrument dry stem (when not wet) */
  dryStem: AudioBuffer | null;
  lanes: Record<string, ArrLane>;
  /** fx approximated on the dry path */
  fx: FxJson[];
  /** automation values over song seconds for dry-path params, by "<fxid>.<param>" */
  automation: Record<string, (songS: number) => number>;
}

export interface Arrangement {
  duration: number;
  tracks: ArrTrack[];
  events: ArrEvent[];
  /** sidechain triggers (song seconds) by "track" and "track.lane" */
  triggers: Record<string, number[]>;
}

const LOOKAHEAD_S = 0.2;
const TICK_MS = 25;
const XF_S = 0.02;
const STOP_FADE_S = 0.004;

function equalPower(fadeIn: boolean, n = 64): Float32Array {
  const c = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = ((i + 0.5) / n) * (Math.PI / 2);
    c[i] = fadeIn ? Math.sin(t) : Math.cos(t);
  }
  return c;
}

function raisedCos(fadeIn: boolean, n = 64): Float32Array {
  const c = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const v = 0.5 - 0.5 * Math.cos((Math.PI * i) / (n - 1));
    c[i] = fadeIn ? v : 1 - v;
  }
  return c;
}

function scaled(c: Float32Array, k: number): Float32Array {
  const o = new Float32Array(c.length);
  for (let i = 0; i < c.length; i++) o[i] = c[i] * k;
  return o;
}

function holdAt(p: AudioParam, t: number): void {
  const anyP = p as AudioParam & { cancelAndHoldAtTime?: (t: number) => void };
  if (typeof anyP.cancelAndHoldAtTime === "function") anyP.cancelAndHoldAtTime(t);
  else {
    const v = p.value;
    p.cancelScheduledValues(t);
    p.setValueAtTime(v, t);
  }
}

/** Glide to `v` (no zipper noise); a node that has never been set jumps
 *  straight there (no leak from the default gain of 1 at a fresh start). */
const touched = new WeakSet<AudioParam>();
function smooth(p: AudioParam, v: number, now: number): void {
  if (!touched.has(p)) {
    touched.add(p);
    p.cancelScheduledValues(0);
    p.value = v;
    p.setValueAtTime(v, now);
    return;
  }
  holdAt(p, now);
  p.setTargetAtTime(v, now, 0.006);
}

/** Butterworth section Qs for an even order (same design as render/dsp.py). */
export function butterworthQs(order: number): number[] {
  const qs: number[] = [];
  for (let k = 0; k < order / 2; k++) qs.push(1 / (2 * Math.cos(((2 * k + 1) * Math.PI) / (2 * order))));
  return qs;
}

/** Renderer pan law (render/automation.py pan_gain): L·cos(a), R·sin(a), a=(pan+1)π/4. */
export function panLaw(pan: number): [number, number] {
  const a = ((pan + 1) * Math.PI) / 4;
  return [Math.cos(a), Math.sin(a)];
}

interface TrackNodes {
  dryIn: GainNode;
  dryOut: GainNode;
  wetOut: GainNode;
  faderL: GainNode;
  faderR: GainNode;
  merger: ChannelMergerNode;
  splitter: ChannelSplitterNode;
  filters: { fx: FxJson; nodes: BiquadFilterNode[] }[];
  gains: { fx: FxJson; node: GainNode }[];
  sidechain: { fx: FxJson; node: GainNode } | null;
  lanes: Map<string, { l: GainNode; r: GainNode; split: ChannelSplitterNode; merge: ChannelMergerNode; key: string }>;
  fxKey: string;
  wetBuf: AudioBuffer | null;
  dryStemBuf: AudioBuffer | null;
}

interface Scheduled {
  key: string;
  track: string;
  src: AudioBufferSourceNode;
  gain: GainNode;
  start: number;
  end: number;
}

interface Pass {
  ctx0: number;
  song0: number;
  index: number;
}

interface StemSource {
  src: AudioBufferSourceNode;
  gain: GainNode;
  buffer: AudioBuffer;
}

export class ArrangementPlayer {
  readonly ctx: BaseAudioContext;
  readonly output: GainNode;
  private arr: Arrangement | null = null;
  private nodes = new Map<string, TrackNodes>();
  private scheduled = new Map<string, Scheduled>();
  private stems = new Map<string, StemSource>();
  private reversed = new WeakMap<AudioBuffer, AudioBuffer>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private run: { when: number; offset: number; loopStart: number; loopEnd: number } | null = null;
  private passes: Pass[] = [];
  private cursor = 0;
  private passCount = 0;
  /** diagnostics: ctx time an event key was last scheduled at */
  lastScheduled = new Map<string, number>();
  /** called after each scheduling tick (tests / measurements) */
  onTick: (() => void) | null = null;

  constructor(ctx: BaseAudioContext, dest: AudioNode) {
    this.ctx = ctx;
    this.output = ctx.createGain();
    this.output.connect(dest);
  }

  // ------------------------------------------------------------ graph

  private trackNodes(t: ArrTrack): TrackNodes {
    let n = this.nodes.get(t.id);
    const fxKey = JSON.stringify(t.fx.filter((f) => ["highpass", "lowpass", "gain", "sidechain"].includes(f.type)));
    if (n && n.fxKey === fxKey) return n;
    const ctx = this.ctx;
    if (!n) {
      const dryIn = ctx.createGain();
      const dryOut = ctx.createGain();
      dryOut.gain.value = Math.SQRT1_2;
      const wetOut = ctx.createGain();
      wetOut.gain.value = 0;
      const splitter = ctx.createChannelSplitter(2);
      const merger = ctx.createChannelMerger(2);
      const faderL = ctx.createGain();
      const faderR = ctx.createGain();
      dryOut.connect(splitter);
      wetOut.connect(splitter);
      splitter.connect(faderL, 0);
      splitter.connect(faderR, 1);
      faderL.connect(merger, 0, 0);
      faderR.connect(merger, 0, 1);
      merger.connect(this.output);
      n = { dryIn, dryOut, wetOut, faderL, faderR, merger, splitter, filters: [], gains: [], sidechain: null, lanes: new Map(), fxKey: "", wetBuf: null, dryStemBuf: null };
      this.nodes.set(t.id, n);
    }
    // (re)build the dry fx chain
    n.dryIn.disconnect();
    for (const f of n.filters) f.nodes.forEach((b) => b.disconnect());
    for (const g of n.gains) g.node.disconnect();
    n.sidechain?.node.disconnect();
    n.filters = [];
    n.gains = [];
    n.sidechain = null;
    let tail: AudioNode = n.dryIn;
    for (const fx of t.fx) {
      if (fx.type === "highpass" || fx.type === "lowpass") {
        const order = Number(fx.order ?? 4);
        const nodes = butterworthQs(order).map((q) => {
          const b = ctx.createBiquadFilter();
          b.type = fx.type as BiquadFilterType;
          b.Q.value = 20 * Math.log10(q); // Web Audio's lowpass/highpass Q is in dB
          b.frequency.value = Number(fx.cutoff_hz);
          return b;
        });
        for (const b of nodes) {
          tail.connect(b);
          tail = b;
        }
        n.filters.push({ fx, nodes });
      } else if (fx.type === "gain") {
        const g = ctx.createGain();
        g.gain.value = 10 ** (Number(fx.gain_db) / 20);
        tail.connect(g);
        tail = g;
        n.gains.push({ fx, node: g });
      } else if (fx.type === "sidechain" && !n.sidechain) {
        const g = ctx.createGain();
        tail.connect(g);
        tail = g;
        n.sidechain = { fx, node: g };
      }
    }
    tail.connect(n.dryOut);
    n.fxKey = fxKey;
    return n;
  }

  private laneNode(n: TrackNodes, lane: string, mix: ArrLane | undefined): AudioNode {
    const key = `${mix?.gainDb ?? 0}|${mix?.pan ?? 0}`;
    let l = n.lanes.get(lane);
    if (!l) {
      const split = this.ctx.createChannelSplitter(2);
      const merge = this.ctx.createChannelMerger(2);
      const lg = this.ctx.createGain();
      const rg = this.ctx.createGain();
      split.connect(lg, 0);
      split.connect(rg, 1);
      lg.connect(merge, 0, 0);
      rg.connect(merge, 0, 1);
      merge.connect(n.dryIn);
      l = { l: lg, r: rg, split, merge, key: "" };
      n.lanes.set(lane, l);
    }
    if (l.key !== key) {
      const g = 10 ** ((mix?.gainDb ?? 0) / 20);
      const [cl, cr] = panLaw(mix?.pan ?? 0);
      smooth(l.l.gain, g * cl, this.ctx.currentTime);
      smooth(l.r.gain, g * cr, this.ctx.currentTime);
      l.key = key;
    }
    return l.split;
  }

  private applyMix(): void {
    if (!this.arr) return;
    const now = this.ctx.currentTime;
    const anySolo = this.arr.tracks.some((t) => t.solo);
    for (const t of this.arr.tracks) {
      const n = this.trackNodes(t);
      const on = !t.mute && (!anySolo || t.solo);
      const g = on ? 10 ** (t.gainDb / 20) : 0;
      const [cl, cr] = panLaw(t.pan);
      smooth(n.faderL.gain, g * cl * Math.SQRT2, now);
      smooth(n.faderR.gain, g * cr * Math.SQRT2, now);
      for (const f of n.filters) {
        const auto = t.automation[`${f.fx.id}.cutoff_hz`];
        if (!auto) for (const b of f.nodes) smooth(b.frequency, Number(f.fx.cutoff_hz), now);
      }
      for (const gfx of n.gains) {
        if (!t.automation[`${gfx.fx.id}.gain_db`]) smooth(gfx.node.gain, 10 ** (Number(gfx.fx.gain_db) / 20), now);
      }
    }
  }

  // ------------------------------------------------------------ arrangement

  /** Swap in a new arrangement (after any edit). Cheap; never restarts playback. */
  setArrangement(arr: Arrangement): void {
    const prev = this.arr;
    this.arr = arr;
    for (const t of arr.tracks) this.trackNodes(t);
    // tracks that disappeared
    for (const [id, n] of this.nodes) {
      if (!arr.tracks.some((t) => t.id === id)) {
        n.merger.disconnect();
        this.nodes.delete(id);
      }
    }
    this.applyMix();
    if (!this.run) return;
    const now = this.ctx.currentTime;
    // stop events that no longer exist (or whose track switched to its wet stem)
    const live = new Set(arr.events.map((e) => e.key));
    const wetTracks = new Set(arr.tracks.filter((t) => t.wet).map((t) => t.id));
    for (const [k, s] of this.scheduled) {
      if (s.end <= now) {
        this.scheduled.delete(k);
        continue;
      }
      const eventKey = k.slice(0, k.lastIndexOf("@"));
      if (!live.has(eventKey) || wetTracks.has(s.track)) this.stopScheduled(k, s, now);
    }
    this.syncStems(now, prev);
    // schedule what is new inside the already-scheduled window
    this.fillWindow(Math.max(now + 0.004, this.run.when), this.cursor, true);
  }

  private stopScheduled(k: string, s: Scheduled, now: number): void {
    const t = Math.max(now, s.start);
    try {
      holdAt(s.gain.gain, t);
      s.gain.gain.linearRampToValueAtTime(0, t + STOP_FADE_S);
      s.src.stop(t + STOP_FADE_S + 0.001);
    } catch {
      /* already stopped */
    }
    this.scheduled.delete(k);
  }

  // ------------------------------------------------------------ transport

  get playing(): boolean {
    return !!this.run;
  }

  /** Start at ctx time `when` from song second `offset`, looping [loopStart, loopEnd). */
  start(when: number, offset: number, loop: { start: number; end: number } | null): void {
    this.stop(when);
    const dur = this.arr?.duration ?? 0;
    const loopStart = loop ? loop.start : 0;
    const loopEnd = loop ? loop.end : dur;
    let off = Math.min(Math.max(0, offset), dur);
    if (off >= loopEnd || off < loopStart) off = loopStart;
    this.run = { when, offset: off, loopStart, loopEnd };
    this.passes = [{ ctx0: when, song0: off, index: this.passCount++ }];
    this.cursor = when;
    this.syncStems(when, null, true);
    this.fillWindow(when, when, false, true);
    this.tick();
    if (this.timer == null && typeof setInterval !== "undefined" && this.ctx instanceof AudioContext) {
      this.timer = setInterval(() => this.tick(), TICK_MS);
    }
  }

  stop(at: number = this.ctx.currentTime): void {
    if (this.timer != null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    for (const [k, s] of this.scheduled) {
      try {
        if (s.start >= at) s.src.stop(at);
        else this.stopScheduled(k, s, at);
      } catch {
        /* */
      }
    }
    this.scheduled.clear();
    for (const [, st] of this.stems) {
      try {
        holdAt(st.gain.gain, at);
        st.gain.gain.linearRampToValueAtTime(0, at + STOP_FADE_S);
        st.src.stop(at + STOP_FADE_S + 0.001);
      } catch {
        /* */
      }
    }
    this.stems.clear();
    this.run = null;
  }

  /** The current run (WP17 pads: loop-unwrapped clock for recording). */
  get runInfo(): { when: number; offset: number; loopStart: number; loopEnd: number } | null {
    return this.run ? { ...this.run } : null;
  }

  /** Song position (s) heard at ctx time t. */
  position(t: number): number {
    const r = this.run;
    if (!r) return 0;
    if (t <= r.when) return r.offset;
    const L = r.loopEnd - r.loopStart;
    const into = r.offset - r.loopStart + (t - r.when);
    return L > 0 ? r.loopStart + (into % L) : r.offset;
  }

  // ------------------------------------------------------------ scheduling

  private tick(): void {
    if (!this.run || !this.arr) return;
    const now = this.ctx.currentTime;
    const horizon = now + LOOKAHEAD_S;
    if (horizon > this.cursor) {
      this.advance(horizon);
    }
    // forget passes that are fully in the past
    this.passes = this.passes.filter((p, i) => i === this.passes.length - 1 || this.passEnd(p) > now - 1);
    for (const [k, s] of this.scheduled) if (s.end < now - 0.05) this.scheduled.delete(k);
    this.onTick?.();
  }

  private passEnd(p: Pass): number {
    return p.ctx0 + (this.run!.loopEnd - p.song0);
  }

  /** Schedule everything up to ctx time `t` now (offline rendering). */
  scheduleUntil(t: number): void {
    if (this.run && this.arr && t > this.cursor) this.advance(t);
  }

  private advance(horizon: number): void {
    const r = this.run!;
    let guard = 0;
    while (this.cursor < horizon - 1e-9 && guard++ < 10000) {
      const pass = this.passes[this.passes.length - 1];
      const end = this.passEnd(pass);
      const to = Math.min(horizon, end);
      this.schedulePass(pass, this.cursor, to, false);
      this.automate(pass, to);
      this.cursor = to;
      if (to >= end - 1e-9) {
        const next = { ctx0: end, song0: r.loopStart, index: this.passCount++ };
        this.passes.push(next);
        this.schedulePass(next, end, end, false, true);
      }
      if (r.loopEnd - r.loopStart <= 1e-6) break;
    }
  }

  /** Schedule events of the arrangement whose start falls in [from, to) ctx of `pass`.
   *  `jump`: also start events already sounding at the pass start (run start, loop wrap). */
  private schedulePass(pass: Pass, from: number, to: number, onlyNew: boolean, jump = false): void {
    const arr = this.arr!;
    const r = this.run!;
    const songFrom = pass.song0 + (from - pass.ctx0);
    const songTo = pass.song0 + (to - pass.ctx0);
    const wet = new Set(arr.tracks.filter((t) => t.wet).map((t) => t.id));
    for (const e of arr.events) {
      if (wet.has(e.track)) continue;
      const key = `${e.key}@${pass.index}`;
      if (this.scheduled.has(key)) continue;
      if (e.time >= songFrom - 1e-9 && e.time < songTo - 1e-9 && e.time < r.loopEnd) {
        this.startEvent(e, key, pass.ctx0 + (e.time - pass.song0), 0);
      } else if ((jump || onlyNew) && e.time < songFrom && e.time + e.duration > songFrom + 0.002 && (jump || e.sustained)) {
        // already sounding: start mid-way
        this.startEvent(e, key, from, songFrom - e.time);
      }
    }
    if (!onlyNew) this.scheduleSidechain(pass, songFrom, songTo);
  }

  /** New events inside the already scheduled window (after an edit). */
  private fillWindow(from: number, to: number, onlyNew: boolean, jump = false): void {
    if (!this.run) return;
    for (const p of this.passes) {
      const a = Math.max(from, p.ctx0);
      const b = Math.min(to, this.passEnd(p));
      if (b > a || (jump && a === p.ctx0)) this.schedulePass(p, a, Math.max(a, b), onlyNew, jump && a === p.ctx0);
    }
  }

  private startEvent(e: ArrEvent, key: string, when: number, into: number): void {
    const t = this.arr!.tracks.find((x) => x.id === e.track);
    if (!t) return;
    const n = this.trackNodes(t);
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    let buf = e.buffer;
    let offset = e.offset;
    if (e.reverse) {
      buf = this.reverse(e.buffer);
      offset = Math.max(0, e.buffer.duration - (e.offset + e.duration));
    }
    src.buffer = buf;
    const g = ctx.createGain();
    src.connect(g);
    g.connect(e.lane ? this.laneNode(n, e.lane, t.lanes[e.lane]) : n.dryIn);
    const total = e.duration + (e.fadeOut?.inTail ? e.tail : 0);
    const play = Math.max(0.001, total - into);
    // envelope
    const base = e.gain;
    g.gain.setValueAtTime(into > 0 ? 0 : base, when);
    if (into > 0) g.gain.linearRampToValueAtTime(base, when + 0.004);
    else if (e.fadeIn && e.fadeIn.len > 0.0005) {
      const curve = scaled(e.fadeIn.shape === "eq" ? equalPower(true) : raisedCos(true), base);
      g.gain.setValueCurveAtTime(curve, when, e.fadeIn.len);
    }
    if (e.fadeOut && e.fadeOut.len > 0.0005) {
      const fStart = when + (e.fadeOut.inTail ? e.duration : e.duration - e.fadeOut.len) - into;
      if (fStart > when + (e.fadeIn?.len ?? 0) + 0.0001) {
        const curve = scaled(e.fadeOut.shape === "eq" ? equalPower(false) : raisedCos(false), base);
        g.gain.setValueCurveAtTime(curve, fStart, e.fadeOut.len);
      }
    }
    const bufOffset = Math.min(Math.max(0, offset + into), Math.max(0, buf.duration - 1e-6));
    src.start(when, bufOffset, Math.min(play, buf.duration - bufOffset));
    const end = when + play;
    this.scheduled.set(key, { key, track: e.track, src, gain: g, start: when, end });
    this.lastScheduled.set(e.key, when);
    src.onended = () => {
      try {
        g.disconnect();
      } catch {
        /* */
      }
      if (this.scheduled.get(key)?.src === src) this.scheduled.delete(key);
    };
  }

  private reverse(b: AudioBuffer): AudioBuffer {
    let r = this.reversed.get(b);
    if (!r) {
      r = new AudioBuffer({ numberOfChannels: b.numberOfChannels, length: b.length, sampleRate: b.sampleRate });
      for (let c = 0; c < b.numberOfChannels; c++) {
        const src = b.getChannelData(c);
        const dst = new Float32Array(src.length);
        for (let i = 0, j = src.length - 1; i < src.length; i++, j--) dst[i] = src[j];
        r.copyToChannel(dst, c);
      }
      this.reversed.set(b, r);
    }
    return r;
  }

  /** Approximate the event-triggered sidechain on dry tracks (render/dsp.py). */
  private scheduleSidechain(pass: Pass, songFrom: number, songTo: number): void {
    const arr = this.arr!;
    for (const t of arr.tracks) {
      if (t.wet) continue;
      const n = this.nodes.get(t.id);
      const sc = n?.sidechain;
      if (!sc) continue;
      const key = sc.fx.key_lane ? `${sc.fx.key_track}.${sc.fx.key_lane}` : String(sc.fx.key_track);
      const trig = arr.triggers[key] ?? [];
      const depth = Number(sc.fx.depth ?? 0);
      const attack = Number(sc.fx.attack_ms ?? 1) / 1000;
      const release = Number(sc.fx.release_ms ?? 120) / 1000;
      for (const s of trig) {
        if (s < songFrom - 1e-9 || s >= songTo - 1e-9) continue;
        const at = pass.ctx0 + (s - pass.song0);
        if (at < this.ctx.currentTime) continue;
        const p = sc.node.gain;
        holdAt(p, at);
        p.linearRampToValueAtTime(1 - depth, at + Math.max(attack, 0.0005));
        p.setTargetAtTime(1, at + Math.max(attack, 0.0005), release);
      }
    }
  }

  /** Automated dry-path params (filter cutoff, gain fx): ramp to the value at `to`. */
  private automate(pass: Pass, to: number): void {
    const arr = this.arr!;
    const s = pass.song0 + (to - pass.ctx0);
    for (const t of arr.tracks) {
      const n = this.nodes.get(t.id);
      if (!n) continue;
      for (const f of n.filters) {
        const fn = t.automation[`${f.fx.id}.cutoff_hz`];
        if (fn) for (const b of f.nodes) b.frequency.linearRampToValueAtTime(Math.min(fn(s), this.ctx.sampleRate * 0.49), to);
      }
      for (const g of n.gains) {
        const fn = t.automation[`${g.fx.id}.gain_db`];
        if (fn) g.node.gain.linearRampToValueAtTime(10 ** (fn(s) / 20), to);
      }
    }
  }

  // ------------------------------------------------------------ stems

  /** Start / swap / stop the per-track stems (wet, or instrument dry) so they
   *  follow the arrangement; dry⇄wet switch is a 20 ms crossfade. */
  private syncStems(at: number, _prev: Arrangement | null, fresh = false): void {
    if (!this.arr) return;
    const now = Math.max(at, this.ctx.currentTime);
    for (const t of this.arr.tracks) {
      const n = this.trackNodes(t);
      const wantWet = t.wet;
      // dry path level: muted while the wet stem plays (a crossfade while
      // playing; set outright when a run starts)
      if (fresh) {
        for (const [g, v] of [
          [n.dryOut.gain, wantWet ? 0 : Math.SQRT1_2],
          [n.wetOut.gain, wantWet ? 1 : 0],
        ] as const) {
          g.cancelScheduledValues(0);
          g.value = v;
          g.setValueAtTime(v, now);
        }
      } else {
        holdAt(n.dryOut.gain, now);
        n.dryOut.gain.linearRampToValueAtTime(wantWet ? 0 : Math.SQRT1_2, now + XF_S);
        holdAt(n.wetOut.gain, now);
        n.wetOut.gain.linearRampToValueAtTime(wantWet ? 1 : 0, now + XF_S);
      }
      this.syncStem(t.id, "wet", wantWet ? t.wet : null, n.wetOut, now, fresh);
      this.syncStem(t.id, "dry", !wantWet ? t.dryStem : null, n.dryIn, now, fresh);
    }
    for (const k of [...this.stems.keys()]) {
      const id = k.slice(0, k.lastIndexOf(":"));
      if (!this.arr.tracks.some((t) => t.id === id)) this.dropStem(k, now);
    }
  }

  private syncStem(track: string, kind: "wet" | "dry", buf: AudioBuffer | null, dest: AudioNode, at: number, fresh: boolean): void {
    const key = `${track}:${kind}`;
    const cur = this.stems.get(key);
    if (cur && cur.buffer === buf && !fresh) return;
    if (cur) this.dropStem(key, at);
    if (!buf || !this.run) return;
    const r = this.run;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    src.loopStart = r.loopStart;
    src.loopEnd = Math.min(r.loopEnd, buf.duration);
    const g = this.ctx.createGain();
    src.connect(g);
    g.connect(dest);
    // a fresh run starts every stem exactly at the run start (sample-locked);
    // a stem swapped in during playback starts just ahead of the clock
    const when = fresh ? at : Math.max(at, this.ctx.currentTime + 0.005);
    const pos = this.position(when);
    g.gain.setValueAtTime(fresh ? 1 : 0, when);
    if (!fresh) g.gain.linearRampToValueAtTime(1, when + XF_S);
    src.start(when, Math.min(pos, Math.max(0, buf.duration - 1e-6)));
    this.stems.set(key, { src, gain: g, buffer: buf });
  }

  private dropStem(key: string, at: number): void {
    const s = this.stems.get(key);
    if (!s) return;
    try {
      holdAt(s.gain.gain, at);
      s.gain.gain.linearRampToValueAtTime(0, at + XF_S);
      s.src.stop(at + XF_S + 0.005);
    } catch {
      /* */
    }
    this.stems.delete(key);
  }

  // ------------------------------------------------------------ one-shots

  /**
   * The low-latency path: play `buffer` NOW (or at ctx time `when`) through the
   * editor output, independent of the transport. Used by the editor's instant
   * audition and by the performance pads (WP17). Returns a stop function.
   */
  oneShot(buffer: AudioBuffer, opts: { when?: number; gain?: number; pan?: number; offset?: number; duration?: number } = {}): () => void {
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const split = ctx.createChannelSplitter(2);
    const merge = ctx.createChannelMerger(2);
    const lg = ctx.createGain();
    const rg = ctx.createGain();
    const [cl, cr] = panLaw(opts.pan ?? 0);
    const g = opts.gain ?? 1;
    lg.gain.value = g * cl * Math.SQRT2;
    rg.gain.value = g * cr * Math.SQRT2;
    src.connect(split);
    split.connect(lg, 0);
    split.connect(rg, buffer.numberOfChannels > 1 ? 1 : 0);
    lg.connect(merge, 0, 0);
    rg.connect(merge, 0, 1);
    merge.connect(this.output);
    const when = opts.when ?? ctx.currentTime;
    src.start(when, opts.offset ?? 0, opts.duration);
    src.onended = () => merge.disconnect();
    return () => {
      try {
        src.stop();
      } catch {
        /* */
      }
    };
  }

  /** Scheduled sources right now (diagnostics / memory checks). */
  get scheduledCount(): number {
    return this.scheduled.size;
  }

  dispose(): void {
    this.stop();
    this.output.disconnect();
  }
}
