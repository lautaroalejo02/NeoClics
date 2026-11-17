// Pads state + actions (WP17). The audio path is synchronous and outside React:
// keydown -> classifyKey -> press() -> PadsPlayer.press() (src.start(0)) ->
// the take buffer; React only redraws from a rAF-throttled counter. Pads
// capture the keyboard only while the pads view is ON (Esc leaves).
//
// Recording: against the editor transport, loop-unwrapped, compensated with
// the context's output timestamp, the stem bus delay and the calibrated
// per-device offset, so a hit lands where it was played relative to what was
// HEARD. Takes are saved to pads/takes/ while recording (every pass and every
// 3 s) and mirrored to localStorage on every press: a reload mid-take loses
// nothing (recovered on the next load).

import type { AudioEngine } from "../audio/engine";
import { isTypingTarget } from "../hooks/hooks";
import type { EditorStore } from "../editor/store";
import { BEATS_PER_BAR, plainSound, scalePitchClasses, tonicOf, trackNotes, voiceKey, type SongJson, type TrackJson } from "../editor/song";
import type { Op } from "../editor/ops";
import type { Store } from "../state/store";
import { friendlyError } from "../text/es";
import { PAD_COLORS, PAD_KEYS, classifyKey, kitOrder, kitSlot, readLayout } from "./keymap";
import {
  DEFAULT_QUANTIZE,
  calibrate,
  deviceKey,
  heardCtxTime,
  heardUnwrapped,
  latencyVerdict,
  padSound,
  placePress,
  processEvents,
  snapshotPads,
  takeSpan,
  undoLastPass,
  type CleanupJson,
  type LatencyLevel,
  type PadBankJson,
  type PadJson,
  type PadsFileJson,
  type QuantizeJson,
  type TakeEventJson,
  type TakeJson,
} from "./model";
import { Metronome, PadsPlayer, clickBuffer } from "./player";

export interface TakeSummary {
  id: string;
  name: string;
  status: "recording" | "done";
  bpm: number;
  length_beats: number;
  loop_beats?: number | null;
  origin_beat: number;
  n_events: number;
  pads: string[];
  quantize: QuantizeJson;
  created_at: string;
  updated_at: string;
  commits: number;
}

export interface LatencyInfo {
  outputMs: number;
  baseMs: number;
  busMs: number;
  calibratedMs: number | null;
  device: string;
  level: LatencyLevel;
  text: string;
  schedP50: number | null;
  schedP95: number | null;
  schedMax: number | null;
}

export interface CalibState {
  phase: "idle" | "running" | "done" | "failed";
  taps: number;
  total: number;
  resultMs: number | null;
  spreadMs: number | null;
}

export interface PadsState {
  status: "off" | "loading" | "ready" | "error" | "unsupported";
  error: string | null;
  /** the pads panel is open in the Arreglo view */
  panel: boolean;
  /** the pads capture the keyboard */
  on: boolean;
  banks: PadsFileJson;
  takes: TakeSummary[];
  selected: string | null;
  missing: string[];
  rec: { phase: "idle" | "countin" | "recording"; take: string | null; cycle: number; events: number };
  metronome: boolean;
  countIn: 0 | 1 | 2;
  quantize: QuantizeJson;
  latency: LatencyInfo;
  calib: CalibState;
  selectedTake: string | null;
  auditioning: string | null;
  notice: string | null;
  layout: Map<string, string> | null;
  /** bumps on every press (throttled redraw of counters) */
  tick: number;
}

const LS_INFLIGHT = "muestra.pads.inflight.v1";
const LS_CALIB = "muestra.pads.calibration.v1";
const SAVE_EVERY_MS = 3000;

function defaultBanks(): PadsFileJson {
  return { active_bank: "a", banks: ["a", "b", "c", "d"].map((id) => ({ id, name: `Banco ${id.toUpperCase()}`, pads: [] })) };
}

function readLS<T>(key: string): T | null {
  try {
    return JSON.parse(globalThis.localStorage?.getItem(key) ?? "null") as T | null;
  } catch {
    return null;
  }
}

function writeLS(key: string, v: unknown): void {
  try {
    if (v == null) globalThis.localStorage?.removeItem(key);
    else globalThis.localStorage?.setItem(key, JSON.stringify(v));
  } catch {
    /* private mode / quota */
  }
}

interface Recording {
  take: TakeJson;
  /** loop length (s) for overdub takes; null = linear */
  loopLenS: number | null;
  loopStartS: number;
  originBeat: number;
  startCycle: number;
  held: Map<string, { ev: TakeEventJson; u: number }>;
  dirty: boolean;
  lastSave: number;
  saving: Promise<void> | null;
  lastCycle: number;
  /** ctx time of the transport start (count-in end) */
  startsAt: number;
}

export class PadsStore {
  state: PadsState;
  readonly app: Store;
  readonly editor: EditorStore;
  readonly engine: AudioEngine;
  private listeners = new Set<() => void>();
  private player: PadsPlayer | null = null;
  private metro: Metronome | null = null;
  private buffers = new Map<string, AudioBuffer[]>();
  private rec: Recording | null = null;
  private takeCache = new Map<string, TakeJson>();
  private saveBanksTimer: ReturnType<typeof setTimeout> | null = null;
  private saveTimer: ReturnType<typeof setInterval> | null = null;
  private padEls = new Map<string, HTMLElement>();
  private rafPending = false;
  private calibRun: { clicks: number[]; taps: number[]; done: ReturnType<typeof setTimeout> | null } | null = null;
  private loadGen = 0;
  /** last press diagnostics (tests / measurements) */
  readonly presses: { code: string; eventMs: number; schedMs: number; when: number; heardCtx: number; beat: number | null }[] = [];

