// Editor state + actions. The working song lives here as JSON; every manual
// edit is applied instantly (ops.ts), rebuilt into the browser arrangement
// (arrange.ts -> engine) and sent to song.edit, which validates and writes
// song.yaml. Undo/redo replay the inverse ops. Pieces (audio) are fetched
// from song.pieces in the background and swapped in while playing.
//
// Rule for edits in flight when the file changes underneath (agent edit,
// restore, another tab): edits are sent in order with the revision they were
// made on; song.edit re-applies them on the newer file when every element they
// touch is unchanged (the agent edited something else) and refuses them when
// it touched the same element. Refused = the editor reloads the file's song and
// says so; nothing of the agent's is overwritten. With no edit in flight, an
// external change simply reloads (selection kept when it still exists).

import { ApiError } from "../api/client";
import type { CommentEvent, VersionId } from "../api/types";
import type { AudioEngine } from "../audio/engine";
import type { Store } from "../state/store";
import { friendlyError } from "../text/es";
import { buildArrangement, emptyPieces, materialKey, type MaterialPiece, type PieceSet, type TrackStatus } from "./arrange";
import { applyOps, ConflictError, PathError, type Op } from "./ops";
import { BEATS_PER_BAR, instrumentFingerprint, songSeconds, stable, trackKind, wetFingerprint, type SongJson } from "./song";
import { clampScroll, defaultView, type Snap, type View } from "./timeline";
import { type Selection, emptySelection, selectionPayload } from "./selection";

export interface UndoEntry {
  label: string;
  ops: Op[];
  inverse: Op[];
}

interface Pending {
  ops: Op[];
  entry: UndoEntry | null;
  kind: "edit" | "undo" | "redo";
  label: string;
}

export interface EditorState {
  status: "off" | "loading" | "ready" | "error" | "unsupported";
  error: string | null;
  song: SongJson | null;
  /** last revision confirmed by the server */
  revision: string | null;
  baseVersion: VersionId | null;
  dirty: boolean;
  changes: string[];
  summary: string | null;
  inFlight: number;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
  selection: Selection;
  view: View;
  solo: string[];
  trackStatus: Record<string, TrackStatus>;
  /** tracks whose wet render is being prepared */
  rendering: string[];
  pieces: "idle" | "loading" | "ready" | "error";
  /** what you hear in the editor */
  listen: "editor" | "final";
  final: { draftId: string; revision: string | null; state: "rendering" | "ready" | "stale" } | null;
  saving: { jobId: string | null; summary: string } | null;
  inspector: boolean;
  source: { id: string } | null;
  lastEditMs: number | null;
  /** bumps on every arrangement rebuild (redraw trigger) */
  rev: number;
  /** loop region in absolute song beats (0-based) */
  loop: { startBeat: number; endBeat: number } | null;
  /** right-edge drags stretch (keep content, change length) instead of trimming */
  stretchMode: boolean;
  scrollY: number;
  /** track that receives pastes / keyboard actions without a selection */
  focusTrack: string | null;
  /** timeline height available for rows (rows grow to fill it) */
  fitH: number;
}

type Listener = () => void;

const PIECE_BYTES_BUDGET = 420 * 1024 * 1024;

export class EditorStore {
  state: EditorState;
  readonly app: Store;
  readonly engine: AudioEngine;
  private listeners = new Set<Listener>();
  private undo: UndoEntry[] = [];
  private redo: UndoEntry[] = [];
  private queue: Pending[] = [];
  private sending = false;
  private serverSong: SongJson | null = null;
  private pieces: PieceSet = emptyPieces();
  private buffers = new Map<string, AudioBuffer>();
  private bufferUse = new Map<string, number>();
  private piecesTimer: ReturnType<typeof setTimeout> | null = null;
  private stateTimer: ReturnType<typeof setTimeout> | null = null;
  private piecesGen = 0;
  private wetQueue: string[] = [];
  private wetBusy = false;
  private needsReload = false;
  private lastEditTrack: string | null = null;
  /** ms timings of the last edits (performance report) */
  timings: { edit: number[]; pieces: number[]; wet: number[] } = { edit: [], pieces: [], wet: [] };

