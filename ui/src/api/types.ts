// Contract types mirroring docs/contracts.md and schemas/*.schema.json.
// Field comments reference the contract where the shape is non-obvious.

export type VersionId = string; // "vNNN"

// ---------- Tool envelope (contracts.md "Tool envelope") ----------
export interface Issue {
  path: string;
  code: string;
  message: string;
  hint: string | null;
  value?: string;
}

export interface ToolResult<T = unknown> {
  tool: string;
  ok: boolean;
  data: T | null;
  files: string[];
  errors: Issue[];
  hint: string | null;
}

// ---------- renders/vNNN/meta.json → RenderMeta ----------
export interface DiffEntry {
  path: string;
  from?: unknown;
  to?: unknown;
}

export interface TrackStemInfo {
  id: string;
  kind: string; // clips | pattern | instrument
  stem: string; // project-relative path, post track fx / pre master
  lanes: { id: string; stem: string }[]; // pre track fx (pattern tracks)
}

export interface RenderMeta {
  schema_version: number;
  version: VersionId;
  parent: VersionId | null;
  git_commit?: string | null;
  created_at: string;
  request?: string | null;
  agent: string;
  summary?: string | null;
  diff: DiffEntry[];
  render_time_s?: number | null;
  duration_s?: number | null;
  sample_rate: number;
  bpm: number;
  key: string;
  length_bars: number;
  range_bars: [number, number]; // [start, end) 1-indexed, end exclusive
  mix: string;
  tracks: TrackStemInfo[];
}

// ---------- renders/vNNN/metrics.json → Metrics ----------
export interface Metrics {
  schema_version: number;
  version: VersionId;
  loudness: {
    integrated_lufs: number;
    true_peak_dbtp: number;
    clipping: boolean;
    clipped_samples: number;
  };
  spectral: {
    bands_hz: { low: [number, number]; mid: [number, number]; high: [number, number] };
    mix: { low_db: number; mid_db: number; high_db: number };
    tracks: Record<string, { low_db: number; mid_db: number; high_db: number }>;
  };
  low_end_masking: {
    band_hz: [number, number];
    energy_db: Record<string, number>;
    dominant_track: string;
  };
  alignment: {
    tracks: Record<
      string,
      { mean_offset_ms: number; max_abs_offset_ms: number; n_transients: number }
    >;
    max_abs_offset_ms: number;
  };
  tonal: {
    estimated: { tonic: string; mode: "major" | "minor"; confidence: number } | null;
    target_key: string;
    tonic_match: boolean;
    mode_match: boolean;
    in_key: boolean;
  };
  clicks: { count: number; events: { track: string; at_s: number; severity: number }[] };
  style_similarity: {
    model: string;
    references: { reference: string; score: number }[];
    mean_score: number;
  } | null;
  alarms: {
    code: string;
    severity: "info" | "warn" | "error";
    message: string;
    track?: string | null;
    at_s?: number | null;
  }[];
}

// ---------- feedback.jsonl → FeedbackEvent ----------
export interface RatingEvent {
  ts: string;
  type: "rating";
  version: VersionId;
  value: "like" | "dislike";
}
export interface CommentEvent {
  ts: string;
  type: "comment";
  version?: VersionId | null;
  at_s?: number | null;
  track?: string | null;
  text: string;
}
export interface ABChoiceEvent {
  ts: string;
  type: "ab_choice";
  a: VersionId;
  b: VersionId;
  winner: VersionId;
}
export interface ChopAdjust {
  start_beats: number;
  length_beats: number;
}
export interface ChopChoiceEvent {
  ts: string;
  type: "chop_choice";
  version?: VersionId | null;
  candidate_id: string; // "csNNN/candN"
  adjust?: ChopAdjust | null;
}
export type FeedbackEvent =
  | RatingEvent
  | CommentEvent
  | ABChoiceEvent
  | ChopChoiceEvent;

export interface FeedbackReadResult {
  files: string[];
  events: FeedbackEvent[];
  next_cursor: number;
  latest_ts: string | null;
  count: number;
  skipped: { path: string; line: number; message: string; hint: string }[];
}

// ---------- versions.list / versions.get ----------
export interface VersionInfo {
  version: VersionId;
  parent?: VersionId | null;
  created_at?: string | null;
  git_commit?: string | null;
  summary?: string | null;
  liked?: boolean | null;
}

export interface VersionsListResult {
  files: string[];
  versions: VersionInfo[];
}

export interface VersionsGetResult {
  files: string[];
  version: VersionId;
  meta: RenderMeta | null;
  song_yaml: string;
}

// ---------- Chop candidates ----------
export interface ChopCandidate {
  /** Local id inside the set in chops.list results ("candN"); the global
   *  contract id is `<set_id>/<id>`. */
  id: string;
  source_id: string;
  stem: "drums" | "bass" | "vocals" | "other" | "mix";
  start_downbeat: number;
  start_s: number;
  length_beats: number;
  end_s: number;
  local_bpm?: number | null;
  preview?: string | null;
  score?: { value: number; reasons: string[] } | null;
}

export interface ChopCandidateSet {
  schema_version: number;
  set_id: string;
  source_id: string;
  created_at: string;
  query: {
    section?: string | null;
    stem?: string | null;
    length_beats?: number | null;
    n?: number;
  };
  candidates: ChopCandidate[];
  pick?: { candidate_id: string; justification: string } | null;
  chosen?: {
    candidate_id: string;
    mode: "auto" | "manual";
    adjust?: ChopAdjust | null;
    at: string;
  } | null;
}

export interface ChopsListResult {
  files: string[];
  sets: ChopCandidateSet[];
}

// ---------- analysis.json → Analysis (via source.analyze) ----------
export interface Analysis {
  schema_version: number;
  source_id: string;
  file: string;
  duration_s: number;
  sample_rate: number;
  bpm_global: number;
  tempo_variable: boolean;
  beats_s: number[];
  downbeats_s: number[];
  key: { tonic: string; mode: "major" | "minor"; confidence: number };
  sections: { label: string; start_s: number; end_s: number }[];
  loudness_lufs: number;
  stems: Partial<Record<"drums" | "bass" | "vocals" | "other", string>>;
  separation_model: string;
}

// ---------- project.open / song.get ----------
export interface ProjectOpenResult {
  files: string[];
  project_path: string;
  config: {
    schema_version: number;
    sample_rate: number;
    export_bit_depth: number;
    library: { roots: string[] };
    plugins: { search_dirs: string[]; aliases: Record<string, unknown> };
    separation: { backend: string; model?: string | null };
  };
  has_song: boolean;
  sources?: string[];
  versions?: string[];
}

export interface SongGetResult {
  files: string[];
  path: string;
  song: {
    meta?: { title?: string; bpm?: number; key?: string; length_bars?: number };
    [k: string]: unknown;
  };
}

// ---------- Jobs + websocket ----------
export type JobStatus = "queued" | "running" | "done" | "failed";
export interface Job {
  id: string;
  tool: string;
  status: JobStatus;
  progress: number | null;
  message?: string | null;
  result?: ToolResult | null;
}

export type WsEventType =
  | "job.updated"
  | "version.created"
  | "candidates.created"
  | "feedback.appended"
  | "song.changed";

export interface WsEvent {
  type: WsEventType;
  ts: string;
  data: Record<string, unknown>;
}