  constructor(app: Store, editor: EditorStore, engine: AudioEngine) {
    this.app = app;
    this.editor = editor;
    this.engine = engine;
    this.state = {
      status: "off",
      error: null,
      panel: false,
      on: false,
      banks: defaultBanks(),
      takes: [],
      selected: null,
      missing: [],
      rec: { phase: "idle", take: null, cycle: 0, events: 0 },
      metronome: true,
      countIn: 1,
      quantize: { ...DEFAULT_QUANTIZE },
      latency: { outputMs: 0, baseMs: 0, busMs: 0, calibratedMs: null, device: "default", level: "ok", text: "", schedP50: null, schedP95: null, schedMax: null },
      calib: { phase: "idle", taps: 0, total: 0, resultMs: null, spreadMs: null },
      selectedTake: null,
      auditioning: null,
      notice: null,
      layout: null,
      tick: 0,
    };
    (globalThis as { __muestraPads?: PadsStore }).__muestraPads = this;
  }

  // ------------------------------------------------------------ observable

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getState = (): PadsState => this.state;

  private set(p: Partial<PadsState>): void {
    this.state = { ...this.state, ...p };
    this.listeners.forEach((fn) => fn());
  }

  /** Redraw counters at most once per frame (never on the audio path). */
  private bump(): void {
    if (this.rafPending) return;
    this.rafPending = true;
    const raf = globalThis.requestAnimationFrame ?? ((f: () => void) => setTimeout(f, 16));
    raf(() => {
      this.rafPending = false;
      this.set({ tick: this.state.tick + 1, rec: this.recState() });
    });
  }

  private recState(): PadsState["rec"] {
    const r = this.rec;
    if (!r) return { phase: "idle", take: null, cycle: 0, events: 0 };
    const ctx = this.engine.audioContext();
    const phase = ctx && ctx.currentTime < r.startsAt ? "countin" : "recording";
    return { phase, take: r.take.id ?? null, cycle: r.lastCycle, events: r.take.events.length };
  }

  // ------------------------------------------------------------ boot

  async init(): Promise<void> {
    if (this.state.status === "loading" || this.state.status === "ready") return;
    this.set({ status: "loading" });
    try {
      const res = await this.app.client.tool<{ banks: PadsFileJson; takes: TakeSummary[] }>("pads_get");
      this.set({ status: "ready", banks: res.banks?.banks?.length ? res.banks : defaultBanks(), takes: res.takes ?? [] });
      void readLayout().then((layout) => layout && this.set({ layout }));
      await this.recover(res.takes ?? []);
      this.refreshLatency();
    } catch (e) {
      const msg = friendlyError(e, "No pude cargar los pads").message;
      this.set({ status: /unknown_tool|not_implemented|404/.test(String(e)) ? "unsupported" : "error", error: msg });
    }
  }

  /** A take left "recording" (page closed mid-take) or only in localStorage. */
  private async recover(takes: TakeSummary[]): Promise<void> {
    const local = readLS<TakeJson & { savedAt: number }>(LS_INFLIGHT);
    let notice: string | null = null;
    if (local && local.events?.length) {
      const server = local.id ? takes.find((t) => t.id === local.id) : undefined;
      if (!server || server.n_events < local.events.length) {
        try {
          const saved = await this.app.client.tool<{ take: TakeSummary }>("pads_take_save", { take: { ...stripLocal(local), status: "done" } });
          notice = `Recuperé la toma «${saved.take.name}» que estabas grabando (${local.events.length} golpes): no se perdió nada.`;
          await this.reloadTakes();
        } catch {
          notice = "Tenés una toma sin guardar en este navegador; no pude subirla todavía.";
        }
      }
    }
    for (const t of this.state.takes.filter((x) => x.status === "recording")) {
      const full = await this.loadTake(t.id);
      if (full) await this.app.client.tool("pads_take_save", { take: { ...full, status: "done" } });
      notice ??= `Recuperé la toma «${t.name}» (${t.n_events} golpes) que quedó grabándose cuando se cerró la página.`;
    }
    if (notice) {
      writeLS(LS_INFLIGHT, null);
      await this.reloadTakes();
      this.set({ notice });
      this.app.toast(notice, "ok", undefined, 8000);
    }
  }

  async reloadTakes(): Promise<void> {
    const res = await this.app.client.tool<{ banks: PadsFileJson; takes: TakeSummary[] }>("pads_get");
    this.set({ takes: res.takes ?? [] });
  }

  // ------------------------------------------------------------ banks

  bank(): PadBankJson {
    const b = this.state.banks;
    return b.banks.find((x) => x.id === b.active_bank) ?? b.banks[0];
  }

  padAt(code: string): PadJson | undefined {
    return this.bank().pads.find((p) => p.key === code);
  }

  setBank(index: number): void {
    const b = this.state.banks.banks[index];
    if (!b || b.id === this.state.banks.active_bank) return;
    if (this.rec) {
      this.app.toast("Terminá la toma (Enter) para cambiar de banco.", "info", undefined, 2500);
      return;
    }
    this.set({ banks: { ...this.state.banks, active_bank: b.id } });
    this.persistBanks();
    void this.loadBank();
  }

  /** Replace pads of the active bank (merge by key) and reload sounds. */
  updatePads(pads: PadJson[], remove: string[] = []): void {
    const bank = this.bank();
    const byKey = new Map(bank.pads.map((p) => [p.key, p]));
    for (const k of remove) byKey.delete(k);
    for (const p of pads) byKey.set(p.key, p);
    const next = { ...bank, pads: PAD_KEYS.filter((k) => byKey.has(k)).map((k) => byKey.get(k)!) };
    this.set({ banks: { ...this.state.banks, banks: this.state.banks.banks.map((b) => (b.id === bank.id ? next : b)) } });
    this.persistBanks();
    void this.loadBank();
  }

  setPad(code: string, patch: Partial<PadJson>): void {
    const p = this.padAt(code);
    if (!p) return;
    this.updatePads([{ ...p, ...patch }]);
  }