  constructor(app: Store, engine: AudioEngine) {
    this.app = app;
    this.engine = engine;
    let view = defaultView();
    try {
      const saved = JSON.parse(globalThis.localStorage?.getItem("muestra.editor.view.v1") ?? "null") as Partial<View> | null;
      if (saved) view = { ...view, pxPerBeat: saved.pxPerBeat ?? view.pxPerBeat, zoomY: saved.zoomY ?? 1, snap: (saved.snap as Snap) ?? view.snap, expanded: saved.expanded ?? {}, showAuto: saved.showAuto ?? {} };
    } catch {
      /* private mode */
    }
    this.state = {
      status: "off",
      error: null,
      song: null,
      revision: null,
      baseVersion: null,
      dirty: false,
      changes: [],
      summary: null,
      inFlight: 0,
      canUndo: false,
      canRedo: false,
      undoLabel: null,
      redoLabel: null,
      selection: emptySelection(),
      view,
      solo: [],
      trackStatus: {},
      rendering: [],
      pieces: "idle",
      listen: "editor",
      final: null,
      saving: null,
      // phones: the details sheet would cover the timeline; open it on demand
      inspector: (globalThis.innerWidth ?? 1400) >= 900,
      source: null,
      lastEditMs: null,
      rev: 0,
      loop: null,
      stretchMode: false,
      scrollY: 0,
      focusTrack: null,
      fitH: 0,
    };
  }

  // ------------------------------------------------------------ observable

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getState = (): EditorState => this.state;

  private set(p: Partial<EditorState>): void {
    this.state = { ...this.state, ...p };
    this.listeners.forEach((fn) => fn());
  }

  private stacks(): Partial<EditorState> {
    return {
      canUndo: this.undo.length > 0,
      canRedo: this.redo.length > 0,
      undoLabel: this.undo[this.undo.length - 1]?.label ?? null,
      redoLabel: this.redo[this.redo.length - 1]?.label ?? null,
      inFlight: this.queue.length + (this.sending ? 1 : 0),
    };
  }

  // ------------------------------------------------------------ boot

  /** Load the working song. Resolves false when the backend has no editor (old server / mock). */
  async init(): Promise<boolean> {
    if (this.state.status === "loading" || this.state.status === "ready") return this.state.status === "ready";
    this.set({ status: "loading", error: null });
    try {
      const st = await this.app.client.tool<{ revision: string; song: SongJson | null; base_version: string | null; dirty: boolean; changes: string[]; summary: string | null }>("song_state");
      if (!st.song) throw new Error("song.yaml no es válido: arreglalo (o pedile al agente) para editar");
      this.serverSong = st.song;
      this.set({ status: "ready", song: st.song, revision: st.revision, baseVersion: st.base_version, dirty: st.dirty, changes: st.changes ?? [], summary: st.summary });
      this.engine.editorBpm = st.song.meta.bpm;
      this.rebuild();
      this.schedulePieces(0);
      void this.matchLevel();
      return true;
    } catch (e) {
      const notThere = e instanceof ApiError && (e.status === 404 || e.issues.some((i) => i.code === "unknown_tool" || i.code === "not_implemented"));
      this.set({ status: notThere ? "unsupported" : "error", error: friendlyError(e, "No pude cargar la canción").message });
      return false;
    }
  }

  /** Make the editor mix sit at the level of the last render (its stems vs its mastered mix). */
  private async matchLevel(): Promise<void> {
    const latest = this.app.state.versions[this.app.state.versions.length - 1]?.version;
    if (!latest) return;
    try {
      const db = await (this.engine as AudioEngine & { stemMatchDb?: (v: string) => Promise<number | null> }).stemMatchDb?.(latest);
      if (db != null && Number.isFinite(db)) this.engine.setEditorLevel(db);
    } catch {
      /* best effort */
    }
  }

  // ------------------------------------------------------------ edits

  /** Apply ops now (instant), remember them for undo, persist in order. */
  edit(ops: Op[], label: string, opts: { track?: string } = {}): boolean {
    const song = this.state.song;
    if (!song || !ops.length) return false;
    const t0 = performance.now();
    let res;
    try {
      res = applyOps(song, ops);
    } catch (e) {
      this.app.toast(e instanceof ConflictError ? "Eso cambió recién; probá de nuevo." : `No pude editar: ${e instanceof PathError ? e.message : String(e)}`, "warn");
      return false;
    }
    const entry: UndoEntry = { label, ops: res.applied, inverse: res.inverse };
    this.undo.push(entry);
    this.redo = [];
    this.lastEditTrack = opts.track ?? this.lastEditTrack;
    this.set({ song: res.song, dirty: true, ...this.stacks() });
    this.rebuild();
    const ms = performance.now() - t0;
    this.timings.edit.push(ms);
    if (this.timings.edit.length > 200) this.timings.edit.shift();
    this.set({ lastEditMs: ms });
    this.enqueue({ ops: res.applied, entry, kind: "edit", label });
    return true;
  }

