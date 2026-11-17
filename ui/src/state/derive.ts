// Pure derivations from app state (unit-tested, no React, no audio).

import type {
  Alarm,
  CommentEvent,
  DiffEntry,
  FeedbackEvent,
  Metrics,
  RenderMeta,
  VersionId,
  VersionInfo,
} from "../api/types";

export interface VersionRow {
  version: VersionId;
  parent: VersionId | null;
  depth: number;
  /** parent is not the row right below (older) — show "de vNNN". */
  branched: boolean;
  createdAt: string | null;
  summary: string | null;
}

export function versionNumber(v: VersionId): number {
  return Number(v.slice(1)) || 0;
}

export function sortVersions<T extends { version: VersionId }>(list: T[]): T[] {
  return [...list].sort((a, b) => versionNumber(a.version) - versionNumber(b.version));
}

/**
 * History as a tree, listed newest first. The main line (each node's newest
 * child) stays at depth 0; older siblings — branches started from an older
 * version, e.g. after a restore — are indented one level per branch.
 */
export function versionTree(versions: VersionInfo[], metas: Record<VersionId, RenderMeta | undefined>): VersionRow[] {
  const known = new Set(versions.map((v) => v.version));
  const parentOf = (v: VersionInfo): VersionId | null => {
    const p = v.parent ?? metas[v.version]?.parent ?? null;
    return p && known.has(p) ? p : null;
  };
  const children = new Map<VersionId | null, VersionInfo[]>();
  for (const v of versions) {
    const p = parentOf(v);
    const arr = children.get(p) ?? [];
    arr.push(v);
    children.set(p, arr);
  }
  for (const arr of children.values()) arr.sort((a, b) => versionNumber(b.version) - versionNumber(a.version));
  const depth = new Map<VersionId, number>();
  const visit = (v: VersionInfo, d: number) => {
    depth.set(v.version, d);
    (children.get(v.version) ?? []).forEach((c, i) => visit(c, i === 0 ? d : d + 1));
  };
  (children.get(null) ?? []).forEach((r, i) => visit(r, i === 0 ? 0 : 1));
  const rows = sortVersions(versions).reverse();
  return rows.map((v, i) => {
    const parent = parentOf(v);
    const older = rows[i + 1]?.version ?? null;
    return {
      version: v.version,
      parent,
      depth: depth.get(v.version) ?? 0,
      branched: parent != null && parent !== older,
      createdAt: v.created_at ?? metas[v.version]?.created_at ?? null,
      summary: v.summary ?? metas[v.version]?.summary ?? null,
    };
  });
}

/** Latest like/dislike per version (feedback.jsonl order = time order). */
export function latestRatings(events: FeedbackEvent[]): Record<VersionId, "like" | "dislike"> {
  const out: Record<VersionId, "like" | "dislike"> = {};
  for (const e of events) if (e.type === "rating") out[e.version] = e.value;
  return out;
}

export function commentsFor(events: FeedbackEvent[], version: VersionId | null): CommentEvent[] {
  return events.filter((e): e is CommentEvent => e.type === "comment" && (e.version ?? null) === version);
}

export function commentCounts(events: FeedbackEvent[]): Record<VersionId, number> {
  const out: Record<VersionId, number> = {};
  for (const e of events) if (e.type === "comment" && e.version) out[e.version] = (out[e.version] ?? 0) + 1;
  return out;
}

/**
 * The agent reads feedback before each iteration (spec 9), so an event is
 * "pendiente de leer por el agente" until a version created after it exists.
 */
export function isPending(e: FeedbackEvent, newestVersionTs: number): boolean {
  const t = Date.parse(e.ts);
  return Number.isFinite(t) && t > newestVersionTs;
}

export function newestVersionTs(metas: Record<VersionId, RenderMeta | undefined>, versions: VersionInfo[]): number {
  let max = 0;
  for (const v of versions) {
    const t = Date.parse(metas[v.version]?.created_at ?? v.created_at ?? "");
    if (Number.isFinite(t) && t > max) max = t;
  }
  return max;
}

export function alarmCounts(m: Metrics | null | undefined): Record<Alarm["severity"], number> {
  const out = { info: 0, warn: 0, error: 0 };
  for (const a of m?.alarms ?? []) out[a.severity]++;
  return out;
}

/** Loudness-match offsets (dB, always <= 0: only ever turn the louder one
 *  DOWN, so matching can't clip). Null when a LUFS value is missing. */
export function loudnessOffsets(lufs: Record<VersionId, number | null | undefined>): Record<VersionId, number> | null {
  const entries = Object.entries(lufs);
  if (entries.length === 0 || entries.some(([, v]) => v == null || !Number.isFinite(v))) return null;
  const ref = Math.min(...entries.map(([, v]) => v as number));
  const out: Record<VersionId, number> = {};
  for (const [k, v] of entries) out[k] = ref - (v as number);
  return out;
}

