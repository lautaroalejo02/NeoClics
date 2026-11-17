// Thin API client: the ONLY module that knows HTTP routes, tool names and
// envelope shapes. Everything the UI needs from the backend goes through
// here, so a contract change is a one-file edit. ui/API-NEEDS.md lists every
// endpoint/field used and which ones are contractual vs assumptions.

import type {
  Analysis,
  DraftInfo,
  ChopAdjust,
  ChopsChooseResult,
  ChopsListResult,
  FeedbackEvent,
  FeedbackReadResult,
  Job,
  Metrics,
  PeaksData,
  ProjectOpenResult,
  ProjectSummary,
  ProjectView,
  RenderMeta,
  SongGetResult,
  ToolResult,
  VersionId,
  VersionsDiffResult,
  VersionsGetResult,
  VersionsListResult,
  WaveformData,
  WsEvent,
  WsEventType,
} from "./types";

export class ApiError extends Error {
  readonly status: number | null;
  readonly issues: ToolResult["errors"];
  constructor(message: string, status: number | null, issues: ToolResult["errors"] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.issues = issues;
  }
}

/** The server says no: missing / wrong access token (non-loopback use). */
export function isAuthError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}

/** Header names the access token travels in. One place, so the integrator can
 *  reconcile with the backend (docs/server.md will say which it accepts). */
export const TOKEN_HEADERS = ["authorization", "x-muestra-token"] as const;

export interface ClientOptions {
  /** Access token (see api/token.ts); omitted → no auth headers at all. */
  token?: string | null;
  /** Called when any request is refused with 401/403. */
  onAuthError?: (status: number) => void;
  /** API base URL. "" = same origin (production: the API serves ui/dist). */
  baseUrl?: string;
  /** Project path for project-scoped tool params. The HTTP API is bound to
   *  ONE project and injects it when omitted (WP7), so leave it unset. */
  project?: string | null;
  fetchImpl?: typeof fetch;
}

const WS_TYPES: ReadonlySet<string> = new Set<WsEventType>([
  "job.updated",
  "version.created",
  "candidates.created",
  "feedback.appended",
  "song.changed",
  "draft.created",
  "drafts.changed",
  "grid.changed",
]);

/** Resolve the API base URL: `?api=` query param, then VITE_MUESTRA_API,
 *  then same origin. */
export function resolveBaseUrl(): string {
  try {
    const q = new URLSearchParams(globalThis.location?.search ?? "").get("api");
    if (q) return q.replace(/\/$/, "");
  } catch {
    /* no location (tests) */
  }
  const env = (import.meta.env?.VITE_MUESTRA_API as string | undefined) ?? "";
  return env.replace(/\/$/, "");
}

/** Encode a project-relative POSIX path for a URL, segment by segment. */
export function encodePath(p: string): string {
  return p.split("/").map(encodeURIComponent).join("/");
}

