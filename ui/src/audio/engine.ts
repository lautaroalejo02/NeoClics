// Web Audio playback engine. Owns the AudioContext, the per-version deck
// graphs and all scheduling. React never drives it through renders: the
// store calls methods, the UI polls getPosition() from requestAnimationFrame.
//
// Design (see ui/README.md "Audio engine"):
// * A deck = one version. ALL of its sources (mix.wav, every track stem,
//   lane stems once loaded) are started with the SAME (when, offset), so
//   they stay sample-synchronous for the whole run. Mute/solo/volume and
//   the mix <-> stems switch are pure gain ramps on that running graph:
//   nothing is rebuilt, nothing re-renders.
// * A/B: the incoming deck is started at ctx time T on the position the
//   outgoing deck will have at T (musically mapped), then a short
//   equal-power crossfade runs at T. Both positions come from the same
//   clock arithmetic, so the switch is gapless and sample-accurate.
// * Seeks and loop edits restart the run behind a 6 ms fade (no clicks).
// * Decoded audio is memory-heavy: decks are evicted LRU over a byte
//   budget (active + A/B partner are pinned). Lane stems load on demand.

import type { VersionId } from "../api/types";
import { decodeWav, isWav } from "./wav";
import { matchGain, rms, rmsOfSum } from "./signal";
import { matchStemGainLimited, matchStemGainNative } from "./stemmatch";
import { createStemBus, loadLimiterModule, type BusKind, type StemBus } from "./stembus";
import {
  type MixerState,
  EMPTY_MIXER,
  isMixerDefault,
  laneGain,
  trackGain,
  trackUsesLanes,
} from "./mixer";
import { BEATS_PER_BAR, mapPositionAB, playheadAt, type Timing } from "../music/time";
import { ArrangementPlayer, type Arrangement } from "./arrangement";

export interface DeckSpec {
  version: VersionId;
  bpm: number;
  firstBar: number;
  durationS: number;
  sampleRate: number;
  mix: string;
  tracks: { id: string; stem: string; lanes: { id: string; stem: string }[] }[];
}

/** Loop in ABSOLUTE song beats (0 = bar 1 beat 1), so it means the same
 *  bars in every version regardless of bpm or rendered range. */
export interface AbsLoop {
  startBeat: number;
  endBeat: number;
}

export interface EngineSnapshot {
  audio: "idle" | "suspended" | "running" | "unsupported";
  /** what the transport plays: rendered versions, or the editor's live arrangement */
  source: "decks" | "editor";
  playing: boolean;
  active: VersionId | null;
  partner: VersionId | null;
  /** What is actually audible on the active deck. */
  listening: "mix" | "stems";
  /** Which stem-bus limiter is in use ("pending" until the worklet answers). */
  stemBus: BusKind | "pending" | "none";
  /** Stems requested (mixer touched) but still decoding. */
  stemsLoading: boolean;
  /** Versions whose mix is decoded and playable. */
  ready: VersionId[];
  loading: VersionId[];
  preview: string | null;
  error: string | null;
}

export interface SwitchRecord {
  from: VersionId;
  to: VersionId;
  /** ctx time at which the crossfade starts */
  at: number;
  fromPos: number;
  toOffset: number;
  restarted: boolean;
}

export type FetchFile = (path: string, signal: AbortSignal) => Promise<ArrayBuffer>;

const LOOKAHEAD_S = 0.012; // scheduling lead: > one render quantum + jitter
const XFADE_S = 0.02; // A/B equal-power crossfade
const SEEK_FADE_S = 0.006; // fade around seeks / loop edits / pause
const MODE_FADE_S = 0.015; // mix <-> stems crossfade (correlated: linear)
const GAIN_TAU_S = 0.006; // mute/solo/volume smoothing time constant

type Ctx = AudioContext;

/** The AudioContext constructor, including old Safari's prefixed one. */
function audioContextCtor(): typeof AudioContext | null {
  const g = globalThis as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext };
  return g.AudioContext ?? g.webkitAudioContext ?? null;
}

interface Run {
  when: number;
  offset: number;
  loop: { start: number; end: number } | null;
  sources: Map<string, AudioBufferSourceNode>;
  gen: number;
}

interface TrackNodes {
  fader: GainNode;
  full: GainNode;
  laneSum: GainNode;
  lanes: Map<string, GainNode>;
}

class Deck {
  readonly spec: DeckSpec;
  readonly timing: Timing;
  out: GainNode;
  level: GainNode;
  mixG: GainNode;
  stemG: GainNode;
  tracks = new Map<string, TrackNodes>();
  mix: AudioBuffer | null = null;
  stems = new Map<string, AudioBuffer>();
  lanes = new Map<string, AudioBuffer>(); // key "track.lane"
  stemMatch: number | null = null;
  laneMatch = new Map<string, number>();
  run: Run | null = null;
  mixPromise: Promise<void> | null = null;
  stemsPromise: Promise<void> | null = null;
  lanePromises = new Map<string, Promise<void>>();
  abort = new AbortController();
  lastUsed = 0;

  mixDelay: DelayNode;
  bus: StemBus;

  constructor(ctx: BaseAudioContext, dest: AudioNode, spec: DeckSpec, bus: StemBus) {
    this.spec = spec;
    this.bus = bus;
    this.timing = { bpm: spec.bpm, firstBar: spec.firstBar, durationS: spec.durationS };
    this.out = ctx.createGain();
    this.out.gain.value = 0;
    this.level = ctx.createGain();
    this.mixG = ctx.createGain();
    this.stemG = ctx.createGain();
    this.stemG.gain.value = 0;
    // Stems go through the limiter bus; the mix is delayed by the bus latency
    // so the mix <-> stems crossfade stays phase-aligned.
    this.mixDelay = ctx.createDelay(0.1);
    this.mixDelay.delayTime.value = bus.latencyS;
    this.mixG.connect(this.mixDelay);
    this.mixDelay.connect(this.level);
    this.stemG.connect(bus.input);
    bus.output.connect(this.level);
    this.level.connect(this.out);
    this.out.connect(dest);
    for (const t of spec.tracks) {
      const fader = ctx.createGain();
      const full = ctx.createGain();
      const laneSum = ctx.createGain();
      laneSum.gain.value = 0;
      full.connect(fader);
      laneSum.connect(fader);
      fader.connect(this.stemG);
      const lanes = new Map<string, GainNode>();
      for (const l of t.lanes) {
        const g = ctx.createGain();
        g.connect(laneSum);
        lanes.set(l.id, g);
      }
      this.tracks.set(t.id, { fader, full, laneSum, lanes });
    }
  }