  clearPad(code: string): void {
    this.updatePads([], [code]);
  }

  renameBank(name: string): void {
    const bank = this.bank();
    this.set({ banks: { ...this.state.banks, banks: this.state.banks.banks.map((b) => (b.id === bank.id ? { ...b, name: name.slice(0, 40) || b.name } : b)) } });
    this.persistBanks();
  }

  private persistBanks(): void {
    if (this.saveBanksTimer) clearTimeout(this.saveBanksTimer);
    this.saveBanksTimer = setTimeout(() => {
      void this.app.client.tool("pads_save_banks", { banks: this.state.banks }).catch((e) => this.app.toast(`No pude guardar los pads: ${friendlyError(e).message}`, "warn"));
    }, 400);
  }

  /** Flush a pending banks save (tests / before leaving). */
  async flushBanks(): Promise<void> {
    if (this.saveBanksTimer) {
      clearTimeout(this.saveBanksTimer);
      this.saveBanksTimer = null;
      await this.app.client.tool("pads_save_banks", { banks: this.state.banks });
    }
  }

  select(code: string | null): void {
    this.set({ selected: code });
  }

  // ------------------------------------------------------------ assigning sounds

  assignSample(code: string, uri: string, name?: string): void {
    const old = this.padAt(code);
    const idx = PAD_KEYS.indexOf(code);
    this.updatePads([{ ...(old ?? {}), key: code, name: name ?? uri.split("/").pop()!.replace(/\.[a-z0-9]+$/i, "").slice(0, 40), color: old?.color ?? PAD_COLORS[idx % 8], sample: uri, slice: null, note: null }]);
  }

  /** A whole starter kit onto the bank: kick/snare/clap/hats on the bottom row, the rest above. */
  async dropKit(kit: string): Promise<number> {
    const res = await this.app.client.tool<{ kits: { name: string; lanes: { lane: string; uri: string; present: boolean }[] }[] }>("library_kits", { name: kit });
    const info = res.kits.find((k) => k.name === kit) ?? res.kits[0];
    if (!info) return 0;
    const used = new Set<number>();
    const pads: PadJson[] = [];
    const present = info.lanes.filter((x) => x.present);
    for (const name of kitOrder(present.map((x) => x.lane))) {
      const l = present.find((x) => x.lane === name)!;
      const slot = kitSlot(l.lane, used);
      if (slot < 0) break;
      used.add(slot);
      const hat = /hat/.test(l.lane);
      pads.push({ key: PAD_KEYS[slot], name: l.lane, lane: l.lane.replace(/[^a-z0-9_-]/g, "-") || undefined, color: PAD_COLORS[slot % 8], sample: l.uri, choke: hat ? 1 : null, velocity: 0.8, accent: 1 });
    }
    this.updatePads(pads, this.bank().pads.map((p) => p.key));
    return pads.length;
  }

  /** "Armar pads desde este track": the lanes of a drum track (same sounds, gains, pans). */
  fromTrack(song: SongJson, trackId: string): number {
    const t = song.tracks?.find((x) => x.id === trackId);
    if (!t?.pattern) return 0;
    const used = new Set<number>();
    const pads: PadJson[] = [];
    for (const name of kitOrder(Object.keys(t.pattern.lanes))) {
      const lane = t.pattern.lanes[name];
      const slot = kitSlot(name, used);
      if (slot < 0) break;
      used.add(slot);
      pads.push({ key: PAD_KEYS[slot], name, lane: name, color: PAD_COLORS[slot % 8], sample: lane.sample ?? null, slice: lane.slice ?? null, gain_db: lane.gain_db ?? 0, pan: lane.pan ?? 0, pitch_semitones: lane.pitch_semitones ?? 0, reverse: !!lane.reverse, choke: lane.choke ?? null });
    }
    this.updatePads(pads, this.bank().pads.map((p) => p.key));
    return pads.length;
  }

  /**
   * "Cortar en pads": slice a chop or a source region (song.pieces renders each
   * slice through the chop warp path) onto consecutive pads from `startCode`.
   * Slices share choke group 2 so the next slice cuts the previous one.
   */
  async sliceToPads(region: { chop?: string; source?: string; stem?: string; start_downbeat?: number; start_beats?: number; length_beats?: number }, by: "beat" | "half" | "transients", speed: "project" | "source", startCode?: string, label = "slice"): Promise<number> {
    const res = await this.app.client.tool<{ slices: PadJson["slice"][] }>("pads_slice", { ...region, by, speed, max_slices: 24 });
    const start = Math.max(0, startCode ? PAD_KEYS.indexOf(startCode) : 0);
    const pads: PadJson[] = [];
    res.slices.forEach((sl, i) => {
      const k = PAD_KEYS[start + i];
      if (!k || !sl) return;
      pads.push({ key: k, name: `${label} ${i + 1}`, lane: `${label.replace(/[^a-z0-9_-]/gi, "-").toLowerCase()}-${i + 1}`, color: PAD_COLORS[(i % 4) + 2], slice: sl, choke: 2, velocity: 0.85, accent: 1 });
    });
    this.updatePads(pads);
    return pads.length;
  }

  /** Note pads: the scale of the song's key (8 degrees) on a row, for an instrument track. */
  scalePads(song: SongJson, trackId: string, rowStart = 8): number {
    const t = song.tracks?.find((x) => x.id === trackId);
    if (!t?.instrument) return 0;
    const pcs = scalePitchClasses(song.meta.key);
    const tonic = tonicOf(song.meta.key) ?? 0;
    const notes = trackNotes(song, t).map((n) => n.pitch).sort((a, b) => a - b);
    const median = notes.length ? notes[Math.floor(notes.length / 2)] : 48;
    let root = tonic + 12 * Math.floor((median - tonic) / 12);
    if (root > median) root -= 12;
    const degrees: number[] = [];
    for (let p = root; degrees.length < 8 && p < root + 24; p++) if (pcs.has(((p % 12) + 12) % 12)) degrees.push(p);
    const names = ["I", "II", "III", "IV", "V", "VI", "VII", "I↑"];
    const pads = degrees.map((pitch, i) => ({ key: PAD_KEYS[rowStart + i], name: `${t.id} ${names[i]}`, color: "#7aa2ff", note: { track: t.id, pitches: [pitch] }, mode: "gate" as const, velocity: 0.8, accent: 1 }));
    this.updatePads(pads);
    return pads.length;
  }