  undoEdit(): void {
    const entry = this.undo.pop();
    if (!entry || !this.state.song) return;
    try {
      const res = applyOps(this.state.song, entry.inverse);
      this.redo.push(entry);
      this.set({ song: res.song, ...this.stacks() });
      this.rebuild();
      this.enqueue({ ops: entry.inverse, entry, kind: "undo", label: entry.label });
      this.app.toast(`Deshice: ${entry.label}`, "info", undefined, 2200);
    } catch {
      this.undo.push(entry);
      this.app.toast("No puedo deshacer eso: esa parte cambió desde afuera.", "warn");
    }
  }

  redoEdit(): void {
    const entry = this.redo.pop();
    if (!entry || !this.state.song) return;
    try {
      const res = applyOps(this.state.song, entry.ops);
      this.undo.push(entry);
      this.set({ song: res.song, ...this.stacks() });
      this.rebuild();
      this.enqueue({ ops: entry.ops, entry, kind: "redo", label: entry.label });
      this.app.toast(`Rehice: ${entry.label}`, "info", undefined, 2200);
    } catch {
      this.redo.push(entry);
      this.app.toast("No puedo rehacer eso: esa parte cambió desde afuera.", "warn");
    }
  }

  private enqueue(p: Pending): void {
    this.queue.push(p);
    this.set(this.stacks());
    void this.pump();
  }

  private async pump(): Promise<void> {
    if (this.sending) return;
    const next = this.queue.shift();
    if (!next) return;
    this.sending = true;
    this.set(this.stacks());
    try {
      const res = await this.app.client.tool<{ revision: string; song: SongJson; changes: string[]; rebased: boolean }>("song_edit", {
        ops: next.ops,
        base_revision: this.state.revision,
      });
      this.serverSong = res.song;
      this.set({ revision: res.revision });
      if (res.rebased) this.app.toast("El agente había cambiado otra parte: tu edición quedó encima.", "info", undefined, 3500);
      if (!this.queue.length) {
        // adopt the server's song (same music, defaults filled in)
        this.set({ song: res.song });
        this.rebuild();
      }
      this.scheduleState();
      this.schedulePieces();
    } catch (e) {
      const fe = friendlyError(e, "No se guardó la edición");
      // roll back to what the file holds; later queued edits were built on top: drop them too
      const dropped = [next, ...this.queue];
      this.queue = [];
      for (const d of dropped) {
        if (!d.entry) continue;
        if (d.kind === "edit" || d.kind === "redo") this.undo = this.undo.filter((u) => u !== d.entry);
        else this.redo = this.redo.filter((u) => u !== d.entry);
      }
      const conflict = e instanceof ApiError && e.issues.some((i) => i.code === "edit_conflict");
      this.app.toast(conflict ? "El agente cambió eso mismo mientras editabas: recargué la canción (no pisé su cambio)." : `${fe.message}${fe.advice ? ` ${fe.advice}` : ""}`, conflict ? "warn" : "error", undefined, 6500);
      this.sending = false;
      await this.reload(true);
      return;
    }
    this.sending = false;
    this.set(this.stacks());
    if (this.queue.length) void this.pump();
    else if (this.needsReload) {
      this.needsReload = false;
      void this.reload(false);
    }
  }

  /** The file changed (websocket song.changed) or an edit failed: reload it. */
  async reload(force: boolean): Promise<void> {
    if (!force && (this.sending || this.queue.length)) {
      this.needsReload = true;
      return;
    }
    try {
      const st = await this.app.client.tool<{ revision: string; song: SongJson | null; base_version: string | null; dirty: boolean; changes: string[]; summary: string | null }>("song_state");
      const external = st.revision !== this.state.revision;
      if (st.song && (external || force)) {
        this.serverSong = st.song;
        const sel = this.state.selection;
        this.set({ song: st.song, revision: st.revision, selection: stillValid(sel, st.song) ? sel : emptySelection() });
        this.engine.editorBpm = st.song.meta.bpm;
        this.rebuild();
        this.schedulePieces();
        if (external && !force) this.app.toast("La canción cambió desde afuera (el agente): recargué el arreglo.", "info", undefined, 4000);
      }
      this.set({ baseVersion: st.base_version, dirty: st.dirty, changes: st.changes ?? [], summary: st.summary });
      this.checkFinal();
    } catch {
      /* the next event retries */
    }
  }