  get stemsReady(): boolean {
    return this.stemMatch != null;
  }

  bytes(): number {
    let n = 0;
    const add = (b: AudioBuffer | null) => {
      if (b) n += b.length * b.numberOfChannels * 4;
    };
    add(this.mix);
    this.stems.forEach(add);
    this.lanes.forEach(add);
    return n;
  }

  position(t: number): number {
    const r = this.run;
    if (!r) return 0;
    return playheadAt(r.offset, t - r.when, r.loop, this.spec.durationS);
  }

  dispose(): void {
    this.abort.abort();
    this.run?.sources.forEach((s) => {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* not started */
      }
    });
    this.run = null;
    try {
      this.out.disconnect();
    } catch {
      /* already */
    }
  }
}

/** Ramp an AudioParam from its current value without clicks (Firefox lacks
 *  cancelAndHoldAtTime, so fall back to pinning the current value). */
function holdAt(p: AudioParam, t: number): void {
  const anyP = p as AudioParam & { cancelAndHoldAtTime?: (t: number) => void };
  if (typeof anyP.cancelAndHoldAtTime === "function") {
    anyP.cancelAndHoldAtTime(t);
  } else {
    const v = p.value;
    p.cancelScheduledValues(t);
    p.setValueAtTime(v, t);
  }
}

function linearTo(p: AudioParam, value: number, now: number, dur: number): void {
  holdAt(p, now);
  p.linearRampToValueAtTime(value, now + dur);
}

function smoothTo(p: AudioParam, value: number, now: number): void {
  holdAt(p, now);
  p.setTargetAtTime(value, now, GAIN_TAU_S);
}

function equalPowerCurve(fadeIn: boolean, from = 1): Float32Array {
  const N = 64;
  const c = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const x = i / (N - 1);
    c[i] = fadeIn ? Math.sin((x * Math.PI) / 2) : from * Math.cos((x * Math.PI) / 2);
  }
  return c;
}

export interface EngineOptions {
  fetchFile: FetchFile;
  /** Decoded-audio budget in bytes (LRU over decks). */
  memoryBudget?: number;
  /** Called once a deck's mix is decoded (the store derives waveform peaks). */
  onMixDecoded?: (version: VersionId, channels: Float32Array[], sampleRate: number) => void;
}

export function defaultMemoryBudget(): number {
  const nav = globalThis.navigator as (Navigator & { deviceMemory?: number }) | undefined;
  const dm = nav?.deviceMemory;
  const coarse = globalThis.matchMedia?.("(pointer: coarse)").matches ?? false;
  if ((dm != null && dm <= 4) || coarse) return 220 * 1024 * 1024;
  return 640 * 1024 * 1024;
}

export class AudioEngine {
  private ctx: Ctx | null = null;
  private master: GainNode | null = null;
  /** Stem-bus flavour: decided once per context (see initBus). */
  private busKind: BusKind | "pending" | null = null;
  private busPromise: Promise<void> | null = null;
  private decks = new Map<VersionId, Deck>();
  private specs = new Map<VersionId, DeckSpec>();
  private opts: EngineOptions;
  private budget: number;
  private gen = 0;
  private useClock = 0;

  private active: VersionId | null = null;
  private partner: VersionId | null = null;
  private playing = false;
  private pausedPos = 0;
  private loop: AbsLoop | null = null;
  private mixer: MixerState = EMPTY_MIXER;
  private levelsDb = new Map<VersionId, number>();
  private volume = 0.85;
  private error: string | null = null;

  // editor (WP16): the working song played from its pieces
  private source: "decks" | "editor" = "decks";
  private editor: ArrangementPlayer | null = null;
  private editorOut: GainNode | null = null;
  private editorLevel: GainNode | null = null;
  private editorArr: Arrangement | null = null;
  private editorLevelDb = 0;

  private previewSrc: AudioBufferSourceNode | null = null;
  private previewGain: GainNode | null = null;
  private previewKey: string | null = null;
  private previewStart = 0;
  private previewRegion: { start: number; end: number } | null = null;
  private previewLoop = true;
  private previewCache = new Map<string, Promise<AudioBuffer>>();

  private listeners = new Set<() => void>();
  private snap: EngineSnapshot;
  /** Last A/B switch (diagnostics + tests). */
  lastSwitch: SwitchRecord | null = null;
  /** How each file was decoded (diagnostics). */
  decodeLog: { path: string; via: "native" | "wav-fallback" }[] = [];

  onEnded: (() => void) | null = null;

  constructor(opts: EngineOptions) {
    this.opts = opts;
    this.budget = opts.memoryBudget ?? defaultMemoryBudget();
    this.snap = this.computeSnapshot();
  }