  // ------------------------------------------------------------ buffers

  /** Decode/prepare every sound of the active bank (song.pieces renders slices,
   *  tuned and reversed sounds exactly like the renderer). */
  async loadBank(): Promise<void> {
    const gen = ++this.loadGen;
    const pads = this.bank().pads;
    const sounds = pads.map(padSound).filter((s): s is NonNullable<typeof s> => !!s);
    const notes = pads.flatMap((p) => (p.note ? p.note.pitches.map((pitch) => ({ track: p.note!.track, pitch: Math.max(0, Math.min(127, pitch + Math.round(p.pitch_semitones ?? 0))), seconds: 4 })) : []));
    this.set({ missing: pads.map((p) => p.key).filter((k) => !this.buffers.has(k)) });
    try {
      const [byKey, noteBufs] = await Promise.all([this.editor.soundBuffers(sounds), this.editor.noteBuffers(notes)]);
      if (gen !== this.loadGen) return;
      const next = new Map<string, AudioBuffer[]>();
      const missing: string[] = [];
      for (const p of pads) {
        let bufs: AudioBuffer[] = [];
        if (p.note) bufs = p.note.pitches.map((pitch) => noteBufs.get(`${p.note!.track}:${Math.max(0, Math.min(127, pitch + Math.round(p.pitch_semitones ?? 0)))}`)).filter((b): b is AudioBuffer => !!b);
        else {
          const s = padSound(p)!;
          const b = byKey.get(plainSound(s) ? s.sample! : voiceKey(s));
          if (b) bufs = [b];
        }
        if (bufs.length) next.set(p.key, bufs);
        else missing.push(p.key);
      }
      this.buffers = next;
      this.set({ missing });
    } catch (e) {
      if (gen === this.loadGen) this.app.toast(`No pude preparar los sonidos de los pads: ${friendlyError(e).message}`, "warn");
    }
  }

  // ------------------------------------------------------------ on / off + keys

  /** Open/close the pads panel (opening turns the keyboard on). */
  openPanel(open: boolean): void {
    if (open === this.state.panel) {
      if (open && !this.state.on) this.setOn(true);
      return;
    }
    this.set({ panel: open });
    if (open) {
      void this.init().then(() => this.setOn(true));
    } else this.setOn(false);
  }

  setOn(on: boolean): void {
    if (on === this.state.on) return;
    if (on) {
      this.engine.unlock();
      this.ensureAudio();
      window.addEventListener("keydown", this.onKeyDown, true);
      window.addEventListener("keyup", this.onKeyUp, true);
      window.addEventListener("blur", this.onBlur);
      this.editor.padsActive = true;
      this.refreshLatency();
      void this.loadBank();
    } else {
      if (this.rec) void this.stopRecord();
      window.removeEventListener("keydown", this.onKeyDown, true);
      window.removeEventListener("keyup", this.onKeyUp, true);
      window.removeEventListener("blur", this.onBlur);
      this.player?.stopAll();
      this.editor.padsActive = false;
    }
    this.set({ on });
  }

  private ensureAudio(): PadsPlayer | null {
    if (this.player) return this.player;
    const ctx = this.engine.audioContext();
    const out = this.engine.padsOutput();
    if (!ctx || !out) return null;
    this.player = new PadsPlayer(ctx, out);
    this.metro = new Metronome(ctx, out, () => this.engine.editorClock(), () => this.editor.state.song?.meta.bpm ?? 120);
    this.metro.enabled = this.state.metronome;
    return this.player;
  }

  private onKeyDown = (e: KeyboardEvent): void => {
    const t = e.target as HTMLElement | null;
    if (isTypingTarget(t) || t?.isContentEditable) {
      if (e.code === "Escape") t?.blur();
      return;
    }
    if (document.querySelector(".overlay:not(.pads-overlay-ok)")) return;
    const a = classifyKey(e);
    if (!a) return;
    if (this.calibRun && (a.kind === "pad" || a.kind === "transport")) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (!e.repeat) this.calibTap(e.timeStamp);
      return;
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    if (a.kind === "pad") {
      if (!e.repeat) this.press(a.code, a.accent, e.timeStamp);
      return;
    }
    if (e.repeat) return;
    switch (a.kind) {
      case "bank":
        this.setBank(a.index);
        return;
      case "transport":
        this.editor.togglePlay();
        return;
      case "record":
        void this.toggleRecord();
        return;
      case "undoPass":
        void this.undoPass();
        return;
      case "metronome":
        this.setMetronome(!this.state.metronome);
        return;
      case "exit":
        this.setOn(false);
        return;
    }
  };

  private onKeyUp = (e: KeyboardEvent): void => {
    const a = classifyKey({ code: e.code });
    if (a?.kind !== "pad") return;
    e.preventDefault();
    e.stopImmediatePropagation();
    this.release(a.code, e.timeStamp);
  };

  private onBlur = (): void => {
    for (const p of this.bank().pads) this.release(p.key, performance.now());
  };

  /** DOM nodes of the pads (lit straight from the key handler, no React). */
  registerPad(code: string, el: HTMLElement | null): void {
    if (el) this.padEls.set(code, el);
    else this.padEls.delete(code);
  }

  private light(code: string, on: boolean, accent = false): void {
    const el = this.padEls.get(code);
    if (!el) return;
    el.classList.toggle("hit", on);
    el.classList.toggle("accent", on && accent);
  }

