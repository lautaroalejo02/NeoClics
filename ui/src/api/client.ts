// Thin API client — the ONLY file that knows HTTP routes, tool names and
// envelope shapes. Everything the UI needs from the backend goes through
// here so a contract change is a one-file edit. See ui/API-NEEDS.md for
// the full endpoint/field list and what is contractual vs assumed.

import type {
  Analysis,
  ChopAdjust,
  ChopCandidateSet,
  ChopsListResult,
  FeedbackEvent,
  FeedbackReadResult,
  Job,
  Metrics,
  ProjectOpenResult,
  RenderMeta,
  SongGetResult,
  ToolResult,
  VersionId,
  VersionsGetResult,
  VersionsListResult,
  WsEvent,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly issues: ToolResult["errors"] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ClientOptions {
  /** API base URL. "" = same origin (production: API serves ui/dist). */
  baseUrl?: string;
  /** Project path sent inside project-scoped tool params. The HTTP API
   *  serves one project per process, so this is a formality; default ".". */
  project?: string;
  fetchImpl?: typeof fetch;
}

export class MuestraClient {
  readonly baseUrl: string;
  readonly project: string;
  private fetchImpl: typeof fetch;

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "").replace(/\/$/, "");
    this.project = opts.project ?? ".";
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  // ---------- low level ----------

  private async request(path: string, init?: RequestInit): Promise<Response> {
    const res = await this.fetchImpl(this.baseUrl + path, init);
    return res;
  }

  /** POST (or GET for readonly tools) /tools/<mcp_name>. Returns ToolResult. */
  async tool<T = unknown>(
    mcpName: string,
    params: Record<string, unknown> = {},
    { method = "POST", projectScoped = true } = {},
  ): Promise<ToolResult<T>> {
    const body = { ...(projectScoped ? { project: this.project } : {}), ...params };
    let res: Response;
    if (method === "GET") {
      const qs = new URLSearchParams(
        Object.entries(body).map(([k, v]) => [k, JSON.stringify(v)]),
      );
      res = await this.request(`/tools/${mcpName}?${qs}`);
    } else {
      res = await this.request(`/tools/${mcpName}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    if (!res.ok) {
      throw new ApiError(`${mcpName}: HTTP ${res.status}`, res.status);
    }
    const tr = (await res.json()) as ToolResult<T>;
    if (!tr.ok) {
      const msg = tr.errors.map((e) => e.message).join("; ") || tr.hint || "tool error";
      throw new ApiError(msg, res.status, tr.errors);
    }
    return tr;
  }

  /** GET a JSON file under /files (renders/, cache/previews/). */
  private async getJson<T>(projectPath: string): Promise<T> {
    const res = await this.request(`/files/${projectPath}`);
    if (!res.ok) throw new ApiError(`${projectPath}: HTTP ${res.status}`, res.status);
    return (await res.json()) as T;
  }

  /** Public URL for a project file (audio). */
  fileUrl(projectPath: string): string {
    return `${this.baseUrl}/files/${projectPath}`;
  }

  // ---------- project / song ----------

  async openProject(): Promise<ProjectOpenResult> {
    const tr = await this.tool<ProjectOpenResult>("project_open", {
      path: this.project,
    });
    return tr.data as ProjectOpenResult;
  }

  async getSong(): Promise<SongGetResult> {
    const tr = await this.tool<SongGetResult>("song_get", {});
    return tr.data as SongGetResult;
  }

  // ---------- versions ----------

  async listVersions(): Promise<VersionsListResult> {
    const tr = await this.tool<VersionsListResult>("versions_list", {});
    return tr.data as VersionsListResult;
  }

  async getVersion(version: VersionId): Promise<VersionsGetResult> {
    const tr = await this.tool<VersionsGetResult>("versions_get", { version });
    return tr.data as VersionsGetResult;
  }

  /** meta.json is inside renders/, so plain /files works too — cheaper than
   *  versions.get when we don't need the song.yaml snapshot. */
  async getMeta(version: VersionId): Promise<RenderMeta> {
    return this.getJson<RenderMeta>(`renders/${version}/meta.json`);
  }

  async getMetrics(version: VersionId): Promise<Metrics | null> {
    try {
      return await this.getJson<Metrics>(`renders/${version}/metrics.json`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }

  // ---------- waveforms ----------

  /** Optional peaks endpoint — NOT in the contract (see API-NEEDS.md).
   *  Expected shape: {peaks: number[][], channels: 1|2} with min/max pairs.
   *  Returns null on 404/invalid so callers can decode the wav locally. */
  async getPeaks(version: VersionId): Promise<number[][] | null> {
    try {
      const data = await this.getJson<{ peaks?: unknown }>(
        `renders/${version}/peaks.json`,
      );
      if (Array.isArray(data?.peaks) && data.peaks.every((p) => Array.isArray(p))) {
        return data.peaks as number[][];
      }
      return null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      return null;
    }
  }

  async fetchAudio(projectPath: string): Promise<ArrayBuffer> {
    const res = await this.request(`/files/${projectPath}`);
    if (!res.ok) throw new ApiError(`${projectPath}: HTTP ${res.status}`, res.status);
    return res.arrayBuffer();
  }

  // ---------- feedback ----------

  async readFeedback(opts: { since?: string; cursor?: number } = {}): Promise<FeedbackReadResult> {
    const params: Record<string, unknown> = {};
    if (opts.since) params.since = opts.since;
    if (opts.cursor != null) params.cursor = opts.cursor;
    const tr = await this.tool<FeedbackReadResult>("feedback_read", params);
    return tr.data as FeedbackReadResult;
  }

  async appendFeedback(event: FeedbackEvent): Promise<void> {
    await this.tool("feedback_append", { event });
  }

  // ---------- chops ----------

  async listChopSets(): Promise<ChopsListResult> {
    const tr = await this.tool<ChopsListResult>("chops_list", {});
    return tr.data as ChopsListResult;
  }

  async getChopSet(setPath: string): Promise<ChopCandidateSet> {
    // Candidate sets live under cache/chops/ which is NOT inside the /files
    // whitelist; the contract route is chops.list. Kept for API-NEEDS.
    return this.getJson<ChopCandidateSet>(setPath);
  }

  async chooseChop(
    candidateId: string,
    chopId: string,
    adjust?: ChopAdjust,
  ): Promise<void> {
    const params: Record<string, unknown> = {
      candidate_id: candidateId,
      chop_id: chopId,
      mode: "manual",
    };
    if (adjust && (adjust.start_beats !== 0 || adjust.length_beats !== 0)) {
      params.adjust = adjust;
    }
    await this.tool("chops_choose", params);
  }

  // ---------- analysis ----------

  async getAnalysis(sourceId: string): Promise<Analysis> {
    const tr = await this.tool<{ analysis: Analysis }>("source_analyze", {
      source_id: sourceId,
    });
    return (tr.data as { analysis: Analysis }).analysis;
  }

  // ---------- jobs ----------

  async getJob(id: string): Promise<Job> {
    const res = await this.request(`/jobs/${id}`);
    if (!res.ok) throw new ApiError(`job ${id}: HTTP ${res.status}`, res.status);
    return (await res.json()) as Job;
  }

  // ---------- websocket ----------

  /** Connect to /events. Caller owns reconnect; returns the WebSocket. */
  eventsSocket(): WebSocket {
    const base = this.baseUrl || location.origin;
    const url = base.replace(/^http/, "ws") + "/events";
    return new WebSocket(url);
  }

  static parseWsEvent(raw: string): WsEvent | null {
    try {
      const e = JSON.parse(raw) as WsEvent;
      return typeof e?.type === "string" ? e : null;
    } catch {
      return null;
    }
  }
}