  private scheduleState(): void {
    if (this.stateTimer) clearTimeout(this.stateTimer);
    this.stateTimer = setTimeout(() => {
      void this.app.client
        .tool<{ revision: string; base_version: string | null; dirty: boolean; changes: string[]; summary: string | null }>("song_state")
        .then((st) => {
          if (st.revision === this.state.revision) this.set({ baseVersion: st.base_version, dirty: st.dirty, changes: st.changes ?? [], summary: st.summary });
          this.checkFinal();
        })
        .catch(() => undefined);
    }, 350);
  }

  // ------------------------------------------------------------ arrangement + pieces

  rebuild(): void {
    const song = this.state.song;
    if (!song) return;
    const { arr, status } = buildArrangement(song, this.pieces, { solo: new Set(this.state.solo) });
    this.engine.editorBpm = song.meta.bpm;
    this.engine.setArrangement(arr);
    this.set({ trackStatus: status, rev: this.state.rev + 1 });
  }

  /** Decoded pieces (the timeline draws waveforms from them). */
  piecesSet(): PieceSet {
    return this.pieces;
  }

  clipboard: import("./actions").Clipboard | null = null;

  setLoop(loop: { startBeat: number; endBeat: number } | null): void {
    this.set({ loop });
    this.engine.setLoop(loop);
  }

  setStretchMode(on: boolean): void {
    this.set({ stretchMode: on });
  }

  setScrollY(y: number): void {
    if (Math.abs(y - this.state.scrollY) > 0.5) this.set({ scrollY: Math.max(0, y) });
  }

  setFitHeight(h: number): void {
    if (Math.abs(h - this.state.fitH) > 2) this.set({ fitH: h });
  }

  setFocusTrack(id: string | null): void {
    if (id !== this.state.focusTrack) this.set({ focusTrack: id });
  }

  /** Apply an Edit from actions.ts (and its follow-up selection). */
  apply(e: import("./actions").Edit | null): boolean {
    if (!e) return false;
    const ok = this.edit(e.ops, e.label, { track: e.track });
    if (ok && e.select) this.select(e.select);
    return ok;
  }

  /** Song beat (0-based) currently heard. */
  playheadBeat(): number {
    const song = this.state.song;
    return song ? (this.engine.getPosition() * song.meta.bpm) / 60 : 0;
  }

  /** Ask song.pieces for what the current song needs (debounced). */
  schedulePieces(ms = 250): void {
    if (this.piecesTimer) clearTimeout(this.piecesTimer);
    this.piecesTimer = setTimeout(() => void this.loadPieces(), ms);
  }

  private async loadPieces(): Promise<void> {
    const song = this.serverSong;
    const revision = this.state.revision;
    if (!song || !revision || this.sending || this.queue.length) {
      if (song) this.schedulePieces(300);
      return;
    }
    const gen = ++this.piecesGen;
    this.set({ pieces: "loading" });
    const t0 = performance.now();
    try {
      const res = await this.app.client.tool<PiecesResult>("song_pieces", { revision, dry: true, wet: false, instruments: false });
      if (gen !== this.piecesGen) return;
      await this.ingest(res, song);
      this.timings.pieces.push(performance.now() - t0);
      this.set({ pieces: "ready" });
      this.rebuild();
      // wet renders: the edited track first, then every track whose wet is stale
      const stale = (song.tracks ?? []).filter((t) => this.pieces.wet.get(t.id)?.fp !== wetFingerprint(song, t.id)).map((t) => t.id);
      stale.sort((a, b) => (a === this.lastEditTrack ? -1 : b === this.lastEditTrack ? 1 : 0));
      this.wetQueue = stale;
      void this.pumpWet(revision, song, gen);
    } catch (e) {
      if (gen !== this.piecesGen) return;
      if (e instanceof ApiError && e.issues.some((i) => i.code === "stale_revision")) {
        void this.reload(false);
        return;
      }
      this.set({ pieces: "error" });
      this.app.toast(`No pude preparar el audio del editor: ${friendlyError(e).message}`, "warn");
    }
  }