  // ------------------------------------------------------------ playing

  /**
   * THE audio path: synchronous from the event to src.start(). `eventMs` is the
   * event's timeStamp (performance.now domain) — the latency is measured from it.
   */
  press(code: string, accent = false, eventMs: number = performance.now()): boolean {
    const pad = this.padAt(code);
    const bufs = this.buffers.get(code);
    const player = this.player ?? this.ensureAudio();
    if (!pad || !bufs || !player) {
      this.light(code, true);
      setTimeout(() => this.light(code, false), 90);
      return false;
    }
    const vel = accent ? (pad.accent ?? 1) : (pad.velocity ?? 0.8);
    const info = player.press(code, bufs, { gain: vel * 10 ** ((pad.gain_db ?? 0) / 20), pan: pad.pan ?? 0, group: pad.choke ?? null, eventMs });
    this.light(code, true, accent);
    if (pad.mode !== "gate") setTimeout(() => this.light(code, false), 110);
    const ctx = player.ctx as AudioContext;
    const heard = this.heardAt(ctx, eventMs);
    let beat: number | null = null;
    if (this.rec) beat = this.recordPress(code, pad, vel, heard);
    this.presses.push({ code, eventMs, schedMs: info.schedMs, when: info.when, heardCtx: heard, beat });
    if (this.presses.length > 2000) this.presses.splice(0, 1000);
    this.bump();
    return true;
  }

  release(code: string, eventMs: number = performance.now()): void {
    const pad = this.padAt(code);
    if (pad?.mode === "gate") {
      this.player?.release(code);
      this.light(code, false);
      if (this.rec && this.player) this.recordRelease(code, this.heardAt(this.player.ctx as AudioContext, eventMs));
    }
  }

  /** ctx time of what was coming out of the speakers at `eventMs`, minus the calibrated offset. */
  private heardAt(ctx: AudioContext, eventMs: number): number {
    const ts = ctx.getOutputTimestamp?.() ?? null;
    const raw = heardCtxTime(eventMs, ts && ts.contextTime ? { contextTime: ts.contextTime, performanceTime: ts.performanceTime ?? 0 } : null, ctx.currentTime, performance.now(), ctx.outputLatency || 0);
    return raw - (this.state.latency.calibratedMs ?? 0) / 1000;
  }

  // ------------------------------------------------------------ recording

  async toggleRecord(): Promise<void> {
    if (this.rec) await this.stopRecord();
    else await this.startRecord();
  }

  async startRecord(): Promise<void> {
    const song = this.editor.state.song;
    const ctx = this.engine.audioContext();
    if (!song || !ctx || this.rec) return;
    this.ensureAudio();
    const bpm = song.meta.bpm;
    const spb = 60 / bpm;
    const loop = this.editor.state.loop;
    const playing = this.engine.isPlaying() && this.engine.getSource() === "editor";
    const loopStartS = loop ? loop.startBeat * spb : 0;
    const loopLenS = loop ? (loop.endBeat - loop.startBeat) * spb : null;
    let startsAt = ctx.currentTime;
    let originBeat: number;
    let startCycle = 0;
    if (playing) {
      const clock = this.engine.editorClock();
      const u0 = clock ? heardUnwrapped(this.heardAt(ctx, performance.now()), clock) : 0;
      if (loopLenS) {
        originBeat = loop!.startBeat;
        startCycle = Math.floor(u0 / loopLenS);
      } else originBeat = Math.floor((loopStartS + u0) / spb / BEATS_PER_BAR) * BEATS_PER_BAR;
    } else {
      originBeat = loop ? loop.startBeat : Math.floor(this.editor.playheadBeat() / BEATS_PER_BAR) * BEATS_PER_BAR;
      const lead = this.state.countIn * BEATS_PER_BAR * spb;
      startsAt = ctx.currentTime + 0.12 + lead;
      if (this.state.countIn && this.state.metronome !== undefined) this.metro?.countIn(startsAt, this.state.countIn);
      this.engine.playEditorAt(startsAt, originBeat * spb);
    }
    const bank = this.bank();
    const take: TakeJson = {
      name: `Toma ${this.state.takes.length + 1}`,
      status: "recording",
      bpm,
      origin_beat: originBeat,
      loop_beats: loop ? loop.endBeat - loop.startBeat : null,
      length_beats: loop ? loop.endBeat - loop.startBeat : 4,
      bank: bank.id,
      pads: snapshotPads(bank.pads),
      events: [],
      quantize: { ...this.state.quantize },
      latency_ms: Math.round(((ctx.outputLatency || 0) + (this.state.latency.calibratedMs ?? 0) / 1000) * 1000 * 10) / 10,
    };
    this.rec = { take, loopLenS, loopStartS, originBeat, startCycle, held: new Map(), dirty: true, lastSave: 0, saving: null, lastCycle: 1, startsAt };
    if (this.state.metronome) this.metro?.start();
    this.set({ rec: this.recState() });
    writeLS(LS_INFLIGHT, { ...take, savedAt: Date.now() });
    await this.saveRec();
    this.saveTimer = setInterval(() => this.periodic(), 250);
  }

  private recordPress(code: string, pad: PadJson, vel: number, heard: number): number | null {
    const r = this.rec!;
    const clock = this.engine.editorClock();
    if (!clock) return null;
    const u = heardUnwrapped(heard, clock);
    const bpm = r.take.bpm;
    const { beat, cycle } = placePress(u, { loopStartS: r.loopStartS, loopLenS: r.loopLenS, originBeat: r.originBeat, startCycle: r.startCycle }, bpm);
    const ev: TakeEventJson = { beat: Math.round(beat * 1e6) / 1e6, pad: code, velocity: Math.round(vel * 1e4) / 1e4, cycle };
    r.take.events.push(ev);
    if (pad.mode === "gate") r.held.set(code, { ev, u });
    r.dirty = true;
    if (!r.loopLenS) r.take.length_beats = Math.max(r.take.length_beats, Math.ceil((beat + 0.01) / BEATS_PER_BAR) * BEATS_PER_BAR);
    this.mirrorLocal();
    return ev.beat;
  }

