// App store: a small observable state container plus the actions that
// orchestrate API client <-> audio engine. React subscribes with
// useSyncExternalStore; the audio engine is driven imperatively, so audio
// never depends on React re-renders. The engine and the client are
// injected (component tests pass a fake engine and a client on the mock).

import { MuestraClient, ApiError } from "../api/client";
import type {
  Analysis,
  ChopAdjust,
  ChopCandidateSet,
  FeedbackEvent,
  Job,
  Metrics,
  RenderMeta,
  VersionId,
  VersionInfo,
} from "../api/types";
import type { AbsLoop, AudioEngine, DeckSpec } from "../audio/engine";
import { type MixerState, EMPTY_MIXER, patchLane, patchTrack, type MixerLane, type MixerTrack } from "../audio/mixer";
import { computePeaks, peaksFromPayload } from "../audio/signal";
import * as fb from "../feedback/events";
import { BEATS_PER_BAR, defaultLoopAt, secondsToBeats, stepSeek, type Timing } from "../music/time";
import { loudnessOffsets, sortVersions, versionNumber } from "./derive";

/** The engine surface the store uses (AudioEngine implements it; tests fake it). */
export type PlaybackEngine = Pick<
  AudioEngine,
  | "subscribe"
  | "getSnapshot"
  | "register"
  | "prepare"
  | "select"
  | "setPartner"
  | "setInitial"
  | "play"
  | "pause"
  | "toggle"
  | "seek"
  | "getPosition"
  | "getDuration"
  | "isPlaying"
  | "setLoop"
  | "setVolume"
  | "setLevels"
  | "setMixer"
  | "playPreview"
  | "stopPreview"
  | "getPreviewPosition"
  | "unlock"
  | "clearError"
>;

export interface Toast {
  id: number;
  kind: "info" | "ok" | "warn" | "error";
  text: string;
  action?: { label: string; run: () => void };
}

export type SendStatus = "sending" | "error";

export interface CommentDraft {
  atS: number;
  version: VersionId | null;
  track: string | null;
}

export interface BlindState {
  x: VersionId;
  y: VersionId;
  /** set once the user picked: reveals the mapping */
  picked: "x" | "y" | null;
}

export type Theme = "dark" | "light";
export type NewVersionPolicy = "play" | "offer";

export interface AppState {
  boot: "loading" | "ready" | "empty" | "error";
  bootError: string | null;
  title: string | null;
  versions: VersionInfo[]; // sorted ascending
  metas: Record<VersionId, RenderMeta>;
  metrics: Record<VersionId, Metrics | null>;
  peaks: Record<VersionId, Float32Array>;
  feedback: FeedbackEvent[];
  sendStatus: Record<string, SendStatus>;
  chopSets: ChopCandidateSet[];
  /** chops declared in song.yaml (suggests the chop id on a manual choose) */
  songChops: { id: string; source: string }[];
  analyses: Record<string, Analysis>;
  jobs: Record<string, Job>;
  ws: "connecting" | "open" | "closed";
  /** The A/B pair. `active` is the one you hear. */
  a: VersionId | null;
  b: VersionId | null;
  active: VersionId | null;
  blind: BlindState | null;
  loudnessMatch: boolean;
  loopOn: boolean;
  loopRegion: AbsLoop | null;
  volume: number;
  mixer: MixerState;
  theme: Theme;
  newVersionPolicy: NewVersionPolicy;
  /** "v004 está lista" banner */
  offer: VersionId | null;
  draft: CommentDraft | null;
  toasts: Toast[];
}

const PREFS_KEY = "muestra.prefs.v1";

interface Prefs {
  theme: Theme;
  newVersionPolicy: NewVersionPolicy;
  loudnessMatch: boolean;
  volume: number;
}

function loadPrefs(): Partial<Prefs> {
  try {
    return JSON.parse(globalThis.localStorage?.getItem(PREFS_KEY) ?? "{}") as Partial<Prefs>;
  } catch {
    return {};
  }
}

function savePrefs(p: Prefs): void {
  try {
    globalThis.localStorage?.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* private mode */
  }
}