export class MuestraClient {
  readonly baseUrl: string;
  readonly project: string | null;
  private readonly fetchImpl: typeof fetch;
  private readonly token: string | null;
  private readonly onAuthError: ((status: number) => void) | null;
  /** Optional endpoints we already know are missing (404) — don't retry. */
  private missing = new Set<string>();
  private versionRequests = new Map<VersionId, Promise<VersionsGetResult | null>>();

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "").replace(/\/$/, "");
    this.project = opts.project ?? null;
    this.fetchImpl = opts.fetchImpl ?? ((...a) => fetch(...a));
    this.token = opts.token ?? null;
    this.onAuthError = opts.onAuthError ?? null;
  }

  /** Headers for every HTTP request: the access token, when there is one. */
  private headers(extra: Record<string, string> = {}): Record<string, string> {
    if (!this.token) return extra;
    return { ...extra, authorization: `Bearer ${this.token}`, "x-muestra-token": this.token };
  }

  /** 401/403 → tell the app (it shows the "open the link" screen). */
  private checkAuth(res: Response): void {
    if (res.status === 401 || res.status === 403) this.onAuthError?.(res.status);
  }

  // ---------------------------------------------------------------- low level

  private url(path: string): string {
    return this.baseUrl + path;
  }

  /** POST /tools/<mcp_name> with the tool's params. Unwraps the ToolResult
   *  envelope; `ok: false` becomes an ApiError carrying the Issues. */
  async tool<T>(mcpName: string, params: Record<string, unknown> = {}): Promise<T> {
    const body: Record<string, unknown> = { ...params };
    if (this.project && !("project" in body)) body.project = this.project;
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(`/tools/${mcpName}`), {
        method: "POST",
        headers: this.headers({ "content-type": "application/json" }),
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(`${mcpName}: sin conexión con la API (${String(e)})`, null);
    }
    this.checkAuth(res);
    let tr: ToolResult<T> | null = null;
    try {
      tr = (await res.json()) as ToolResult<T>;
    } catch {
      /* non-JSON error page */
    }
    if (!tr || typeof tr.ok !== "boolean") {
      throw new ApiError(`${mcpName}: HTTP ${res.status}`, res.status);
    }
    if (!tr.ok) {
      const msg =
        tr.errors?.map((e) => e.message).join("; ") || tr.hint || `${mcpName} falló`;
      throw new ApiError(msg, res.status, tr.errors ?? []);
    }
    return tr.data as T;
  }

  /** Start a long_running tool (render): POST answers 202 {job_id}; progress
   *  arrives as job.updated events. Returns the job id. */
  async startJob(mcpName: string, params: Record<string, unknown> = {}): Promise<string> {
    const body: Record<string, unknown> = { ...params };
    if (this.project && !("project" in body)) body.project = this.project;
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(`/tools/${mcpName}`), {
        method: "POST",
        headers: this.headers({ "content-type": "application/json" }),
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(`${mcpName}: sin conexión con la API (${String(e)})`, null);
    }
    this.checkAuth(res);
    const data = (await res.json().catch(() => null)) as { job_id?: string; ok?: boolean; errors?: ToolResult["errors"]; hint?: string } | null;
    if (res.status === 202 && data?.job_id) return data.job_id;
    if (data && data.ok === false) throw new ApiError(data.errors?.map((e) => e.message).join("; ") || data.hint || `${mcpName} falló`, res.status, data.errors ?? []);
    throw new ApiError(`${mcpName}: HTTP ${res.status}`, res.status);
  }

  /** GET JSON; null on 404 (optional files/endpoints). */
  private async getJson<T>(path: string): Promise<T | null> {
    const res = await this.fetchImpl(this.url(path), { headers: this.headers() });
    this.checkAuth(res);
    if (res.status === 404) return null;
    if (!res.ok) throw new ApiError(`${path}: HTTP ${res.status}`, res.status);
    return (await res.json()) as T;
  }

  private async optional<T>(key: string, path: string): Promise<T | null> {
    if (this.missing.has(key)) return null;
    try {
      const v = await this.getJson<T>(path);
      if (v == null) this.missing.add(key);
      return v;
    } catch {
      this.missing.add(key);
      return null;
    }
  }

  /** URL of a project file under /files (renders/, cache/previews/). */
  fileUrl(projectPath: string): string {
    return this.url(`/files/${encodePath(projectPath)}`);
  }

  async fetchFile(projectPath: string, signal?: AbortSignal): Promise<ArrayBuffer> {
    const res = await this.fetchImpl(this.fileUrl(projectPath), { signal, headers: this.headers() });
    this.checkAuth(res);
    if (!res.ok) throw new ApiError(`${projectPath}: HTTP ${res.status}`, res.status);
    return res.arrayBuffer();
  }

  // ---------------------------------------------------------------- project

  /** GET /project (muestra-api): song, versions with meta + metrics,
   *  candidate sets and source analyses in one round-trip. null when the
   *  endpoint is missing (404, remembered) or the request fails (retried
   *  next time), so the per-tool fallback still works. */
  async getSummary(): Promise<ProjectSummary | null> {
    const view = await this.projectView();
    return view ? summaryFromProjectView(view) : null;
  }

  private async projectView(): Promise<ProjectView | null> {
    if (this.missing.has("summary")) return null;
    try {
      const res = await this.fetchImpl(this.url("/project"), { headers: this.headers() });
      this.checkAuth(res);
      if (res.status === 404) {
        this.missing.add("summary");
        return null;
      }
      if (!res.ok) return null;
      const view = (await res.json()) as ProjectView;
      return view && Array.isArray(view.versions) ? view : null;
    } catch {
      return null;
    }
  }

  openProject(): Promise<ProjectOpenResult> {
    // `path` omitted: the HTTP server is bound to its project.
    return this.tool<ProjectOpenResult>("project_open", this.project ? { path: this.project } : {});
  }

  getSong(): Promise<SongGetResult> {
    return this.tool<SongGetResult>("song_get");
  }

  // ---------------------------------------------------------------- versions

  listVersions(): Promise<VersionsListResult> {
    return this.tool<VersionsListResult>("versions_list");
  }

  diffVersions(a: VersionId, b: VersionId): Promise<VersionsDiffResult> {
    return this.tool<VersionsDiffResult>("versions_diff", { a, b });
  }

  /** One version's meta + metrics through the readonly `versions.get` tool
   *  (/files only serves audio). Concurrent getMeta/getMetrics for the same
   *  version share one request. */
  private getVersion(version: VersionId): Promise<VersionsGetResult | null> {
    let pending = this.versionRequests.get(version);
    if (!pending) {
      // detail: the UI wants the full metrics (the agent default is the summary).
      pending = this.tool<VersionsGetResult>("versions_get", { version, detail: true }).catch(() => null);
      this.versionRequests.set(version, pending);
      void pending.finally(() => this.versionRequests.delete(version));
    }
    return pending;
  }

  async getMeta(version: VersionId): Promise<RenderMeta | null> {
    return (await this.getVersion(version))?.meta ?? null;
  }

  async getMetrics(version: VersionId): Promise<Metrics | null> {
    return (await this.getVersion(version))?.metrics ?? null;
  }

  /** GET /waveform?path=&points= → per-channel min/max, as PeaksData. */
  async getPeaks(projectPath: string, buckets: number): Promise<PeaksData | null> {
    const d = await this.optional<WaveformData>(
      "peaks",
      `/waveform?path=${encodeURIComponent(projectPath)}&points=${buckets}`,
    );
    if (!d || !Array.isArray(d.channels) || d.channels.length === 0) return null;
    const peaks = d.channels.map((ch) => ch.min.map((mn, i): [number, number] => [mn, ch.max[i] ?? mn]));
    return { peaks, duration_s: d.duration_s };
  }

  /** Source analysis for the chop panel: /project carries `analyses`
   *  (/files only serves audio; `source.analyze` is a compute tool). */
  async getAnalysis(sourceId: string): Promise<Analysis | null> {
    return (await this.projectView())?.analyses?.[sourceId] ?? null;
  }

  // ---------------------------------------------------------------- feedback

  readFeedback(opts: { since?: string; cursor?: number } = {}): Promise<FeedbackReadResult> {
    const params: Record<string, unknown> = {};
    if (opts.since) params.since = opts.since;
    if (opts.cursor != null) params.cursor = opts.cursor;
    return this.tool<FeedbackReadResult>("feedback_read", params);
  }

  async appendFeedback(event: FeedbackEvent): Promise<void> {
    await this.tool("feedback_append", { event });
  }

  // ---------------------------------------------------------------- chops

  listChopSets(): Promise<ChopsListResult> {
    return this.tool<ChopsListResult>("chops_list");
  }

  /** Manual override of the agent's pick (spec 3 step 4). The tool itself
   *  logs the `chop_choice` feedback event in mode manual. */
  chooseChop(candidateId: string, chopId: string, adjust?: ChopAdjust | null): Promise<ChopsChooseResult> {
    const params: Record<string, unknown> = {
      candidate_id: candidateId,
      chop_id: chopId,
      mode: "manual",
    };
    if (adjust && (adjust.start_beats !== 0 || adjust.length_beats !== 0)) params.adjust = adjust;
    return this.tool<ChopsChooseResult>("chops_choose", params);
  }

  // ---------------------------------------------------------------- jobs

  async getJob(id: string): Promise<Job | null> {
    return this.getJson<Job>(`/jobs/${encodeURIComponent(id)}`);
  }

  // ---------------------------------------------------------------- events

  eventsUrl(): string {
    const base = this.baseUrl || globalThis.location?.origin || "http://localhost";
    // browsers cannot set headers on a WebSocket: the token goes in the query
    return base.replace(/^http/, "ws") + "/events" + (this.token ? `?token=${encodeURIComponent(this.token)}` : "");
  }

  static parseWsEvent(raw: unknown): WsEvent | null {
    if (typeof raw !== "string") return null;
    try {
      const e = JSON.parse(raw) as Partial<WsEvent>;
      if (!e || typeof e.type !== "string" || !WS_TYPES.has(e.type)) return null;
      return { type: e.type as WsEventType, ts: String(e.ts ?? ""), data: e.data ?? {} };
    } catch {
      return null;
    }
  }

  /** `feedback.appended` carries "the event line" as data (schemas/api.py);
   *  tolerate a `{event}` wrapper too. */
  static feedbackFromWs(data: Record<string, unknown>): FeedbackEvent | null {
    const d = (typeof data.type === "string" ? data : data.event) as FeedbackEvent | undefined;
    return d && typeof d.type === "string" && typeof d.ts === "string" ? d : null;
  }
}