  private recordRelease(code: string, heard: number): void {
    const r = this.rec!;
    const h = r.held.get(code);
    const clock = this.engine.editorClock();
    if (!h || !clock) return;
    r.held.delete(code);
    const len = ((heardUnwrapped(heard, clock) - h.u) * r.take.bpm) / 60;
    h.ev.length_beats = Math.max(1 / 64, Math.min(64, Math.round(len * 1e6) / 1e6));
    r.dirty = true;
    this.mirrorLocal();
  }

  private lastMirror = 0;
  private mirrorTimer: ReturnType<typeof setTimeout> | null = null;
  /** Mirror the take to localStorage at most every 250 ms, always with a trailing write. */
  private mirrorLocal(): void {
    if (!this.rec) return;
    const now = Date.now();
    const write = () => {
      this.mirrorTimer = null;
      this.lastMirror = Date.now();
      if (this.rec) writeLS(LS_INFLIGHT, { ...this.rec.take, savedAt: this.lastMirror });
    };
    if (now - this.lastMirror >= 250) write();
    else if (!this.mirrorTimer) this.mirrorTimer = setTimeout(write, 250 - (now - this.lastMirror));
  }

  private periodic(): void {
    const r = this.rec;
    if (!r) return;
    const ctx = this.engine.audioContext();
    const clock = this.engine.editorClock();
    if (ctx && clock && r.loopLenS) {
      const u = heardUnwrapped(ctx.currentTime - (ctx.outputLatency || 0), clock);
      const cycle = Math.max(1, Math.floor(u / r.loopLenS) - r.startCycle + 1);
      if (cycle !== r.lastCycle) {
        r.lastCycle = cycle;
        r.dirty = true;
        void this.saveRec(); // a pass ended: save it
        this.bump();
        return;
      }
    }
    if (r.dirty && Date.now() - r.lastSave > SAVE_EVERY_MS) void this.saveRec();
    this.bump();
  }

  private async saveRec(): Promise<void> {
    const r = this.rec;
    if (!r || r.saving) return;
    r.dirty = false;
    r.lastSave = Date.now();
    const body = JSON.parse(JSON.stringify(r.take)) as TakeJson;
    r.saving = (async () => {
      try {
        const res = await this.app.client.tool<{ take: TakeSummary }>("pads_take_save", { take: body });
        if (!r.take.id) {
          r.take.id = res.take.id;
          r.take.name = res.take.name;
          r.take.created_at = res.take.created_at;
        }
        writeLS(LS_INFLIGHT, { ...r.take, savedAt: Date.now() });
      } catch (e) {
        r.dirty = true;
        this.app.toast(`No pude guardar la toma en el proyecto (queda en este navegador): ${friendlyError(e).message}`, "warn");
      } finally {
        r.saving = null;
      }
    })();
    await r.saving;
  }

  async stopRecord(): Promise<TakeJson | null> {
    const r = this.rec;
    if (!r) return null;
    if (this.saveTimer) clearInterval(this.saveTimer);
    this.saveTimer = null;
    if (this.player) for (const code of [...r.held.keys()]) this.recordRelease(code, this.heardAt(this.player.ctx as AudioContext, performance.now()));
    if (r.saving) await r.saving;
    this.rec = null;
    this.metro?.stop();
    r.take.status = "done";
    if (!r.take.loop_beats) {
      const last = r.take.events.reduce((m, e) => Math.max(m, e.beat + (e.length_beats ?? 0)), 0);
      r.take.length_beats = Math.max(4, Math.ceil((last + 0.01) / BEATS_PER_BAR) * BEATS_PER_BAR);
    }
    try {
      const res = await this.app.client.tool<{ take: TakeSummary }>("pads_take_save", { take: r.take });
      r.take.id = res.take.id;
      this.takeCache.set(res.take.id, JSON.parse(JSON.stringify(r.take)));
      writeLS(LS_INFLIGHT, null);
      await this.reloadTakes();
      this.set({ selectedTake: res.take.id, rec: this.recState() });
      this.app.toast(`Guardé «${res.take.name}»: ${r.take.events.length} golpes.`, "ok", undefined, 2500);
    } catch (e) {
      this.set({ rec: this.recState() });
      this.app.toast(`La toma quedó en este navegador; no pude guardarla: ${friendlyError(e).message}`, "warn");
    }
    return r.take;
  }

  async undoPass(): Promise<void> {
    const r = this.rec;
    if (r) {
      const { take, removed, cycle } = undoLastPass(r.take);
      if (!removed) return;
      r.take = take;
      r.dirty = true;
      this.mirrorLocal();
      void this.saveRec();
      this.app.toast(`Saqué la pasada ${cycle} (${removed} golpes).`, "info", undefined, 2000);
      this.bump();
      return;
    }
    const id = this.state.selectedTake;
    const full = id ? await this.loadTake(id) : null;
    if (!full) return;
    const { take, removed, cycle } = undoLastPass(full);
    if (!removed) return;
    await this.saveTake(take);
    this.app.toast(`Saqué la pasada ${cycle} de «${take.name}» (${removed} golpes).`, "info", undefined, 2500);
  }

  setMetronome(on: boolean): void {
    if (this.metro) {
      this.metro.enabled = on;
      if (on && this.engine.isPlaying()) this.metro.start();
      if (!on) this.metro.stop();
    }
    this.set({ metronome: on });
  }

  setCountIn(n: 0 | 1 | 2): void {
    this.set({ countIn: n });
  }

  setQuantize(q: Partial<QuantizeJson>): void {
    this.set({ quantize: { ...this.state.quantize, ...q } });
  }

  // ------------------------------------------------------------ takes