  private async pumpWet(revision: string, song: SongJson, gen: number): Promise<void> {
    if (this.wetBusy) return;
    this.wetBusy = true;
    try {
      while (this.wetQueue.length && gen === this.piecesGen) {
        const id = this.wetQueue.shift()!;
        this.set({ rendering: [id, ...this.wetQueue] });
        const t0 = performance.now();
        try {
          const res = await this.app.client.tool<PiecesResult>("song_pieces", { revision, tracks: [id], dry: false, wet: true });
          if (gen !== this.piecesGen) break;
          await this.ingest(res, song);
          this.timings.wet.push(performance.now() - t0);
          this.rebuild();
        } catch (e) {
          if (e instanceof ApiError && e.issues.some((i) => i.code === "stale_revision")) break;
        }
      }
    } finally {
      this.wetBusy = false;
      if (gen === this.piecesGen) this.set({ rendering: [] });
    }
  }

  private async buffer(id: string, path: string): Promise<AudioBuffer> {
    let b = this.buffers.get(id);
    if (!b) {
      b = await this.engine.loadBuffer(path);
      this.buffers.set(id, b);
    }
    this.bufferUse.set(id, Date.now());
    return b;
  }

  private async ingest(res: PiecesResult, song: SongJson): Promise<void> {
    const paths = new Map(res.pieces.map((p) => [p.id, p.path]));
    const load = (id: string) => this.buffer(id, paths.get(id)!);
    if (res.materials.length || Object.keys(res.oneshots).length) {
      const mats: MaterialPiece[] = [];
      for (const m of res.materials) {
        const pass = { chop: m.chop ?? null, sample: m.sample ?? null } as Parameters<typeof materialKey>[1];
        mats.push({
          chop: m.chop ?? null,
          sample: m.sample ?? null,
          start: m.start,
          length: m.length,
          stretch: m.stretch,
          pitch: m.pitch,
          tailFrames: m.tail_frames,
          base: m.base,
          sourceKey: materialKey(song, pass, m.clip_warp ?? null),
          buffer: await load(m.piece),
        });
      }
      const oneshots = new Map<string, AudioBuffer>();
      for (const [ref, id] of Object.entries(res.oneshots)) oneshots.set(ref, await load(id));
      // keep older materials that still match (other revisions' variants)
      const keep = this.pieces.materials.filter((old) => !mats.some((m) => m.buffer === old.buffer));
      this.pieces = { ...this.pieces, sampleRate: res.sample_rate, materials: [...mats, ...keep].slice(0, 400), oneshots: new Map([...this.pieces.oneshots, ...oneshots]) };
    }
    for (const t of res.tracks) {
      const track = song.tracks?.find((x) => x.id === t.id);
      if (!track) continue;
      if (t.dry_stem && trackKind(track) === "instrument") this.pieces.inst.set(t.id, { fp: instrumentFingerprint(song, track), buffer: await load(t.dry_stem) });
      if (t.wet) this.pieces.wet.set(t.id, { fp: wetFingerprint(song, t.id), buffer: await load(t.wet) });
    }
    this.evict();
  }

  private evict(): void {
    const used = new Set<AudioBuffer>([
      ...this.pieces.materials.map((m) => m.buffer),
      ...this.pieces.oneshots.values(),
      ...[...this.pieces.wet.values()].map((w) => w.buffer),
      ...[...this.pieces.inst.values()].map((w) => w.buffer),
    ]);
    let total = 0;
    const entries = [...this.buffers.entries()].sort((a, b) => (this.bufferUse.get(b[0]) ?? 0) - (this.bufferUse.get(a[0]) ?? 0));
    for (const [id, b] of entries) {
      total += b.length * b.numberOfChannels * 4;
      if (!used.has(b) || total > PIECE_BYTES_BUDGET) {
        if (!used.has(b)) this.buffers.delete(id);
      }
    }
    // materials beyond the budget: drop the least recently used non-base ones
    if (total > PIECE_BYTES_BUDGET) {
      this.pieces.materials = this.pieces.materials.filter((m) => m.base);
    }
  }

  /** Bytes of decoded editor audio (memory measurements). */
  memoryBytes(): number {
    let n = 0;
    this.buffers.forEach((b) => (n += b.length * b.numberOfChannels * 4));
    return n;
  }