/** muestra-api's `GET /project` → the UI's boot summary (one place that
 *  knows the server shape). Only versions with a valid meta.json are listed
 *  by the server, so every version here is playable. */
export function summaryFromProjectView(view: ProjectView): ProjectSummary {
  const metas: Record<VersionId, RenderMeta> = {};
  const metrics: Record<VersionId, Metrics | null> = {};
  const versions = view.versions.map((v) => {
    metas[v.version] = v.meta;
    metrics[v.version] = v.metrics ?? null;
    return {
      version: v.version,
      parent: v.meta.parent ?? null,
      created_at: v.meta.created_at,
      summary: v.meta.summary ?? null,
    };
  });
  const song = (view.song ?? null) as SongGetResult["song"] | null;
  const title = song?.meta?.title ?? view.project_path?.split(/[\\/]/).filter(Boolean).pop() ?? null;
  return {
    project_path: view.project_path,
    title,
    has_song: !!song,
    song,
    versions,
    metas,
    metrics,
    analyses: view.analyses ?? {},
    drafts: normalizeDrafts(view.drafts, "draft").concat(normalizeDrafts(view.previews, "preview")),
    gridPreviews: view.grid_previews ?? {},
  };
}

/** Tolerant reader for the optional `drafts` / `previews` arrays: anything
 *  that is not a list of objects with a string `path` is ignored, so the UI
 *  works unchanged against a server that does not send them yet. */
export function normalizeDrafts(raw: unknown, defaultKind: NonNullable<DraftInfo["kind"]>): DraftInfo[] {
  if (!Array.isArray(raw)) return [];
  const out: DraftInfo[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    if (typeof o.path !== "string" || !o.path) continue;
    const kind = ["draft", "grid", "chop", "preview"].includes(String(o.kind)) ? (o.kind as DraftInfo["kind"]) : defaultKind;
    const rb = Array.isArray(o.range_bars) && o.range_bars.length === 2 && o.range_bars.every((n) => typeof n === "number") ? (o.range_bars as [number, number]) : null;
    out.push({
      id: typeof o.id === "string" && o.id ? o.id : o.path,
      path: o.path,
      created_at: typeof o.created_at === "string" ? o.created_at : null,
      label: typeof o.label === "string" ? o.label : null,
      range_bars: rb,
      duration_s: typeof o.duration_s === "number" && Number.isFinite(o.duration_s) ? o.duration_s : null,
      kind,
      source_id: typeof o.source_id === "string" ? o.source_id : null,
    });
  }
  return out;
}