  async loadTake(id: string): Promise<TakeJson | null> {
    const have = this.takeCache.get(id);
    if (have) return have;
    try {
      const res = await this.app.client.tool<{ take: TakeJson }>("pads_take_get", { take_id: id });
      this.takeCache.set(id, res.take);
      return res.take;
    } catch {
      return null;
    }
  }

  async saveTake(take: TakeJson): Promise<void> {
    await this.app.client.tool("pads_take_save", { take });
    if (take.id) this.takeCache.set(take.id, take);
    await this.reloadTakes();
    if (this.state.auditioning === take.id) void this.audition(take.id);
  }

  selectTake(id: string | null): void {
    this.set({ selectedTake: id });
  }

  async renameTake(id: string, name: string): Promise<void> {
    const t = await this.loadTake(id);
    if (t && name.trim()) await this.saveTake({ ...t, name: name.trim().slice(0, 80) });
  }

  async setTakeQuantize(id: string, q: QuantizeJson): Promise<void> {
    const t = await this.loadTake(id);
    if (t) await this.saveTake({ ...t, quantize: q });
  }

  async deleteTake(id: string): Promise<void> {
    if (this.state.auditioning === id) this.stopAudition();
    await this.app.client.tool("pads_take_delete", { take_id: id });
    this.takeCache.delete(id);
    await this.reloadTakes();
    if (this.state.selectedTake === id) this.set({ selectedTake: null });
    this.app.toast("Borré la toma (queda guardada en pads/takes/deleted por las dudas).", "info", undefined, 3000);
  }

  /** The take as a virtual drum track (exactly what pads.commit would write). */
  takeTrack(take: TakeJson, atBar?: number): TrackJson {
    const lanes: Record<string, import("../editor/song").LaneJson> = {};
    const hits: import("../editor/song").FreeHitJson[] = [];
    const laneOf = new Map<string, string[]>();
    for (const [code, p] of Object.entries(take.pads)) {
      if (p.note) {
        laneOf.set(code, p.note.pitches.map((pitch, i) => {
          const n = `n-${code.toLowerCase()}-${i}`;
          lanes[n] = { sample: `note://${p.note!.track}/${Math.max(0, Math.min(127, pitch + Math.round(p.pitch_semitones ?? 0)))}`, gain_db: p.gain_db ?? 0, pan: p.pan ?? 0 };
          return n;
        }));
      } else {
        const n = `p-${code.toLowerCase()}`;
        lanes[n] = { sample: p.sample ?? null, slice: p.slice ?? null, gain_db: p.gain_db ?? 0, pan: p.pan ?? 0, pitch_semitones: p.pitch_semitones ?? 0, reverse: !!p.reverse, choke: p.choke ?? null };
        laneOf.set(code, [n]);
      }
    }
    const span = takeSpan(take);
    const song = this.editor.state.song!;
    const start = atBar != null ? (atBar - 1) * BEATS_PER_BAR : take.origin_beat;
    const end = song.meta.length_bars * BEATS_PER_BAR;
    const passes = take.loop_beats ? Math.max(1, Math.ceil((end - start) / span)) : 1;
    for (let k = 0; k < passes; k++)
      for (const e of processEvents(take)) {
        if (e.beat < 0 || e.beat >= span - 1e-9) continue;
        const b0 = start + k * span + e.beat;
        if (b0 >= end) continue;
        const pad = take.pads[e.pad];
        for (const lane of laneOf.get(e.pad) ?? []) {
          const len = pad.mode === "gate" ? (e.lengthBeats ?? (pad.note ? 0.5 : null)) : pad.note ? 0.5 : null;
          hits.push({ beat: Math.round((b0 + 1) * 1e6) / 1e6, lane, velocity: e.velocity, ...(len ? { length_beats: len } : {}) });
        }
      }
    return { id: "__take_preview__", pattern: { steps_per_bar: 16, bars: 1, lanes, hits } };
  }

  /** Hear a take over the base (looping its span), without touching the song. */
  async audition(id: string): Promise<void> {
    const take = await this.loadTake(id);
    const song = this.editor.state.song;
    if (!take || !song) return;
    const sounds = Object.values(take.pads).map(padSound).filter((s): s is NonNullable<typeof s> => !!s);
    const notes = Object.values(take.pads).flatMap((p) => (p.note ? p.note.pitches.map((pitch) => ({ track: p.note!.track, pitch: Math.max(0, Math.min(127, pitch + Math.round(p.pitch_semitones ?? 0))), seconds: 4 })) : []));
    await this.editor.soundBuffers(sounds);
    const nb = await this.editor.noteBuffers(notes);
    for (const [k, b] of nb) this.editor.addOneshot(`note://${k.replace(":", "/")}`, b);
    this.editor.setPreviewTrack(this.takeTrack(take));
    this.set({ auditioning: id });
    const span = takeSpan(take);
    if (!this.editor.state.loop) this.editor.setLoop({ startBeat: take.origin_beat, endBeat: take.origin_beat + span });
    if (!this.engine.isPlaying()) {
      this.editor.seekBeat(take.origin_beat);
      this.editor.togglePlay();
    }
  }

  stopAudition(): void {
    this.editor.setPreviewTrack(null);
    this.set({ auditioning: null });
  }