  /** One-shot buffer for a sample ref (audition, library browser); asks song.pieces. */
  async sampleBuffer(ref: string): Promise<AudioBuffer | null> {
    const have = this.pieces.oneshots.get(ref);
    if (have) return have;
    try {
      const res = await this.app.client.tool<PiecesResult>("song_pieces", { samples: [ref], dry: false, wet: false, tracks: [] });
      const id = res.oneshots[ref];
      const path = res.pieces.find((p) => p.id === id)?.path;
      if (!id || !path) return null;
      const b = await this.buffer(id, path);
      this.pieces.oneshots.set(ref, b);
      return b;
    } catch {
      return null;
    }
  }

  // ------------------------------------------------------------ transport / listening

  togglePlay(): void {
    if (this.state.listen === "editor" && this.engine.getSource() !== "editor") this.engine.setSource("editor");
    this.engine.toggle();
  }

  seekBeat(beat0: number): void {
    if (!this.state.song) return;
    this.engine.seek((beat0 * 60) / this.state.song.meta.bpm);
  }

  setSolo(id: string, on: boolean): void {
    const solo = new Set(this.state.solo);
    if (on) solo.add(id);
    else solo.delete(id);
    this.set({ solo: [...solo] });
    this.rebuild();
  }

  setView(p: Partial<View>, width?: number): void {
    let v = { ...this.state.view, ...p };
    if (width && this.state.song) v = clampScroll(v, this.state.song.meta.length_bars * BEATS_PER_BAR, width);
    this.set({ view: v });
    try {
      const { pxPerBeat, zoomY, snap, expanded, showAuto } = v;
      globalThis.localStorage?.setItem("muestra.editor.view.v1", JSON.stringify({ pxPerBeat, zoomY, snap, expanded, showAuto }));
    } catch {
      /* private mode */
    }
  }

  select(sel: Selection): void {
    this.set({ selection: sel });
  }

  setInspector(open: boolean): void {
    this.set({ inspector: open });
  }

  openSource(id: string | null): void {
    this.set({ source: id ? { id } : null });
  }

  /** F: hear the true render of the current state (a draft), or back to the editor preview. */
  async toggleFinal(): Promise<void> {
    if (this.state.listen === "final") {
      this.set({ listen: "editor" });
      this.engine.setSource("editor", this.engine.getPosition());
      return;
    }
    const f = this.state.final;
    if (f && f.revision === this.state.revision && f.state === "ready") {
      await this.playFinal(f.draftId);
      return;
    }
    await this.renderDraft();
  }

  private async renderDraft(): Promise<void> {
    const revision = this.state.revision;
    try {
      this.set({ final: { draftId: "", revision, state: "rendering" } });
      await this.app.client.startJob("render", { draft: true, analyze: false });
      this.app.toast("Renderizando el estado actual (borrador, sin crear versión)…", "info", undefined, 3500);
    } catch (e) {
      this.set({ final: null });
      this.app.toast(`No pude renderizar: ${friendlyError(e).message}`, "error");
    }
  }

  /** A draft appeared (draft.created): if we asked for one, it is the "render final". */
  onDraft(id: string): void {
    const f = this.state.final;
    if (!f || f.state !== "rendering") return;
    this.set({ final: { draftId: id, revision: f.revision, state: "ready" } });
    void this.playFinal(id);
  }

  private async playFinal(id: string): Promise<void> {
    const d = this.app.state.drafts.find((x) => x.id === id);
    const song = this.state.song;
    if (!d || !song) return;
    const pos = this.engine.getPosition();
    this.engine.register({ version: id, bpm: song.meta.bpm, firstBar: d.range_bars?.[0] ?? 1, durationS: d.duration_s ?? songSeconds(song), sampleRate: this.pieces.sampleRate, mix: d.path, tracks: [] });
    this.set({ listen: "final" });
    try {
      await this.engine.prepare(id);
      this.engine.setSource("decks", pos);
      await this.engine.select(id);
    } catch (e) {
      this.set({ listen: "editor" });
      this.engine.setSource("editor", pos);
      this.app.toast(`No pude cargar el render: ${friendlyError(e).message}`, "error");
    }
  }

  private checkFinal(): void {
    const f = this.state.final;
    if (f && f.state === "ready" && f.revision !== this.state.revision) this.set({ final: { ...f, state: "stale" } });
  }

