// Contract types mirroring docs/contracts.md and schemas/*.schema.json
// (the pydantic models under src/muestra/schemas/ are the source of truth).
// Field comments reference the contract where the shape is non-obvious.

export type VersionId = string; // "vNNN"

// ---------- Tool envelope (contracts.md "Tool envelope") ----------
export interface Issue {
  path: string;
  code: string;
  message: string;
  hint?: string | null;
  value?: string | null;
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

export interface StemLaneRef {
  id: string;
  stem: string; // pre track fx
}

export interface RenderTrackMeta {
  id: string;
  kind: string; // clips | pattern | instrument
  stem: string; // project-relative; post track fx/fader/pan, PRE master
  lanes: StemLaneRef[];
}

export interface RenderMeta {
  schema_version: number;
  version: VersionId;
  parent?: VersionId | null;
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
  tracks: RenderTrackMeta[];
}

// ---------- renders/vNNN/metrics.json → Metrics (spec 10) ----------
export interface BandLevels {
  low_db: number;
  mid_db: number;
  high_db: number;
}

export type AlarmSeverity = "info" | "warn" | "error";

export interface Alarm {
  code: string;
  severity: AlarmSeverity;
  message: string;
  track?: string | null;
  at_s?: number | null;
}

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
    mix: BandLevels;
    tracks: Record<string, BandLevels>;
  };
  low_end_masking: {
    band_hz: [number, number];
    energy_db: Record<string, number>;
    dominant_track?: string | null;
  };
  alignment: {
    tracks: Record<string, { mean_offset_ms: number; max_abs_offset_ms: number; n_transients: number }>;
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
  style_similarity?: {
    model: string;
    references: { reference: string; score: number }[];
    mean_score: number;
  } | null;
  alarms: Alarm[];
}

// ---------- feedback.jsonl → FeedbackEvent (spec 7.5) ----------
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
  candidate_id: string; // global "csNNN/candN"
  adjust?: ChopAdjust | null;
}
export type FeedbackEvent = RatingEvent | CommentEvent | ABChoiceEvent | ChopChoiceEvent;

export interface SkippedFeedbackLine {
  path: string;
  line: number;
  message: string;
  hint?: string | null;
}

export interface FeedbackReadResult {
  files: string[];
  events: FeedbackEvent[];
  next_cursor: number;
  latest_ts?: string | null;
  count?: number;
  skipped?: SkippedFeedbackLine[];
}

// ---------- versions.list / versions.get / versions.diff ----------
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

export interface VersionsDiffResult {
  files: string[];
  a: VersionId;
  b: VersionId;
  diff: DiffEntry[];
  musical_summary?: string | null;
}

// ---------- Chop candidates (cache/chops/<src>/<set_id>.json) ----------
export type StemName = "drums" | "bass" | "vocals" | "other" | "mix";

export interface ChopCandidate {
  /** The pydantic model (source of truth) and its JSON Schema use the LOCAL
   *  id ("cand2"); contracts.md R5 says global. The UI accepts both and
   *  always builds the global id with `globalCandidateId()`. */
  id: string;
  source_id: string;
  stem: StemName;
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

export interface ChopsChooseResult {
  files: string[];
  set_id: string;
  candidate_id: string;
  chop: Record<string, unknown>;
}

// ---------- analysis.json → Analysis ----------
export interface Analysis {
  schema_version?: number;
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
  config: Record<string, unknown>;
  has_song: boolean;
  sources?: string[];
  versions?: string[];
}

export interface SongGetResult {
  files: string[];
  path?: string;
  song: {
    meta?: { title?: string; bpm?: number; key?: string; length_bars?: number };
    [k: string]: unknown;
  };
}

// ---------- OPTIONAL accelerators (NOT in the contract; see API-NEEDS.md) ----------
/** GET /summary — one round-trip for everything the UI needs at boot. */
export interface ProjectSummary {
  project_path?: string;
  title?: string | null;
  has_song?: boolean;
  versions?: VersionInfo[];
  metas?: Record<VersionId, RenderMeta>;
  metrics?: Record<VersionId, Metrics | null>;
  analyses?: Record<string, Analysis>;
}

/** GET /peaks/<project-relative wav>?buckets=N — min/max per bucket. */
export interface PeaksData {
  /** peaks[channel][bucket] = [min, max] in -1..1 */
  peaks: [number, number][][];
  duration_s?: number;
}

// ---------- Jobs + websocket (schemas/api.py) ----------
export type JobStatus = "queued" | "running" | "done" | "failed";
export interface Job {
  id: string;
  tool: string;
  status: JobStatus;
  progress?: number | null;
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
