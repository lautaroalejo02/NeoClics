// Thin API client: the ONLY module that knows HTTP routes, tool names and
// envelope shapes. Everything the UI needs from the backend goes through
// here, so a contract change is a one-file edit. ui/API-NEEDS.md lists every
// endpoint/field used and which ones are contractual vs assumptions.

import type {
  Analysis,
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
  RenderMeta,
  SongGetResult,
  ToolResult,
  VersionId,
  VersionsDiffResult,
  VersionsListResult,
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

export interface ClientOptions {
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
  /** Optional endpoints we already know are missing (404) — don't retry. */
  private missing = new Set<string>();

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "").replace(/\/$/, "");
    this.project = opts.project ?? null;
    this.fetchImpl = opts.fetchImpl ?? ((...a) => fetch(...a));
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
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(`${mcpName}: sin conexión con la API (${String(e)})`, null);
    }
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

  /** GET JSON; null on 404 (optional files/endpoints). */
  private async getJson<T>(path: string): Promise<T | null> {
    const res = await this.fetchImpl(this.url(path));
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
    const res = await this.fetchImpl(this.fileUrl(projectPath), { signal });
    if (!res.ok) throw new ApiError(`${projectPath}: HTTP ${res.status}`, res.status);
    return res.arrayBuffer();
  }

  // ---------------------------------------------------------------- project

  /** Optional accelerator (NOT in the contract): GET /summary. */
  getSummary(): Promise<ProjectSummary | null> {
    return this.optional<ProjectSummary>("summary", "/summary");
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

  /** meta.json lives under renders/, which /files serves (contract). */
  async getMeta(version: VersionId): Promise<RenderMeta | null> {
    return this.getJson<RenderMeta>(`/files/renders/${encodeURIComponent(version)}/meta.json`);
  }

  async getMetrics(version: VersionId): Promise<Metrics | null> {
    return this.getJson<Metrics>(`/files/renders/${encodeURIComponent(version)}/metrics.json`);
  }

  /** Optional accelerator (NOT in the contract): GET /peaks/<wav path>. */
  async getPeaks(projectPath: string, buckets: number): Promise<PeaksData | null> {
    const d = await this.optional<PeaksData>(
      "peaks",
      `/peaks/${encodePath(projectPath)}?buckets=${buckets}`,
    );
    if (d && Array.isArray(d.peaks) && d.peaks.length > 0 && Array.isArray(d.peaks[0])) return d;
    return null;
  }

  /** Source analysis for the chop panel. cache/analysis/ is NOT in the
   *  /files whitelist and `source.analyze` is a long-running COMPUTE tool,
   *  so we only read it from /summary or (requested) /files. */
  async getAnalysis(sourceId: string): Promise<Analysis | null> {
    return this.optional<Analysis>(
      `analysis:${sourceId}`,
      `/files/cache/analysis/${encodeURIComponent(sourceId)}.json`,
    );
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
    return base.replace(/^http/, "ws") + "/events";
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