  /**
   * "Usar esta toma": pads.commit computes the song.edit ops (dry run) and the
   * editor applies them like any edit (instant, undoable, persisted in order).
   */
  async commit(id: string, opts: { target: "new_track" | "track"; track?: string; at_bar: number; repeat?: number; until_bar?: number; quantize?: QuantizeJson; cleanup?: CleanupJson }): Promise<boolean> {
    try {
      const res = await this.app.client.tool<{ ops: Op[]; track: string; hits: number; notes: number; dropped: number; messages: string[] }>("pads_commit", { take_id: id, ...opts, dry_run: true });
      const take = await this.loadTake(id);
      const label = `usar «${take?.name ?? id}» en ${res.track}`;
      if (!this.editor.edit(res.ops, label, { track: res.track })) return false;
      if (this.state.auditioning === id) this.stopAudition();
      if (take) {
        const commits = [...(take.commits ?? []), { at: new Date().toISOString(), track: res.track, at_bar: opts.at_bar, repeat: opts.repeat ?? 1, hits: res.hits, notes: res.notes }];
        await this.saveTake({ ...take, commits, status: "done" });
      }
      const what = `${res.hits ? `${res.hits} golpes` : ""}${res.hits && res.notes ? " y " : ""}${res.notes ? `${res.notes} notas` : ""}`;
      this.app.toast(`Listo: ${what} en «${res.track}» (Ctrl+Z deshace).${res.messages.length ? " " + res.messages.join(" ") : ""}`, "ok", undefined, 5000);
      return true;
    } catch (e) {
      this.app.toast(`No pude usar la toma: ${friendlyError(e).message}`, "warn");
      return false;
    }
  }

  // ------------------------------------------------------------ latency

  refreshLatency(): void {
    const ctx = this.engine.audioContext();
    const outputS = ctx?.outputLatency || 0;
    const baseS = ctx?.baseLatency || 0;
    const sink = (ctx as (AudioContext & { sinkId?: string }) | null)?.sinkId;
    const device = deviceKey(typeof sink === "string" ? sink : null, outputS + baseS);
    const stored = readLS<Record<string, number>>(LS_CALIB) ?? {};
    const calibratedMs = stored[device] ?? null;
    const v = latencyVerdict(outputS + baseS, calibratedMs != null ? calibratedMs / 1000 : null);
    const s = [...(this.player?.sched ?? [])].sort((a, b) => a - b);
    const q = (f: number) => (s.length ? Math.round(s[Math.min(s.length - 1, Math.floor(f * s.length))] * 100) / 100 : null);
    const clock = this.engine.editorClock();
    this.set({ latency: { outputMs: Math.round(outputS * 1000 * 10) / 10, baseMs: Math.round(baseS * 1000 * 10) / 10, busMs: Math.round((clock?.busLatency ?? 0) * 1000 * 10) / 10, calibratedMs, device, level: v.level, text: v.text, schedP50: q(0.5), schedP95: q(0.95), schedMax: s.length ? Math.round(s[s.length - 1] * 100) / 100 : null } });
  }

  /** Tap along to 16 clicks at 100 BPM; the median offset is stored for this output. */
  startCalibration(): void {
    const ctx = this.engine.audioContext();
    const out = this.engine.padsOutput();
    if (!ctx || !out || this.calibRun) return;
    this.engine.unlock();
    const hi = clickBuffer(ctx, true);
    const lo = clickBuffer(ctx, false);
    const spb = 0.6;
    const t0 = ctx.currentTime + 0.6;
    const clicks: number[] = [];
    const n = 16;
    for (let i = 0; i < n; i++) {
      const s = ctx.createBufferSource();
      s.buffer = i % 4 === 0 ? hi : lo;
      s.connect(out);
      s.start(t0 + i * spb);
      clicks.push(t0 + i * spb);
    }
    this.calibRun = { clicks, taps: [], done: setTimeout(() => this.finishCalibration(), (0.6 + n * spb + 0.5) * 1000) };
    this.set({ calib: { phase: "running", taps: 0, total: n, resultMs: null, spreadMs: null } });
  }

  /** A tap during calibration (key or button), at its event time. */
  calibTap(eventMs: number): void {
    const r = this.calibRun;
    const ctx = this.engine.audioContext();
    if (!r || !ctx) return;
    const ts = ctx.getOutputTimestamp?.() ?? null;
    r.taps.push(heardCtxTime(eventMs, ts && ts.contextTime ? { contextTime: ts.contextTime, performanceTime: ts.performanceTime ?? 0 } : null, ctx.currentTime, performance.now(), ctx.outputLatency || 0));
    this.set({ calib: { ...this.state.calib, taps: r.taps.length } });
  }

  get calibrating(): boolean {
    return !!this.calibRun;
  }

  private finishCalibration(): void {
    const r = this.calibRun;
    this.calibRun = null;
    if (!r) return;
    const res = calibrate(r.taps, r.clicks);
    if (!res) {
      this.set({ calib: { ...this.state.calib, phase: "failed" } });
      return;
    }
    const stored = readLS<Record<string, number>>(LS_CALIB) ?? {};
    stored[this.state.latency.device] = Math.round(res.offsetS * 1000 * 10) / 10;
    writeLS(LS_CALIB, stored);
    this.set({ calib: { phase: "done", taps: r.taps.length, total: r.clicks.length, resultMs: Math.round(res.offsetS * 1000), spreadMs: Math.round(res.spreadS * 1000) } });
    this.refreshLatency();
  }

  /** Forget the calibration of this output. */
  resetCalibration(): void {
    const stored = readLS<Record<string, number>>(LS_CALIB) ?? {};
    delete stored[this.state.latency.device];
    writeLS(LS_CALIB, stored);
    this.refreshLatency();
  }

  /** Test/diagnostic hook: set the calibrated offset (ms) for this output. */
  setCalibrationMs(ms: number | null): void {
    const stored = readLS<Record<string, number>>(LS_CALIB) ?? {};
    if (ms == null) delete stored[this.state.latency.device];
    else stored[this.state.latency.device] = ms;
    writeLS(LS_CALIB, stored);
    this.refreshLatency();
  }

  /** Recording in progress (tests). */
  get recording(): TakeJson | null {
    return this.rec?.take ?? null;
  }

  dispose(): void {
    this.setOn(false);
    this.player?.dispose();
    this.metro?.dispose();
  }
}

function stripLocal(t: TakeJson & { savedAt?: number }): TakeJson {
  const { savedAt: _s, ...rest } = t;
  void _s;
  return rest;
}