  // ------------------------------------------------------------ versions

  /** "Guardar versión": render with a Spanish summary written from the diff. */
  async saveVersion(): Promise<void> {
    if (this.state.saving) return;
    const summary = this.state.summary ?? "Edición manual en el editor";
    this.set({ saving: { jobId: null, summary } });
    try {
      const job = await this.app.client.startJob("render", { summary, request: "Edición manual en el editor de Muestra", agent: "muestra-editor" });
      this.set({ saving: { jobId: job, summary } });
    } catch (e) {
      this.set({ saving: null });
      this.app.toast(`No pude guardar la versión: ${friendlyError(e).message}`, "error");
    }
  }

  /** A version appeared: if it is ours, done; refresh the unsaved-changes state. */
  onVersion(): void {
    if (this.state.saving) {
      this.app.toast("Versión guardada", "ok");
      this.set({ saving: null });
    }
    this.scheduleState();
  }

  onJob(job: { id: string; status: string; result?: { ok?: boolean; errors?: { message: string }[] } | null }): void {
    const s = this.state.saving;
    if (s?.jobId === job.id && job.status === "failed") {
      this.set({ saving: null });
      this.app.toast(`No se guardó la versión: ${job.result?.errors?.[0]?.message ?? "falló el render"}`, "error", undefined, 8000);
    }
    const f = this.state.final;
    if (f?.state === "rendering" && job.status === "failed") {
      this.set({ final: null });
      this.app.toast("No se pudo renderizar el borrador", "error");
    }
  }

  /** "Descartar": back to the last version's song (undoable). */
  discard(): void {
    const v = this.state.baseVersion;
    if (!v || !this.state.song) return;
    // optimistic: we don't have the version's song locally; send and reload
    this.enqueueRaw([{ op: "revert_to", version: v } as unknown as Op], `volver a ${v}`);
  }

  private enqueueRaw(ops: Op[], label: string): void {
    void this.app.client
      .tool<{ revision: string; song: SongJson; inverse: Op[]; applied: Op[] }>("song_edit", { ops, base_revision: this.state.revision })
      .then((res) => {
        this.undo.push({ label, ops: res.applied, inverse: res.inverse });
        this.redo = [];
        this.serverSong = res.song;
        this.set({ song: res.song, revision: res.revision, selection: emptySelection(), ...this.stacks() });
        this.rebuild();
        this.scheduleState();
        this.schedulePieces();
        this.app.toast(`Descarté los cambios: volví a ${label.replace("volver a ", "")} (Ctrl+Z lo deshace)`, "ok");
      })
      .catch((e) => this.app.toast(`No pude descartar: ${friendlyError(e).message}`, "error"));
  }

  // ------------------------------------------------------------ agent requests

  /** "Pedile al agente": a comment anchored to the current selection. */
  async ask(text: string): Promise<boolean> {
    const song = this.state.song;
    if (!song || !text.trim()) return false;
    const selection = selectionPayload(song, this.state.selection);
    const ev: CommentEvent = {
      ts: new Date().toISOString(),
      type: "comment",
      text: text.trim(),
      ...(selection ? { selection } : {}),
      ...(this.state.revision ? { song_revision: this.state.revision } : {}),
      version: null,
    };
    const ok = await this.app.append(ev);
    if (ok) this.app.toast("Listo: se lo dejé al agente, apuntando a lo que seleccionaste.", "ok");
    return ok;
  }

  /** Song hash (diagnostics). */
  songKey(): string {
    return this.state.song ? stable(this.state.song) : "";
  }
}

function stillValid(sel: Selection, song: SongJson): boolean {
  if (sel.kind === "none") return true;
  return !!song.tracks?.some((t) => t.id === ("track" in sel ? sel.track : ""));
}

export interface PiecesResult {
  revision: string;
  sample_rate: number;
  frames: number;
  duration_s: number;
  pieces: { id: string; path: string; kind: string; frames: number; duration_s: number }[];
  materials: { chop?: string | null; sample?: string | null; clip_warp?: unknown; start: number; length: number; stretch: number; pitch: number; tail_frames: number; base: boolean; piece: string }[];
  oneshots: Record<string, string>;
  tracks: { id: string; kind: string; dry_stem?: string | null; wet?: string | null; wet_render_s?: number | null; error?: string | null }[];
  render_s: number;
  approximations: string[];
}