// ------------------------------------------------------------ readable diff

const PARAM_NAMES: Record<string, string> = {
  cutoff_hz: "corte",
  gain_db: "volumen",
  pan: "paneo",
  pitch_semitones: "afinación",
  steps: "patrón",
  bpm: "tempo",
  key: "tonalidad",
  length_bars: "largo",
  depth: "profundidad",
  release_ms: "release",
  attack_ms: "ataque",
  drive_db: "saturación",
  mix: "mezcla",
  ratio: "ratio",
  threshold_db: "umbral",
  ceiling_db: "techo",
  target_lufs: "loudness objetivo",
  start_downbeat: "downbeat de inicio",
  start_offset_beats: "corrimiento",
  length_beats: "largo",
  mode: "modo",
  swing: "swing",
  fade_ms: "fade",
  wet_level: "nivel húmedo",
  room_size: "tamaño de sala",
  time_beats: "tiempo",
  feedback: "realimentación",
};

const UNITS: [RegExp, string][] = [
  [/_hz$/, " Hz"],
  [/_db$/, " dB"],
  [/_ms$/, " ms"],
  [/_semitones$/, " st"],
  [/_beats$/, " beats"],
  [/^bpm$/, " BPM"],
  [/_lufs$/, " LUFS"],
];

function unitFor(param: string): string {
  for (const [re, u] of UNITS) if (re.test(param)) return u;
  return "";
}

function fmtVal(v: unknown, unit: string): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    const s = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("es-AR") : String(Math.round(v * 100) / 100);
    return s + unit;
  }
  if (typeof v === "string") return v.length > 40 ? `“${v.slice(0, 38)}…”` : `“${v}”`;
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.type === "string") return `${o.type}${o.id ? ` (${String(o.id)})` : ""}`;
    return "{…}";
  }
  return String(v);
}

export interface ReadableDiff {
  where: string;
  what: string;
  from: string;
  to: string;
  kind: "added" | "removed" | "changed";
}

/** Turn an ID-based diff path (contracts.md R10) into Spanish words. */
export function readableDiff(d: DiffEntry): ReadableDiff {
  const parts = d.path.split(".");
  const last = parts[parts.length - 1] ?? d.path;
  const lastName = last.replace(/\[\d+\]$/, "");
  let where = "";
  if (parts[0] === "tracks" && parts.length >= 2) {
    where = `pista ${parts[1].replace(/\[\d+\]$/, "")}`;
    const fxIdx = parts.indexOf("fx");
    if (fxIdx > 0 && parts[fxIdx + 1]) where += ` · efecto ${parts[fxIdx + 1]}`;
    const lanesIdx = parts.indexOf("lanes");
    if (lanesIdx > 0 && parts[lanesIdx + 1]) where += ` · ${parts[lanesIdx + 1]}`;
    const clip = /clips\[(\d+)\]/.exec(d.path);
    if (clip) where += ` · clip ${Number(clip[1]) + 1}`;
    if (/\.fx\[\d+\]$/.test(d.path)) where += ` · efecto ${Number(/\[(\d+)\]$/.exec(d.path)![1]) + 1}`;
  } else if (parts[0] === "chops" && parts[1]) {
    where = `chop ${parts[1]}`;
  } else if (parts[0] === "master") {
    where = "master";
    const fx = /fx\[(\d+)\]/.exec(d.path);
    if (fx) where += ` · efecto ${Number(fx[1]) + 1}`;
    else if (parts[1] === "fx" && parts[2]) where += ` · efecto ${parts[2]}`;
  } else if (parts[0] === "meta") {
    where = "canción";
  } else if (parts[0] === "automation") {
    where = "automatización";
  } else {
    where = parts.slice(0, -1).join(" · ");
  }
  const isContainer = /\]$/.test(last) || parts.length <= 2;
  const what = isContainer && !(lastName in PARAM_NAMES) ? "" : PARAM_NAMES[lastName] ?? lastName.replace(/_/g, " ");
  const unit = unitFor(lastName);
  const kind = d.from == null && d.to != null ? "added" : d.to == null && d.from != null ? "removed" : "changed";
  return { where, what, from: fmtVal(d.from, unit), to: fmtVal(d.to, unit), kind };
}

/** Relative time in Spanish ("hace 3 min"). */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "recién";
  const m = Math.round(s / 60);
  if (m < 60) return `hace ${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  if (d < 7) return `hace ${d} d`;
  return new Date(t).toLocaleDateString("es-AR", { day: "numeric", month: "short" });
}

export function clockTime(iso: string | null | undefined): string {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
}