export function timingOf(meta: RenderMeta | undefined): Timing | null {
  if (!meta) return null;
  const durationS =
    meta.duration_s && meta.duration_s > 0
      ? meta.duration_s
      : ((meta.range_bars[1] - meta.range_bars[0]) * BEATS_PER_BAR * 60) / meta.bpm;
  return { bpm: meta.bpm, firstBar: meta.range_bars?.[0] ?? 1, durationS };
}

function specOf(meta: RenderMeta): DeckSpec {
  const t = timingOf(meta)!;
  return {
    version: meta.version,
    bpm: t.bpm,
    firstBar: t.firstBar,
    durationS: t.durationS,
    sampleRate: meta.sample_rate,
    mix: meta.mix,
    tracks: meta.tracks.map((tr) => ({ id: tr.id, stem: tr.stem, lanes: tr.lanes ?? [] })),
  };
}

const PEAK_BUCKETS = 2400;
let toastSeq = 0;

export class Store {
  state: AppState;
  readonly client: MuestraClient;
  readonly engine: PlaybackEngine;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private wsRetry = 0;
  private wsTimer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;
  private feedbackKeys = new Set<string>();
  private WebSocketImpl: typeof WebSocket | null;

  constructor(opts: { client: MuestraClient; engine: PlaybackEngine; WebSocketImpl?: typeof WebSocket | null }) {
    this.client = opts.client;
    this.engine = opts.engine;
    this.WebSocketImpl = opts.WebSocketImpl ?? (typeof WebSocket !== "undefined" ? WebSocket : null);
    const prefs = loadPrefs();
    this.state = {
      boot: "loading",
      bootError: null,
      title: null,
      versions: [],
      metas: {},
      metrics: {},
      peaks: {},
      feedback: [],
      sendStatus: {},
      chopSets: [],
      songChops: [],
      analyses: {},
      jobs: {},
      ws: "connecting",
      a: null,
      b: null,
      active: null,
      blind: null,
      loudnessMatch: prefs.loudnessMatch ?? true, // default ON: louder always sounds "better"
      loopOn: false,
      loopRegion: null,
      volume: prefs.volume ?? 0.85,
      mixer: EMPTY_MIXER,
      theme: prefs.theme ?? "dark",
      newVersionPolicy: prefs.newVersionPolicy ?? "offer",
      offer: null,
      draft: null,
      toasts: [],
    };
    this.engine.setVolume(this.state.volume);
  }

  // ------------------------------------------------------------ infra

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getState = (): AppState => this.state;

  private set(p: Partial<AppState>): void {
    this.state = { ...this.state, ...p };
    this.listeners.forEach((fn) => fn());
  }

  private persist(): void {
    const s = this.state;
    savePrefs({ theme: s.theme, newVersionPolicy: s.newVersionPolicy, loudnessMatch: s.loudnessMatch, volume: s.volume });
  }

  toast(text: string, kind: Toast["kind"] = "info", action?: Toast["action"], ms = 4500): void {
    const t: Toast = { id: ++toastSeq, kind, text, action };
    this.set({ toasts: [...this.state.toasts.slice(-3), t] });
    setTimeout(() => this.dismissToast(t.id), ms);
  }

  dismissToast(id: number): void {
    if (this.state.toasts.some((t) => t.id === id)) this.set({ toasts: this.state.toasts.filter((t) => t.id !== id) });
  }

  /** Engine callback: a mix finished decoding → waveform peaks. */
  onMixDecoded = (version: VersionId, channels: Float32Array[]): void => {
    if (this.state.peaks[version]) return;
    this.set({ peaks: { ...this.state.peaks, [version]: computePeaks(channels, PEAK_BUCKETS) } });
  };

  timing(v: VersionId | null = this.state.active): Timing | null {
    return v ? timingOf(this.state.metas[v]) : null;
  }

  // ------------------------------------------------------------ boot / sync

  async init(): Promise<void> {
    this.set({ boot: "loading", bootError: null });
    try {
      await this.loadAll();
      this.set({ boot: this.state.versions.length ? "ready" : "empty" });
    } catch (e) {
      this.set({ boot: "error", bootError: e instanceof Error ? e.message : String(e) });
    }
    this.connectWs();
  }