  // ------------------------------------------------------------ observable

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): EngineSnapshot => this.snap;

  private emit(): void {
    const next = this.computeSnapshot();
    const prev = this.snap;
    const same =
      prev.audio === next.audio &&
      prev.source === next.source &&
      prev.playing === next.playing &&
      prev.active === next.active &&
      prev.partner === next.partner &&
      prev.listening === next.listening &&
      prev.stemBus === next.stemBus &&
      prev.stemsLoading === next.stemsLoading &&
      prev.preview === next.preview &&
      prev.error === next.error &&
      prev.ready.join() === next.ready.join() &&
      prev.loading.join() === next.loading.join();
    if (same) return;
    this.snap = next;
    this.listeners.forEach((fn) => fn());
  }

  private computeSnapshot(): EngineSnapshot {
    const deck = this.active ? this.decks.get(this.active) : undefined;
    const wantStems = !isMixerDefault(this.mixer);
    const ready: VersionId[] = [];
    const loading: VersionId[] = [];
    this.decks.forEach((d, v) => (d.mix ? ready : loading).push(v));
    let audio: EngineSnapshot["audio"] = "idle";
    if (!audioContextCtor()) audio = "unsupported";
    else if (this.ctx) audio = this.ctx.state === "running" ? "running" : "suspended";
    return {
      audio,
      source: this.source,
      playing: this.playing,
      active: this.active,
      partner: this.partner,
      listening: wantStems && deck?.stemsReady ? "stems" : "mix",
      stemBus: this.busKind ?? "none",
      stemsLoading: wantStems && !!deck && !deck.stemsReady,
      ready: ready.sort(),
      loading: loading.sort(),
      preview: this.previewKey,
      error: this.error,
    };
  }

  // ------------------------------------------------------------ context

  private ensureCtx(sampleRate?: number): Ctx | null {
    if (this.ctx) return this.ctx;
    const AC = audioContextCtor();
    if (!AC) return null;
    let ctx: Ctx;
    try {
      // Matching the render's rate avoids any resampling on decode.
      ctx = new AC(
        sampleRate && sampleRate >= 8000 && sampleRate <= 96000
          ? { sampleRate, latencyHint: "interactive" }
          : { latencyHint: "interactive" },
      );
    } catch {
      ctx = new AC();
    }
    this.ctx = ctx;
    this.master = ctx.createGain();
    this.master.gain.value = this.volume;
    this.master.connect(ctx.destination);
    ctx.onstatechange = () => this.emit();
    this.initBus(ctx);
    return ctx;
  }

  /** Decide the stem-bus flavour. AudioWorklet needs a secure context and an
   *  async module load; until it answers no deck exists ("pending"). */
  private initBus(ctx: Ctx): void {
    const aw = (ctx as Ctx & { audioWorklet?: unknown }).audioWorklet;
    if (!aw) {
      this.busKind = "native";
      this.busPromise = Promise.resolve();
      return;
    }
    this.busKind = "pending";
    this.busPromise = loadLimiterModule(ctx).then((ok) => {
      this.busKind = ok ? "worklet" : "native";
      this.emit();
    });
  }

  /** Call synchronously from a user gesture (autoplay policy). */
  unlock(): void {
    const ctx = this.ensureCtx(this.firstSampleRate());
    if (ctx && ctx.state !== "running") {
      void ctx.resume().then(
        () => this.emit(),
        () => this.emit(),
      );
    }
  }

  private firstSampleRate(): number | undefined {
    const s = this.active ? this.specs.get(this.active) : this.specs.values().next().value;
    return s?.sampleRate;
  }

  get sampleRate(): number {
    return this.ctx?.sampleRate ?? 48000;
  }

  /** ctx time (tests/diagnostics). */
  now(): number {
    return this.ctx?.currentTime ?? 0;
  }

  // ------------------------------------------------------------ decoding

  private async decode(path: string, signal: AbortSignal): Promise<AudioBuffer> {
    const ctx = this.ensureCtx(this.firstSampleRate());
    if (!ctx) throw new Error("Este navegador no tiene Web Audio");
    const bytes = await this.opts.fetchFile(path, signal);
    const wav = isWav(bytes);
    try {
      // decodeAudioData detaches its input: keep a copy for the fallback.
      const buf = await ctx.decodeAudioData(wav ? bytes.slice(0) : bytes);
      this.decodeLog.push({ path, via: "native" });
      return buf;
    } catch (e) {
      if (!wav) throw e;
      const d = decodeWav(bytes);
      const buf = new AudioBuffer({
        numberOfChannels: d.channels.length,
        length: Math.max(1, d.frames),
        sampleRate: d.sampleRate,
      });
      d.channels.forEach((ch, i) => buf.copyToChannel(ch as Float32Array<ArrayBuffer>, i));
      this.decodeLog.push({ path, via: "wav-fallback" });
      return buf;
    }
  }

  // ------------------------------------------------------------ decks

  /** Register (or refresh) a version's playback spec. Cheap; no I/O. */
  register(spec: DeckSpec): void {
    this.specs.set(spec.version, spec);
  }

  private deckFor(version: VersionId): Deck | null {
    let d = this.decks.get(version);
    if (d) return d;
    const spec = this.specs.get(version);
    if (!spec) return null;
    const ctx = this.ensureCtx(spec.sampleRate);
    if (!ctx || !this.master) return null;
    if (this.busKind === null || this.busKind === "pending") return null; // worklet still loading
    d = new Deck(ctx, this.master, spec, createStemBus(ctx, this.busKind));
    this.decks.set(version, d);
    this.applyLevel(d, ctx.currentTime);
    this.applyMixer(d, ctx.currentTime);
    return d;
  }

  private touch(d: Deck): void {
    d.lastUsed = ++this.useClock;
  }

  /** Decode a version's mix (resolves when playable). Stems follow in the
   *  background when `stems` is true. */
  async prepare(version: VersionId, opts: { stems?: boolean } = {}): Promise<void> {
    const spec = this.specs.get(version);
    if (spec) {
      this.ensureCtx(spec.sampleRate);
      await this.busPromise;
    }
    const d = this.deckFor(version);
    if (!d) throw new Error(`Sin audio para ${version}`);
    this.touch(d);
    if (!d.mixPromise) {
      d.mixPromise = (async () => {
        this.emit();
        const buf = await this.decode(d.spec.mix, d.abort.signal);
        if (d.abort.signal.aborted) return;
        d.mix = buf;
        this.addSource(d, "mix", buf, d.mixG);
        const chans = Array.from({ length: buf.numberOfChannels }, (_, i) => buf.getChannelData(i));
        this.opts.onMixDecoded?.(version, chans, buf.sampleRate);
        this.evict();
        this.emit();
      })().catch((e) => {
        d.mixPromise = null;
        this.fail(`No pude cargar el audio de ${version}: ${String(e)}`);
        throw e;
      });
    }
    await d.mixPromise;
    if (opts.stems) void this.loadStems(d).catch(() => undefined);
  }

  private loadStems(d: Deck): Promise<void> {
    if (!d.stemsPromise) {
      d.stemsPromise = (async () => {
        await Promise.all(
          d.spec.tracks.map(async (t) => {
            const buf = await this.decode(t.stem, d.abort.signal);
            if (d.abort.signal.aborted) return;
            d.stems.set(t.id, buf);
            const nodes = d.tracks.get(t.id);
            if (nodes) this.addSource(d, `t:${t.id}`, buf, nodes.full);
          }),
        );
        if (d.abort.signal.aborted || !d.mix) return;
        // Level-match the pre-master stem sum to the post-master mix so the
        // mix <-> stems switch doesn't jump in loudness.
        d.stemMatch = await this.matchStems(d);
        this.evict();
        if (this.ctx) this.applyMixer(d, this.ctx.currentTime);
        this.emit();
      })().catch((e) => {
        d.stemsPromise = null;
        this.fail(`No pude cargar los stems: ${String(e)}`);
        throw e;
      });
    }
    return d.stemsPromise;
  }

  /** Gain that makes the stem bus output as loud (RMS) as the mastered mix. */
  private async matchStems(d: Deck): Promise<number> {
    const chans = (b: AudioBuffer) => Array.from({ length: b.numberOfChannels }, (_, i) => b.getChannelData(i));
    const mix = chans(d.mix!);
    const stems = [...d.stems.values()];
    if (d.bus.kind === "worklet") {
      return matchStemGainLimited(mix, stems.map(chans), d.mix!.sampleRate).gain;
    }
    return matchStemGainNative(mix, stems, d.mix!.sampleRate);
  }

  private loadLanes(d: Deck, trackId: string): Promise<void> {
    let p = d.lanePromises.get(trackId);
    if (!p) {
      const t = d.spec.tracks.find((x) => x.id === trackId);
      if (!t || t.lanes.length === 0) return Promise.resolve();
      p = (async () => {
        await this.loadStems(d);
        await Promise.all(
          t.lanes.map(async (l) => {
            const buf = await this.decode(l.stem, d.abort.signal);
            if (d.abort.signal.aborted) return;
            d.lanes.set(`${t.id}.${l.id}`, buf);
            const g = d.tracks.get(t.id)?.lanes.get(l.id);
            if (g) this.addSource(d, `l:${t.id}.${l.id}`, buf, g);
          }),
        );
        const stem = d.stems.get(t.id);
        if (stem && !d.abort.signal.aborted) {
          const ch = (b: AudioBuffer) => Array.from({ length: b.numberOfChannels }, (_, i) => b.getChannelData(i));
          const laneBufs = t.lanes.map((l) => d.lanes.get(`${t.id}.${l.id}`)).filter((b): b is AudioBuffer => !!b);
          d.laneMatch.set(t.id, matchGain(rms(ch(stem)), rmsOfSum(laneBufs.map(ch))));
        }
        this.evict();
        if (this.ctx) this.applyMixer(d, this.ctx.currentTime);
        this.emit();
      })().catch((e) => {
        d.lanePromises.delete(trackId);
        this.fail(`No pude cargar los carriles de ${trackId}: ${String(e)}`);
        throw e;
      });
      d.lanePromises.set(trackId, p);
    }
    return p;
  }

  /** Drop least-recently-used decks (never active/partner) over budget. */
  private evict(): void {
    let total = 0;
    this.decks.forEach((d) => (total += d.bytes()));
    if (total <= this.budget) return;
    const victims = [...this.decks.values()]
      .filter((d) => d.spec.version !== this.active && d.spec.version !== this.partner)
      .sort((a, b) => a.lastUsed - b.lastUsed);
    for (const d of victims) {
      if (total <= this.budget) break;
      total -= d.bytes();
      d.dispose();
      this.decks.delete(d.spec.version);
    }
  }

  /** Bytes of decoded audio held (diagnostics/tests). */
  memoryBytes(): number {
    let n = 0;
    this.decks.forEach((d) => (n += d.bytes()));
    return n;
  }

  isReady(version: VersionId): boolean {
    return !!this.decks.get(version)?.mix;
  }

  // ------------------------------------------------------------ runs

  private loopSecondsFor(d: Deck): { start: number; end: number } | null {
    if (!this.loop) return null;
    const off = (d.spec.firstBar - 1) * BEATS_PER_BAR;
    const spb = 60 / d.spec.bpm;
    const start = Math.max(0, (this.loop.startBeat - off) * spb);
    const end = Math.min(d.spec.durationS, (this.loop.endBeat - off) * spb);
    return end - start > 0.01 ? { start, end } : null;
  }

  private makeSource(buf: AudioBuffer, dest: AudioNode, run: Run, when: number, offset: number) {
    const ctx = this.ctx!;
    const s = ctx.createBufferSource();
    s.buffer = buf;
    if (run.loop) {
      s.loop = true;
      s.loopStart = run.loop.start;
      s.loopEnd = Math.min(run.loop.end, buf.duration);
    }
    s.connect(dest);
    s.start(when, Math.min(Math.max(0, offset), Math.max(0, buf.duration - 1e-6)));
    return s;
  }

  /** Start every loaded source of a deck at (when, offset): sample-locked. */
  private startRun(d: Deck, when: number, offset: number): void {
    this.stopRun(d, when);
    const loop = this.loopSecondsFor(d);
    let off = Math.min(Math.max(0, offset), d.spec.durationS);
    if (loop && off >= loop.end) off = loop.start;
    const run: Run = { when, offset: off, loop, sources: new Map(), gen: ++this.gen };
    d.run = run;
    if (d.mix) run.sources.set("mix", this.makeSource(d.mix, d.mixG, run, when, off));
    d.stems.forEach((buf, id) => {
      const n = d.tracks.get(id);
      if (n) run.sources.set(`t:${id}`, this.makeSource(buf, n.full, run, when, off));
    });
    d.lanes.forEach((buf, key) => {
      const [tid, lid] = key.split(".");
      const g = d.tracks.get(tid)?.lanes.get(lid);
      if (g) run.sources.set(`l:${key}`, this.makeSource(buf, g, run, when, off));
    });
    const mixSrc = run.sources.get("mix");
    if (mixSrc && !loop) {
      mixSrc.onended = () => {
        if (d.run?.gen === run.gen && this.active === d.spec.version && this.playing) {
          this.playing = false;
          this.pausedPos = d.spec.durationS;
          d.run = null;
          this.emit();
          this.onEnded?.();
        }
      };
    }
  }

  /** Add a just-decoded source to a RUNNING deck, aligned to its clock. */
  private addSource(d: Deck, key: string, buf: AudioBuffer, dest: AudioNode): void {
    const run = d.run;
    if (!run || !this.ctx || run.sources.has(key)) return;
    const when = this.ctx.currentTime + LOOKAHEAD_S;
    // Same clock arithmetic as every other source of this run.
    const pos = d.position(when);
    if (!run.loop && pos >= d.spec.durationS) return;
    const s = this.ctx.createBufferSource();
    s.buffer = buf;
    if (run.loop) {
      s.loop = true;
      s.loopStart = run.loop.start;
      s.loopEnd = Math.min(run.loop.end, buf.duration);
    }
    s.connect(dest);
    s.start(when, Math.min(pos, Math.max(0, buf.duration - 1e-6)));
    run.sources.set(key, s);
  }

  private stopRun(d: Deck, at: number): void {
    const run = d.run;
    if (!run) return;
    run.sources.forEach((s) => {
      s.onended = null;
      try {
        s.stop(at);
      } catch {
        /* never started */
      }
    });
    d.run = null;
  }

  // ------------------------------------------------------------ transport

  /** Audible position (seconds into the ACTIVE version). */
  getPosition(): number {
    if (this.source === "editor") return this.editorPosition();
    const d = this.active ? this.decks.get(this.active) : undefined;
    if (!this.playing || !d?.run || !this.ctx) return this.pausedPos;
    let t = this.ctx.currentTime;
    // Compensate output latency so the playhead matches what you hear.
    const ts = this.ctx.getOutputTimestamp?.();
    if (ts?.contextTime && ts.contextTime > 0) t = ts.contextTime;
    else t -= this.ctx.outputLatency || 0;
    return Math.max(0, d.position(Math.max(t, d.run.when)));
  }

  getDuration(): number {
    if (this.source === "editor") return this.editorArr?.duration ?? 0;
    return this.active ? this.specs.get(this.active)?.durationS ?? 0 : 0;
  }

  isPlaying(): boolean {
    return this.playing;
  }

  play(): void {
    this.unlock();
    const ctx = this.ctx;
    if (this.busKind === "pending" && this.busPromise && !this.playing) {
      // the limiter worklet is still loading (a few ms): start right after
      void this.busPromise.then(() => {
        if (!this.playing) this.play();
      });
      return;
    }
    if (this.source === "editor") {
      this.playEditor();
      return;
    }
    const d = this.active ? this.deckFor(this.active) : null;
    if (!ctx || !d || this.playing) return;
    if (!d.mix) {
      // not decoded yet: start as soon as it is
      void this.prepare(d.spec.version, { stems: true }).then(() => {
        if (!this.playing && this.active === d.spec.version) this.play();
      });
      return;
    }
    this.stopPreview();
    this.touch(d);
    const loop = this.loopSecondsFor(d);
    if (!loop && this.pausedPos >= d.spec.durationS - 0.01) this.pausedPos = 0;
    const now = ctx.currentTime;
    const T = now + LOOKAHEAD_S;
    this.decks.forEach((other) => {
      if (other !== d && other.run) {
        this.stopRun(other, now + SEEK_FADE_S);
        smoothTo(other.out.gain, 0, now);
      }
    });
    this.startRun(d, T, this.pausedPos);
    holdAt(d.out.gain, now);
    d.out.gain.setValueAtTime(0, T);
    d.out.gain.linearRampToValueAtTime(1, T + SEEK_FADE_S);
    this.playing = true;
    this.emit();
  }

  pause(): void {
    if (this.source === "editor") {
      this.pauseEditor();
      return;
    }
    const ctx = this.ctx;
    const d = this.active ? this.decks.get(this.active) : undefined;
    if (!ctx || !this.playing) return;
    const now = ctx.currentTime;
    this.pausedPos = d?.run ? d.position(now) : this.pausedPos;
    this.decks.forEach((x) => {
      if (x.run) {
        linearTo(x.out.gain, 0, now, SEEK_FADE_S);
        this.stopRun(x, now + SEEK_FADE_S + 0.002);
      }
    });
    this.playing = false;
    this.emit();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(seconds: number): void {
    const dur = this.getDuration();
    const s = Math.min(Math.max(0, seconds), dur || Number.POSITIVE_INFINITY);
    if (this.source === "editor") {
      this.pausedPos = s;
      if (this.playing && this.ctx && this.editor) {
        this.editor.start(this.ctx.currentTime + 0.006, s, this.editorLoop());
      }
      return;
    }
    if (!this.playing) {
      this.pausedPos = s;
      return;
    }
    this.restart(s);
  }

  /** Restart the active run at `pos` behind a short fade (seek/loop edit). */
  private restart(pos: number): void {
    const ctx = this.ctx;
    const d = this.active ? this.decks.get(this.active) : undefined;
    if (!ctx || !d) return;
    const now = ctx.currentTime;
    const T = now + SEEK_FADE_S;
    linearTo(d.out.gain, 0, now, SEEK_FADE_S);
    this.startRun(d, T, pos); // stops the old run at T
    d.out.gain.linearRampToValueAtTime(1, T + SEEK_FADE_S);
    this.pausedPos = d.run?.offset ?? pos;
  }

  setLoop(loop: AbsLoop | null): void {
    const prev = this.loop;
    this.loop = loop;
    if (prev?.startBeat === loop?.startBeat && prev?.endBeat === loop?.endBeat) return;
    if (this.source === "editor") {
      if (this.playing && this.ctx && this.editor) {
        const t = this.ctx.currentTime + 0.006;
        this.editor.start(t, this.editor.position(t), this.editorLoop());
      }
      return;
    }
    if (this.playing && this.ctx) {
      const d = this.active ? this.decks.get(this.active) : undefined;
      if (d?.run) this.restart(d.position(this.ctx.currentTime + SEEK_FADE_S));
    }
  }

  setVolume(v: number): void {
    this.volume = Math.min(1, Math.max(0, v));
    if (this.ctx && this.master) smoothTo(this.master.gain, this.volume * this.volume, this.ctx.currentTime);
  }

  // ------------------------------------------------------------ A/B

  /**
   * Make `version` the one you hear. While playing this is the gapless A/B
   * switch: same musical position, equal-power crossfade, no restart of
   * the transport. Resolves once the switch is scheduled.
   */
  async select(version: VersionId): Promise<void> {
    if (version === this.active) return;
    const spec = this.specs.get(version);
    if (!spec) throw new Error(`Versión desconocida ${version}`);
    await this.prepare(version, { stems: true });
    const prevId = this.active;
    const prev = prevId ? this.decks.get(prevId) : undefined;
    const next = this.decks.get(version);
    if (!next) return;
    this.touch(next);
    if (prevId === version) return;
    if (this.partner === version) this.partner = prevId;
    this.active = version;
    const ctx = this.ctx;
    if (ctx) this.applyMixer(next, ctx.currentTime); // lanes may now be wanted
    if (!this.playing || !prev || !ctx || !prev.run) {
      if (prev) this.pausedPos = mapPositionAB(this.pausedPos, prev.timing, next.timing);
      this.pausedPos = Math.min(this.pausedPos, next.spec.durationS);
      this.emit();
      return;
    }
    const now = ctx.currentTime;
    const T = now + LOOKAHEAD_S;
    // Where the outgoing deck WILL be at T (same clock arithmetic that
    // scheduled it), mapped musically onto the incoming version.
    const fromPos = prev.position(T);
    const toOffset = mapPositionAB(fromPos, prev.timing, next.timing);
    // Incoming: silence any leftover (rapid re-toggle) by T, start every
    // source at (T, toOffset), then fade in on an equal-power curve.
    holdAt(next.out.gain, now);
    next.out.gain.linearRampToValueAtTime(0, T - 0.001);
    this.startRun(next, T, toOffset);
    next.out.gain.setValueCurveAtTime(equalPowerCurve(true), T, XFADE_S);
    // Outgoing: equal-power fade from wherever its gain is right now.
    const fromGain = Math.max(0, Math.min(1, prev.out.gain.value));
    holdAt(prev.out.gain, now);
    prev.out.gain.setValueCurveAtTime(equalPowerCurve(false, fromGain), T, XFADE_S);
    this.stopRun(prev, T + XFADE_S + 0.03);
    this.lastSwitch = { from: prev.spec.version, to: version, at: T, fromPos, toOffset, restarted: true };
    this.pausedPos = toOffset;
    this.emit();
  }

  /** The other side of the A/B pair: preloaded (mix + stems) and pinned. */
  setPartner(version: VersionId | null): void {
    if (version === this.active) version = null;
    this.partner = version;
    if (version && this.specs.has(version)) {
      const v = version;
      void this.prepare(v, { stems: true })
        .then(() => {
          const d = this.decks.get(v);
          if (d && this.ctx) this.applyMixer(d, this.ctx.currentTime);
        })
        .catch(() => undefined);
    }
    this.emit();
  }

  getActive(): VersionId | null {
    return this.active;
  }

  /** First-time selection without audio side effects (boot). */
  setInitial(version: VersionId): void {
    if (this.active) return;
    this.active = version;
    this.emit();
  }

  // ------------------------------------------------------------ levels

  /** Loudness-match gains per version in dB (0 = untouched). */
  setLevels(levels: Map<VersionId, number>): void {
    this.levelsDb = new Map(levels);
    const now = this.ctx?.currentTime ?? 0;
    this.decks.forEach((d) => this.applyLevel(d, now));
  }

  private applyLevel(d: Deck, now: number): void {
    const db = this.levelsDb.get(d.spec.version) ?? 0;
    smoothTo(d.level.gain, 10 ** (db / 20), now);
  }

  // ------------------------------------------------------------ mixer

  setMixer(m: MixerState): void {
    this.mixer = m;
    const now = this.ctx?.currentTime ?? 0;
    this.decks.forEach((d) => this.applyMixer(d, now));
    this.emit();
  }

  private applyMixer(d: Deck, now: number, m: MixerState = this.mixer, allowLoad = true): void {
    const wantStems = !isMixerDefault(m);
    const pinned = allowLoad && (this.active === d.spec.version || this.partner === d.spec.version);
    if (wantStems && !d.stemsReady && d.mix && pinned) void this.loadStems(d).catch(() => undefined);
    const useStems = wantStems && d.stemsReady;
    linearTo(d.mixG.gain, useStems ? 0 : 1, now, MODE_FADE_S);
    linearTo(d.stemG.gain, useStems ? d.stemMatch ?? 1 : 0, now, MODE_FADE_S);
    for (const t of d.spec.tracks) {
      const n = d.tracks.get(t.id);
      if (!n) continue;
      smoothTo(n.fader.gain, trackGain(m, t.id), now);
      const wantLanes = t.lanes.length > 0 && trackUsesLanes(m, t.id);
      if (wantLanes && !d.laneMatch.has(t.id) && pinned) {
        void this.loadLanes(d, t.id).catch(() => undefined);
      }
      const useLanes = wantLanes && d.laneMatch.has(t.id);
      linearTo(n.full.gain, useLanes ? 0 : 1, now, MODE_FADE_S);
      linearTo(n.laneSum.gain, useLanes ? d.laneMatch.get(t.id) ?? 1 : 0, now, MODE_FADE_S);
      for (const l of t.lanes) {
        const g = n.lanes.get(l.id);
        if (g) smoothTo(g.gain, laneGain(m, t.id, l.id), now);
      }
    }
  }

  // ------------------------------------------------------------ offline render (diagnostics)

  /**
   * Render what the speakers would get for `version` with `mixer` applied,
   * OFFLINE and through the very same graph (Deck, stem bus, level match,
   * loudness-match offset, master volume). Used by the e2e test that proves
   * stem mode never clips, and by the stem bench. `bypassBus` skips the
   * limiter to show what the old behaviour did.
   */
  async renderOffline(
    version: VersionId,
    opts: { mixer?: MixerState; seconds?: number; bypassBus?: boolean } = {},
  ): Promise<{ channels: Float32Array[]; sampleRate: number; stemMatchDb: number | null; bus: BusKind }> {
    await this.prepare(version, { stems: true });
    const live = this.decks.get(version);
    if (!live?.mix) throw new Error(`Sin audio para ${version}`);
    await live.stemsPromise;
    const Off = (globalThis as { OfflineAudioContext?: typeof OfflineAudioContext }).OfflineAudioContext;
    if (!Off) throw new Error("Este navegador no tiene OfflineAudioContext");
    const sr = live.mix.sampleRate;
    const frames = Math.max(1, Math.round(Math.min(opts.seconds ?? live.spec.durationS, live.spec.durationS) * sr));
    const off = new Off(2, frames, sr);
    if (live.bus.kind === "worklet" && !opts.bypassBus) await loadLimiterModule(off);
    let bus: StemBus;
    if (opts.bypassBus) {
      const g = off.createGain();
      bus = { kind: live.bus.kind, input: g, output: g, latencyS: 0 };
    } else {
      bus = createStemBus(off, live.bus.kind);
    }
    const master = off.createGain();
    master.gain.value = this.volume * this.volume;
    master.connect(off.destination);
    const d = new Deck(off, master, live.spec, bus);
    d.mix = live.mix;
    d.stems = live.stems;
    d.lanes = live.lanes;
    d.stemMatch = live.stemMatch;
    d.laneMatch = live.laneMatch;
    d.out.gain.value = 1;
    d.level.gain.value = 10 ** ((this.levelsDb.get(version) ?? 0) / 20);
    this.applyMixer(d, 0, opts.mixer ?? this.mixer, false);
    const start = (buf: AudioBuffer, dest: AudioNode) => {
      const src = off.createBufferSource();
      src.buffer = buf;
      src.connect(dest);
      src.start(0);
    };
    start(live.mix, d.mixG);
    live.stems.forEach((buf, id) => {
      const n = d.tracks.get(id);
      if (n) start(buf, n.full);
    });
    live.lanes.forEach((buf, key) => {
      const [tid, lid] = key.split(".");
      const g = d.tracks.get(tid)?.lanes.get(lid);
      if (g) start(buf, g);
    });
    const out = await off.startRendering();
    return {
      channels: [out.getChannelData(0).slice(), out.getChannelData(1).slice()],
      sampleRate: sr,
      stemMatchDb: live.stemMatch ? 20 * Math.log10(live.stemMatch) : null,
      bus: live.bus.kind,
    };
  }

  // ------------------------------------------------------------ preview player

  /**
   * Loop a short preview file (chop candidates). Pauses the main transport.
   * `region` (seconds inside the file) loops a sub-range — used to audition
   * start/length nudges that stay inside the rendered preview.
   */
  async playPreview(key: string, path: string, region: { start: number; end: number } | null = null, loop = true): Promise<void> {
    this.unlock();
    const ctx = this.ctx;
    if (!ctx || !this.master) return;
    if (this.playing) this.pause();
    this.stopPreview();
    this.previewKey = key;
    this.emit();
    let p = this.previewCache.get(path);
    if (!p) {
      p = this.decode(path, new AbortController().signal);
      this.previewCache.set(path, p);
      if (this.previewCache.size > 12) this.previewCache.delete(this.previewCache.keys().next().value as string);
      p.catch(() => this.previewCache.delete(path));
    }
    let buf: AudioBuffer;
    try {
      buf = await p;
    } catch (e) {
      this.previewKey = null;
      this.fail(`No pude cargar el preview: ${String(e)}`);
      return;
    }
    if (this.previewKey !== key) return; // superseded
    const start = Math.max(0, Math.min(region?.start ?? 0, buf.duration - 0.01));
    const end = Math.max(start + 0.01, Math.min(region?.end ?? buf.duration, buf.duration));
    const g = ctx.createGain();
    g.connect(this.master);
    const s = ctx.createBufferSource();
    s.buffer = buf;
    s.loop = loop;
    if (loop) {
      s.loopStart = start;
      s.loopEnd = end;
    } else {
      // one-shot (a draft render): ends by itself and frees the player
      s.onended = () => {
        if (this.previewSrc !== s) return;
        this.previewSrc = null;
        this.previewRegion = null;
        this.previewKey = null;
        setTimeout(() => g.disconnect(), 50);
        this.previewGain = null;
        this.emit();
      };
    }
    s.connect(g);
    const T = ctx.currentTime + LOOKAHEAD_S;
    g.gain.setValueAtTime(0, ctx.currentTime);
    g.gain.setValueAtTime(0, T);
    g.gain.linearRampToValueAtTime(1, T + SEEK_FADE_S);
    s.start(T, start);
    this.previewSrc = s;
    this.previewGain = g;
    this.previewStart = T;
    this.previewRegion = { start, end };
    this.previewLoop = loop;
    this.emit();
  }

  /** Seconds inside the preview file being heard (for its playhead). */
  getPreviewPosition(): number | null {
    if (!this.previewSrc || !this.ctx || !this.previewRegion) return null;
    const r = this.previewRegion;
    return playheadAt(r.start, this.ctx.currentTime - this.previewStart, this.previewLoop ? r : null, r.end);
  }

  stopPreview(): void {
    const ctx = this.ctx;
    if (this.previewSrc && ctx) {
      const now = ctx.currentTime;
      const g = this.previewGain;
      if (g) linearTo(g.gain, 0, now, SEEK_FADE_S);
      try {
        this.previewSrc.stop(now + SEEK_FADE_S + 0.002);
      } catch {
        /* stopped */
      }
      const gg = g;
      setTimeout(() => gg?.disconnect(), 100);
    }
    this.previewSrc = null;
    this.previewGain = null;
    this.previewRegion = null;
    if (this.previewKey !== null) {
      this.previewKey = null;
      this.emit();
    }
  }

  // ------------------------------------------------------------ editor (WP16)

  private ensureEditor(): ArrangementPlayer | null {
    if (this.editor) return this.editor;
    const ctx = this.ensureCtx(48000);
    if (!ctx || !this.master || this.busKind === null || this.busKind === "pending") return null;
    // Same stem bus as stem mode: a look-ahead limiter at -1 dBFS so a
    // pre-master sum never clips at the speaker; then the level match.
    const bus = createStemBus(ctx, this.busKind);
    this.editorOut = ctx.createGain();
    this.editorOut.gain.value = 0;
    this.editorLevel = ctx.createGain();
    this.editorLevel.gain.value = 10 ** (this.editorLevelDb / 20);
    bus.output.connect(this.editorLevel);
    this.editorLevel.connect(this.editorOut);
    this.editorOut.connect(this.master);
    this.editor = new ArrangementPlayer(ctx, bus.input);
    if (this.editorArr) this.editor.setArrangement(this.editorArr);
    return this.editor;
  }

  /** Wait for the context + limiter bus, then build the editor player. */
  async editorReady(sampleRate = 48000): Promise<boolean> {
    this.ensureCtx(sampleRate);
    await this.busPromise;
    return !!this.ensureEditor();
  }

  /** New arrangement after any edit: takes effect without restarting playback. */
  setArrangement(arr: Arrangement): void {
    this.editorArr = arr;
    this.ensureEditor()?.setArrangement(arr);
  }

  /** Editor level (dB) so the pre-master editor mix sits near the mastered render. */
  setEditorLevel(db: number): void {
    this.editorLevelDb = db;
    if (this.editorLevel && this.ctx) smoothTo(this.editorLevel.gain, 10 ** (db / 20), this.ctx.currentTime);
  }

  getSource(): "decks" | "editor" {
    return this.source;
  }

  /** Switch what the transport plays, keeping the musical position (song
   *  seconds: decks are full renders from bar 1). */
  setSource(src: "decks" | "editor", position?: number): void {
    if (src === this.source) return;
    const wasPlaying = this.playing;
    const pos = position ?? this.getPosition();
    if (wasPlaying) this.pause();
    this.source = src;
    this.pausedPos = pos;
    if (wasPlaying) this.play();
    this.emit();
  }

  /** bpm of the editor's song (loops are kept in absolute beats). */
  editorBpm = 120;

  private editorLoop(): { start: number; end: number } | null {
    if (!this.loop || !this.editorArr) return null;
    const start = Math.max(0, (this.loop.startBeat * 60) / this.editorBpm);
    const end = Math.min(this.editorArr.duration, (this.loop.endBeat * 60) / this.editorBpm);
    return end - start > 0.01 ? { start, end } : null;
  }

  private editorPosition(): number {
    const ed = this.editor;
    if (!this.playing || !ed?.playing || !this.ctx) return this.pausedPos;
    let t = this.ctx.currentTime;
    const ts = this.ctx.getOutputTimestamp?.();
    if (ts?.contextTime && ts.contextTime > 0) t = ts.contextTime;
    else t -= this.ctx.outputLatency || 0;
    return ed.position(t);
  }

  private playEditor(): void {
    const ctx = this.ctx;
    if (!ctx || this.playing) return;
    const ed = this.ensureEditor();
    if (!ed || !this.editorArr) {
      void this.editorReady().then((ok) => {
        if (ok && this.source === "editor" && !this.playing && this.editorArr) this.play();
      });
      return;
    }
    this.stopPreview();
    const now = ctx.currentTime;
    this.decks.forEach((d) => {
      if (d.run) {
        this.stopRun(d, now + SEEK_FADE_S);
        smoothTo(d.out.gain, 0, now);
      }
    });
    const T = now + LOOKAHEAD_S;
    ed.start(T, this.pausedPos, this.editorLoop());
    holdAt(this.editorOut!.gain, now);
    this.editorOut!.gain.setValueAtTime(0, T);
    this.editorOut!.gain.linearRampToValueAtTime(1, T + SEEK_FADE_S);
    this.playing = true;
    this.emit();
  }

  private pauseEditor(): void {
    const ctx = this.ctx;
    const ed = this.editor;
    if (!ctx || !this.playing || !ed) return;
    const now = ctx.currentTime;
    this.pausedPos = ed.position(now);
    linearTo(this.editorOut!.gain, 0, now, SEEK_FADE_S);
    ed.stop(now + SEEK_FADE_S + 0.002);
    this.playing = false;
    this.emit();
  }

  /**
   * Low-latency one-shot: play a decoded buffer NOW (or at ctx time `when`)
   * over whatever is playing, through the editor output. The editor's instant
   * audition and the performance pads (WP17) use this path.
   */
  playOneShot(buffer: AudioBuffer, opts: { when?: number; gain?: number; pan?: number; offset?: number; duration?: number } = {}): () => void {
    this.unlock();
    const ed = this.ensureEditor();
    if (!ed || !this.editorOut || !this.ctx) return () => undefined;
    if (!this.playing || this.source !== "editor") {
      holdAt(this.editorOut.gain, this.ctx.currentTime);
      this.editorOut.gain.setValueAtTime(1, this.ctx.currentTime);
    }
    return ed.oneShot(buffer, opts);
  }

  /** Output + base latency in seconds (WP17 pads align recordings with this). */
  outputLatency(): number {
    return (this.ctx?.outputLatency || 0) + (this.ctx?.baseLatency || 0);
  }

  /** Decode a project audio file (editor pieces) at this context's rate. */
  async loadBuffer(path: string, signal: AbortSignal = new AbortController().signal): Promise<AudioBuffer> {
    return this.decode(path, signal);
  }

  /**
   * Render the editor's arrangement OFFLINE through the same player, bus and
   * level (tests prove an edit is audible/absent in the browser mix).
   */
  async renderEditorOffline(opts: { seconds?: number; from?: number; bypassBus?: boolean } = {}): Promise<{ channels: Float32Array[]; sampleRate: number }> {
    const arr = this.editorArr;
    if (!arr) throw new Error("No hay arreglo cargado");
    const Off = (globalThis as { OfflineAudioContext?: typeof OfflineAudioContext }).OfflineAudioContext;
    if (!Off) throw new Error("Este navegador no tiene OfflineAudioContext");
    await this.editorReady();
    const sr = this.ctx?.sampleRate ?? 48000;
    const from = opts.from ?? 0;
    const seconds = Math.min(opts.seconds ?? arr.duration - from, arr.duration - from);
    const off = new Off(2, Math.max(1, Math.round(seconds * sr)), sr);
    const level = off.createGain();
    level.gain.value = 10 ** (this.editorLevelDb / 20);
    level.connect(off.destination);
    let input: AudioNode = level;
    if (!opts.bypassBus) {
      if (this.busKind === "worklet") await loadLimiterModule(off);
      const bus = createStemBus(off, this.busKind === "worklet" ? "worklet" : "native");
      bus.output.connect(level);
      input = bus.input;
    }
    const player = new ArrangementPlayer(off, input);
    player.setArrangement(arr);
    player.start(0, from, null);
    player.scheduleUntil(seconds + 0.01);
    const out = await off.startRendering();
    return { channels: [out.getChannelData(0).slice(), out.getChannelData(1).slice()], sampleRate: sr };
  }

  /** dB that level-matches a version's pre-master stem sum to its mastered mix
   *  (the editor plays a pre-master sum too). Loads the version's stems. */
  async stemMatchDb(version: VersionId): Promise<number | null> {
    if (!this.specs.has(version)) return null;
    await this.prepare(version, { stems: true });
    const d = this.decks.get(version);
    if (!d) return null;
    await this.loadStems(d);
    return d.stemMatch ? 20 * Math.log10(d.stemMatch) : null;
  }

  /** The live player (diagnostics/tests). */
  get editorPlayer(): ArrangementPlayer | null {
    return this.editor;
  }

  // ------------------------------------------------------------ misc

  private fail(msg: string): void {
    this.error = msg;
    this.emit();
  }

  clearError(): void {
    this.error = null;
    this.emit();
  }

  /** Drop decks whose versions no longer exist (rare) and close the ctx. */
  dispose(): void {
    this.stopPreview();
    this.decks.forEach((d) => d.dispose());
    this.decks.clear();
    void this.ctx?.close();
    this.ctx = null;
    this.master = null;
    this.playing = false;
  }
}
