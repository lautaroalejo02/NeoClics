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
  /** WP16: what was selected in the editor ("Pedile al agente") */
  selection?: Record<string, unknown> & { label?: string; track?: string; start_beat?: number; end_beat?: number };
  song_revision?: string;
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
  request?: string | null;
  liked?: boolean | null;
  /** versions.list (WP5): renders/<v>/mix.wav exists. Absent on older servers. */
  rendered?: boolean;
  /** no audio on disk but the song is in git: `render` with version=<v> regenerates it */
  rerenderable?: boolean;
  has_meta?: boolean;
}

export interface VersionsListResult {
  files: string[];
  versions: VersionInfo[];
}

export interface VersionsGetResult {
  files: string[];
  version: VersionId;
  meta: RenderMeta | null;
  metrics?: Metrics | null;
  /** detail=true only (WP12); the default is metrics_summary + metrics_path. */
  song_yaml?: string | null;
  metrics_summary?: string | null;
  metrics_path?: string | null;
}

export interface VersionsDiffResult {
  files: string[];
  a: VersionId;
  b: VersionId;
  diff: DiffEntry[];
  /** Spanish producer-style description (WP5). */
  musical_summary?: string | null;
  /** The same description, one sentence per change. */
  changes?: string[];
  /** Spanish notes on how the ears metrics moved (empty without metrics). */
  metrics_delta?: string[];
  identical?: boolean;
}

// ---------- Chop candidates (cache/chops/<src>/<set_id>.json) ----------
export type StemName = "drums" | "bass" | "vocals" | "other" | "mix";

export interface ChopCandidate {
  /** Global id "csNNN/candN" (contracts.md R5; model, schema and fixture agree
   *  since WP1). The UI also tolerates a local "candN" via globalCandidateId(). */
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
  // ---- planned by the WP14 backend change (all optional; the UI tolerates
  // their absence, see ui/API-NEEDS.md): the human's choice is ALWAYS recorded
  /** the chop id actually written (differs from the requested one when the
   *  backend wrote the chop under a new id instead of replacing a clip's) */
  chop_id?: string;
  requested_chop_id?: string;
  /** a clip's `repeat` was rescaled to keep its span */
  rescaled?: boolean | { clip?: string; repeat_from?: number; repeat_to?: number };
  /** true once the chop_choice feedback event is on disk */
  recorded?: boolean;
  /** backend notes (any language); shown as secondary details */
  message?: string | null;
  notes?: string[];
  warnings?: string[];
}

// ---------- analysis.json → Analysis ----------
export interface Analysis {
  schema_version?: number;
  source_id: string;
  file: string;
  duration_s: number;
  sample_rate: number;
  bpm_global: number | null;
  tempo_variable: boolean;
  beats_s: number[];
  downbeats_s: number[];
  key: { tonic: string; mode: "major" | "minor"; confidence: number };
  sections: { label: string; start_s: number; end_s: number }[];
  loudness_lufs: number;
  stems: Partial<Record<"drums" | "bass" | "vocals" | "other", string>>;
  separation_model: string;
  /** WP12: how the effective grid derives from the tracker (absent = as tracked). */
  grid?: GridCorrection | null;
  reliability?: { confidence: number; hint: string; regions: { start_s: number; end_s: number; reliable: boolean; reason: string }[] } | null;
}

export interface GridAlternative {
  tempo_factor: number;
  beat_phase: number;
  downbeat_shift_beats: number;
  bpm: number;
  label: string;
}

export interface GridCorrection {
  origin: "tracker" | "auto" | "manual";
  tempo_factor: number;
  beat_phase: number;
  downbeat_shift_beats: number;
  beats_per_bar: number;
  tempo_range_bpm: [number, number];
  raw: { bpm_global: number; tempo_variable?: boolean; beats_s?: number[]; downbeats_s?: number[] };
  tempo_confidence: number;
  bar_phase_confidence: number;
  ask_human: boolean;
  reason: string;
  alternatives: GridAlternative[];
}

/** cache/previews/<src>/grid/index.json, written by source.preview_grid. */
export interface GridPreview {
  path: string;
  label: string;
  applied: boolean;
  tempo_factor: number;
  beat_phase: number;
  downbeat_shift_beats: number;
  beats_per_bar: number;
  bpm: number | null;
  start_downbeat: number;
  start_s: number;
  end_s: number;
  bars: number;
}

export interface GridPreviewIndex {
  source_id: string;
  created_at: string;
  bpm: number | null;
  raw_bpm: number | null;
  previews: GridPreview[];
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

// ---------- muestra-api read endpoints (docs/server.md) ----------
/** Playable audio that is NOT a version (WP14 finding 4): a draft render
 *  (partial / experimental, never versioned) or a preview the agent made
 *  (source grid check, chop candidate). Proposed shape, see ui/API-NEEDS.md. */
export interface DraftInfo {
  id: string;
  /** project-relative, under renders/ or cache/previews/ (served by /files) */
  path: string;
  created_at?: string | null;
  label?: string | null;
  /** [start, end) 1-indexed bars when it is a partial render */
  range_bars?: [number, number] | null;
  duration_s?: number | null;
  /** draft (default) | grid | chop | preview */
  kind?: "draft" | "grid" | "chop" | "preview";
  source_id?: string | null;
}

/** GET /project, as the server sends it. */
export interface ProjectView {
  project_path: string;
  config?: Record<string, unknown>;
  song?: Record<string, unknown> | null;
  versions: { version: VersionId; meta: RenderMeta; metrics: Metrics | null; feedback?: unknown }[];
  candidate_sets?: ChopCandidateSet[];
  analyses?: Record<string, Analysis>;
  grid_previews?: Record<string, GridPreviewIndex>;
  feedback_cursor?: number;
  issues?: unknown[];
  /** optional (WP14 backend change): non-versioned draft renders and previews */
  drafts?: unknown;
  previews?: unknown;
}

/** The UI's boot summary, mapped from ProjectView in client.ts. */
export interface ProjectSummary {
  project_path?: string;
  title?: string | null;
  has_song?: boolean;
  song?: SongGetResult["song"] | null;
  versions?: VersionInfo[];
  metas?: Record<VersionId, RenderMeta>;
  metrics?: Record<VersionId, Metrics | null>;
  analyses?: Record<string, Analysis>;
  drafts?: DraftInfo[];
  gridPreviews?: Record<string, GridPreviewIndex>;
}

/** GET /waveform?path=&points=N (server shape). */
export interface WaveformData {
  path: string;
  sample_rate: number;
  frames: number;
  duration_s: number;
  points: number;
  channels: { min: number[]; max: number[] }[];
}

/** Waveform peaks as the UI uses them. */
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
  | "song.changed"
  | "draft.created"
  | "drafts.changed"
  | "grid.changed";

export interface WsEvent {
  type: WsEventType;
  ts: string;
  data: Record<string, unknown>;
}