  /** Full (re)load — also used after a websocket reconnect, where events
   *  may have been missed. */
  async loadAll(): Promise<void> {
    const summary = await this.client.getSummary();
    let versions: VersionInfo[] = summary?.versions ?? [];
    let title = summary?.title ?? null;
    if (!summary?.versions) {
      const [list, project, song] = await Promise.all([
        this.client.listVersions().catch((e: unknown) => e),
        this.client.openProject().catch(() => null),
        this.client.getSong().catch(() => null),
      ]);
      title = song?.song?.meta?.title ?? project?.project_path?.split(/[\\/]/).filter(Boolean).pop() ?? null;
      const chops = (song?.song as { chops?: { id?: unknown; source?: unknown }[] } | undefined)?.chops;
      if (Array.isArray(chops)) {
        this.set({
          songChops: chops.filter((c) => typeof c.id === "string").map((c) => ({ id: String(c.id), source: String(c.source ?? "") })),
        });
      }
      if (list instanceof Error || list == null) {
        // versions.list unavailable (e.g. still a stub): fall back to the
        // render dirs project.open reports.
        if (!project) throw list instanceof Error ? list : new Error("Sin respuesta de la API");
        versions = (project.versions ?? []).map((v) => ({ version: v }));
      } else {
        versions = (list as { versions: VersionInfo[] }).versions;
      }
    }
    versions = sortVersions(versions);
    const metas: Record<VersionId, RenderMeta> = { ...(summary?.metas ?? {}) };
    const metrics: Record<VersionId, Metrics | null> = { ...(summary?.metrics ?? {}) };
    await Promise.all(
      versions.map(async (v) => {
        const [m, x] = await Promise.all([
          metas[v.version] ? metas[v.version] : this.client.getMeta(v.version).catch(() => null),
          v.version in metrics ? metrics[v.version] : this.client.getMetrics(v.version).catch(() => null),
        ]);
        if (m) metas[v.version] = m;
        metrics[v.version] = x ?? null;
      }),
    );
    // Only versions with a render (meta.json) are playable.
    versions = versions
      .filter((v) => metas[v.version])
      .map((v) => ({
        ...v,
        parent: v.parent ?? metas[v.version].parent ?? null,
        created_at: v.created_at ?? metas[v.version].created_at,
        summary: v.summary ?? metas[v.version].summary ?? null,
      }));
    for (const v of versions) this.engine.register(specOf(metas[v.version]));
    this.set({ title: title ?? this.state.title, versions, metas, metrics });
    await Promise.all([this.loadFeedback(), this.loadChops(summary?.analyses)]);
    if (!this.state.active && versions.length) {
      const latest = versions[versions.length - 1].version;
      const parent = metas[latest]?.parent;
      const other = parent && metas[parent] ? parent : versions.length > 1 ? versions[versions.length - 2].version : null;
      this.set({ a: latest, b: other, active: latest });
      this.engine.setInitial(latest);
      this.engine.setPartner(other);
      this.applyLevels();
      void this.engine.prepare(latest, { stems: true }).catch(() => undefined);
    }
    void this.fetchRemotePeaks();
  }

  private async fetchRemotePeaks(): Promise<void> {
    for (const v of [this.state.active, this.state.b].filter((x): x is VersionId => !!x)) {
      const meta = this.state.metas[v];
      if (!meta || this.state.peaks[v]) continue;
      const p = await this.client.getPeaks(meta.mix, PEAK_BUCKETS).catch(() => null);
      if (!p) return; // endpoint missing: local decode will provide them
      if (!this.state.peaks[v]) this.set({ peaks: { ...this.state.peaks, [v]: peaksFromPayload(p.peaks) } });
    }
  }

  async loadFeedback(): Promise<void> {
    try {
      const res = await this.client.readFeedback();
      const keys = new Set(res.events.map(fb.eventKey));
      // keep optimistic events still in flight
      const pending = this.state.feedback.filter((e) => this.state.sendStatus[fb.eventKey(e)] && !keys.has(fb.eventKey(e)));
      this.feedbackKeys = new Set([...keys, ...pending.map(fb.eventKey)]);
      this.set({ feedback: [...res.events, ...pending] });
    } catch (e) {
      this.toast(`No pude leer el feedback: ${errMsg(e)}`, "warn");
    }
  }

  async loadChops(analyses?: Record<string, Analysis>): Promise<void> {
    try {
      const res = await this.client.listChopSets();
      const sets = [...res.sets].sort((a, b) => b.set_id.localeCompare(a.set_id));
      const known = { ...this.state.analyses, ...(analyses ?? {}) };
      const missing = [...new Set(sets.map((s) => s.source_id))].filter((id) => !known[id]);
      await Promise.all(
        missing.map(async (id) => {
          const a = await this.client.getAnalysis(id).catch(() => null);
          if (a) known[id] = a;
        }),
      );
      this.set({ chopSets: sets, analyses: known });
    } catch {
      /* chops.list unavailable: the panel shows its empty state */
    }
  }

  // ------------------------------------------------------------ websocket

  private connectWs(): void {
    if (this.destroyed || !this.WebSocketImpl) return;
    let ws: WebSocket;
    try {
      ws = new this.WebSocketImpl(this.client.eventsUrl());
    } catch {
      this.scheduleWs();
      return;
    }
    this.ws = ws;
    this.set({ ws: "connecting" });
    let opened = false;
    ws.onopen = () => {
      opened = true;
      const wasRetry = this.wsRetry > 0;
      this.wsRetry = 0;
      this.set({ ws: "open" });
      // Events may have been missed while disconnected: resync.
      if (wasRetry) void this.loadAll().then(() => this.set({ boot: this.state.versions.length ? "ready" : "empty" }));
    };
    ws.onmessage = (ev) => this.onWsMessage(ev.data);
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* closing */
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.set({ ws: "closed" });
      if (!opened) this.wsRetry++;
      this.scheduleWs();
    };
  }

  private scheduleWs(): void {
    if (this.destroyed) return;
    const delay = Math.min(10_000, 800 * 2 ** Math.min(this.wsRetry, 4)) + Math.random() * 300;
    this.wsRetry++;
    this.wsTimer = setTimeout(() => this.connectWs(), delay);
  }

  /** Manual "reconectar" from the status pill. */
  reconnect(): void {
    if (this.wsTimer) clearTimeout(this.wsTimer);
    this.ws?.close();
    this.wsRetry = Math.max(1, this.wsRetry);
    this.connectWs();
  }

  onWsMessage(raw: unknown): void {
    const e = MuestraClient.parseWsEvent(raw);
    if (!e) return;
    switch (e.type) {
      case "job.updated": {
        const job = e.data as unknown as Job;
        if (!job?.id) return;
        this.set({ jobs: { ...this.state.jobs, [job.id]: job } });
        if (job.status === "done") setTimeout(() => this.dismissJob(job.id), 5000);
        break;
      }
      case "version.created": {
        const d = e.data as { version?: VersionId; meta?: RenderMeta };
        if (d.version) void this.onNewVersion(d.version, d.meta ?? null);
        break;
      }
      case "feedback.appended": {
        const ev = MuestraClient.feedbackFromWs(e.data);
        if (ev) this.mergeFeedback(ev);
        break;
      }
      case "candidates.created":
        void this.loadChops().then(() => this.toast("El agente propuso candidatos de chop nuevos", "info"));
        break;
      case "song.changed":
        void this.client
          .getSong()
          .then((s) => s?.song?.meta?.title && this.set({ title: s.song.meta.title }))
          .catch(() => undefined);
        break;
    }
  }

  dismissJob(id: string): void {
    if (!this.state.jobs[id]) return;
    const jobs = { ...this.state.jobs };
    delete jobs[id];
    this.set({ jobs });
  }

  private mergeFeedback(ev: FeedbackEvent): void {
    const k = fb.eventKey(ev);
    if (this.feedbackKeys.has(k)) {
      if (this.state.sendStatus[k]) {
        const s = { ...this.state.sendStatus };
        delete s[k];
        this.set({ sendStatus: s });
      }
      return;
    }
    this.feedbackKeys.add(k);
    this.set({ feedback: [...this.state.feedback, ev] });
  }

  async onNewVersion(version: VersionId, meta: RenderMeta | null): Promise<void> {
    if (this.state.metas[version]) return;
    const m = meta ?? (await this.client.getMeta(version).catch(() => null));
    if (!m) return;
    const metrics = await this.client.getMetrics(version).catch(() => null);
    this.engine.register(specOf(m));
    const info: VersionInfo = { version, parent: m.parent ?? null, created_at: m.created_at, summary: m.summary ?? null };
    const versions = sortVersions([...this.state.versions.filter((v) => v.version !== version), info]);
    this.set({
      versions,
      metas: { ...this.state.metas, [version]: m },
      metrics: { ...this.state.metrics, [version]: metrics },
      boot: "ready",
    });
    if (!this.state.active) {
      this.set({ a: version, active: version });
      this.engine.setInitial(version);
      void this.engine.prepare(version, { stems: true }).catch(() => undefined);
      return;
    }
    // Preload so either choice below is instant.
    void this.engine.prepare(version, { stems: false }).catch(() => undefined);
    const canAutoplay = this.engine.getSnapshot().audio === "running";
    if (this.state.newVersionPolicy === "play" && canAutoplay && !this.state.blind) {
      await this.compareNew(version, true);
      this.toast(`${version} sonando · B = ${this.state.b ?? "—"}`, "ok");
    } else {
      this.set({ offer: version });
    }
  }

  /** Put a new version as A against the one you were hearing as B. */
  async compareNew(version: VersionId, play = false): Promise<void> {
    const current = this.state.active;
    this.set({ offer: null });
    if (current && current !== version) this.setPair(version, current);
    await this.listen(version);
    if (play && !this.engine.isPlaying()) this.engine.play();
  }

  dismissOffer(): void {
    this.set({ offer: null });
  }

  // ------------------------------------------------------------ A/B

  private setPair(a: VersionId | null, b: VersionId | null): void {
    this.set({ a, b: b === a ? null : b });
    this.applyLevels();
    const other = this.state.active === a ? b : a;
    this.engine.setPartner(other);
  }

  /** Hear `v` now (gapless while playing). If it is not in the pair it
   *  replaces the side you are currently hearing. */
  async listen(v: VersionId): Promise<void> {
    if (!this.state.metas[v]) return;
    const { a, b, active } = this.state;
    if (v !== a && v !== b) {
      if (active === b && b) this.set({ b: v });
      else this.set({ a: v, b: b === v ? null : b });
    }
    this.set({ active: v });
    this.applyLevels();
    try {
      await this.engine.select(v);
    } catch (e) {
      this.toast(`No pude cargar ${v}: ${errMsg(e)}`, "error");
    }
    const s = this.state;
    this.engine.setPartner(s.active === s.a ? s.b : s.a);
    this.clampLoopToActive();
  }

  setA(v: VersionId): void {
    if (this.state.blind) return;
    if (v === this.state.b) this.setPair(v, this.state.a);
    else this.setPair(v, this.state.b);
    if (this.state.active !== this.state.a && this.state.active !== this.state.b) void this.listen(v);
  }

  setB(v: VersionId): void {
    if (this.state.blind) return;
    if (v === this.state.a) this.setPair(this.state.b, v);
    else this.setPair(this.state.a, v);
    if (this.state.active !== this.state.a && this.state.active !== this.state.b) void this.listen(v);
    this.engine.setPartner(this.state.active === this.state.a ? this.state.b : this.state.a);
  }

  /** The A/B key: flip to the other side of the pair. */
  async flip(): Promise<void> {
    const { a, b, active } = this.state;
    if (!a || !b) {
      this.toast("Elegí una versión B para comparar (botón «B» en la lista)", "info");
      return;
    }
    await this.listen(active === a ? b : a);
  }

  async hearSide(side: "a" | "b"): Promise<void> {
    const v = this.state[side];
    if (v) await this.listen(v);
  }

  /** Blind A/B: X/Y randomly assigned to the pair; nothing reveals which. */
  startBlind(rand: () => number = Math.random): void {
    const { a, b } = this.state;
    if (!a || !b) {
      this.toast("Para el modo ciego elegí dos versiones (A y B)", "warn");
      return;
    }
    const x = rand() < 0.5 ? a : b;
    const y = x === a ? b : a;
    this.set({ blind: { x, y, picked: null } });
  }

  stopBlind(): void {
    this.set({ blind: null });
  }

  async hearBlind(slot: "x" | "y"): Promise<void> {
    const bl = this.state.blind;
    if (bl) await this.listen(bl[slot]);
  }

  async flipBlind(): Promise<void> {
    const bl = this.state.blind;
    if (!bl) return;
    await this.listen(this.state.active === bl.x ? bl.y : bl.x);
  }

  /** Record which one won (ab_choice). In blind mode reveals the mapping. */
  async chooseWinner(winner: VersionId): Promise<void> {
    const { a, b, blind } = this.state;
    const pa = blind ? blind.x : a;
    const pb = blind ? blind.y : b;
    if (!pa || !pb) return;
    const ok = await this.append(fb.abChoice(pa, pb, winner));
    if (!ok) return;
    if (blind) {
      this.set({ blind: { ...blind, picked: winner === blind.x ? "x" : "y" } });
    } else {
      this.toast(`Anotado: preferís ${winner}`, "ok");
    }
  }

  setLoudnessMatch(on: boolean): void {
    this.set({ loudnessMatch: on });
    this.applyLevels();
    this.persist();
  }

  /** Current loudness-match offsets (dB) or null if not computable. */
  levelOffsets(): Record<VersionId, number> | null {
    const { a, b, metrics } = this.state;
    if (!a || !b) return null;
    return loudnessOffsets({
      [a]: metrics[a]?.loudness.integrated_lufs,
      [b]: metrics[b]?.loudness.integrated_lufs,
    });
  }

  private applyLevels(): void {
    const off = this.state.loudnessMatch ? this.levelOffsets() : null;
    this.engine.setLevels(new Map(Object.entries(off ?? {})));
  }

  // ------------------------------------------------------------ transport

  togglePlay(): void {
    this.engine.toggle();
  }

  seek(s: number): void {
    this.engine.seek(s);
  }

  seekBy(dir: 1 | -1, unit: "beat" | "bar"): void {
    const t = this.timing();
    if (!t) return;
    this.engine.seek(stepSeek(this.engine.getPosition(), dir, unit === "bar" ? BEATS_PER_BAR : 1, t));
  }

  setVolume(v: number): void {
    const vol = Math.min(1, Math.max(0, v));
    this.engine.setVolume(vol);
    this.set({ volume: vol });
    this.persist();
  }

  /** Loop region in render-relative beats of the active version. */
  relLoop(): { startBeat: number; endBeat: number } | null {
    const t = this.timing();
    const r = this.state.loopRegion;
    if (!t || !r) return null;
    const off = (t.firstBar - 1) * BEATS_PER_BAR;
    return { startBeat: r.startBeat - off, endBeat: r.endBeat - off };
  }

  toggleLoop(): void {
    const t = this.timing();
    if (!t) return;
    if (this.state.loopOn) {
      this.set({ loopOn: false });
      this.engine.setLoop(null);
      return;
    }
    let region = this.state.loopRegion;
    if (!region) {
      const rel = defaultLoopAt(this.engine.getPosition(), t);
      const off = (t.firstBar - 1) * BEATS_PER_BAR;
      region = { startBeat: rel.startBeat + off, endBeat: rel.endBeat + off };
    }
    this.set({ loopOn: true, loopRegion: region });
    this.engine.setLoop(region);
  }

  /** Set the loop from render-relative beats (already snapped). */
  setLoopRel(rel: { startBeat: number; endBeat: number }, enable = true): void {
    const t = this.timing();
    if (!t) return;
    const off = (t.firstBar - 1) * BEATS_PER_BAR;
    const region = { startBeat: rel.startBeat + off, endBeat: rel.endBeat + off };
    this.set({ loopRegion: region, loopOn: enable || this.state.loopOn });
    if (this.state.loopOn) this.engine.setLoop(region);
  }

  private clampLoopToActive(): void {
    const t = this.timing();
    const r = this.state.loopRegion;
    if (!t || !r) return;
    const off = (t.firstBar - 1) * BEATS_PER_BAR;
    const max = off + secondsToBeats(t.durationS, t.bpm);
    if (r.startBeat >= max || r.endBeat <= off) {
      this.set({ loopRegion: null, loopOn: false });
      this.engine.setLoop(null);
    }
  }

  // ------------------------------------------------------------ mixer

  setTrack(id: string, patch: Partial<MixerTrack>): void {
    const mixer = patchTrack(this.state.mixer, id, patch);
    this.set({ mixer });
    this.engine.setMixer(mixer);
  }

  setLane(trackId: string, laneId: string, patch: Partial<MixerLane>): void {
    const mixer = patchLane(this.state.mixer, trackId, laneId, patch);
    this.set({ mixer });
    this.engine.setMixer(mixer);
  }

  resetMixer(): void {
    this.set({ mixer: EMPTY_MIXER });
    this.engine.setMixer(EMPTY_MIXER);
  }

  // ------------------------------------------------------------ feedback

  /** Optimistic append through `feedback.append`. Returns success. */
  async append(event: FeedbackEvent): Promise<boolean> {
    const k = fb.eventKey(event);
    if (!this.feedbackKeys.has(k)) {
      this.feedbackKeys.add(k);
      this.set({ feedback: [...this.state.feedback, event] });
    }
    this.set({ sendStatus: { ...this.state.sendStatus, [k]: "sending" } });
    try {
      await this.client.appendFeedback(event);
      const s = { ...this.state.sendStatus };
      delete s[k];
      this.set({ sendStatus: s });
      return true;
    } catch (e) {
      this.set({ sendStatus: { ...this.state.sendStatus, [k]: "error" } });
      this.toast(`No se guardó: ${errMsg(e)}`, "error", { label: "Reintentar", run: () => void this.append(event) }, 8000);
      return false;
    }
  }

  /** Drop an event that failed to send. */
  discard(event: FeedbackEvent): void {
    const k = fb.eventKey(event);
    if (this.state.sendStatus[k] !== "error") return;
    this.feedbackKeys.delete(k);
    const s = { ...this.state.sendStatus };
    delete s[k];
    this.set({ feedback: this.state.feedback.filter((e) => fb.eventKey(e) !== k), sendStatus: s });
  }

  async rate(version: VersionId, value: "like" | "dislike"): Promise<void> {
    await this.append(fb.rating(version, value));
  }

  /** M key: freeze the playhead and open the quick comment box. */
  openDraft(atS: number | null = this.engine.getPosition(), track: string | null = null): void {
    if (!this.state.active) return;
    this.set({ draft: { atS: atS ?? 0, version: this.state.active, track } });
  }

  updateDraft(p: Partial<CommentDraft>): void {
    if (this.state.draft) this.set({ draft: { ...this.state.draft, ...p } });
  }

  closeDraft(): void {
    this.set({ draft: null });
  }

  async submitDraft(text: string): Promise<boolean> {
    const d = this.state.draft;
    if (!d || !text.trim()) return false;
    this.set({ draft: null });
    return this.append(fb.comment({ text, version: d.version, atS: d.atS, track: d.track }));
  }

  async generalComment(text: string, version: VersionId | null): Promise<boolean> {
    if (!text.trim()) return false;
    return this.append(fb.comment({ text, version }));
  }

  // ------------------------------------------------------------ chops

  async chooseChop(set: ChopCandidateSet, candId: string, chopId: string, adjust: ChopAdjust | null): Promise<boolean> {
    const gid = fb.globalCandidateId(set.set_id, candId);
    try {
      await this.client.chooseChop(gid, chopId, adjust);
      this.toast(`Listo: ${gid} quedó como chop «${chopId}». El agente lo usa en la próxima versión.`, "ok", undefined, 6000);
      await Promise.all([this.loadChops(), this.loadFeedback()]);
      return true;
    } catch (e) {
      this.toast(`No se pudo confirmar el chop: ${errMsg(e)}`, "error", undefined, 8000);
      return false;
    }
  }

  // ------------------------------------------------------------ prefs

  setTheme(theme: Theme): void {
    this.set({ theme });
    this.persist();
  }

  setNewVersionPolicy(p: NewVersionPolicy): void {
    this.set({ newVersionPolicy: p });
    this.persist();
  }

  /** Version by its 1-based position in history (keys 1..9). */
  versionAt(n: number): VersionId | null {
    const v = this.state.versions[n - 1];
    return v ? v.version : null;
  }

  destroy(): void {
    this.destroyed = true;
    if (this.wsTimer) clearTimeout(this.wsTimer);
    const ws = this.ws;
    this.ws = null;
    ws?.close();
  }
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export { versionNumber };
