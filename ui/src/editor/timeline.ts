// Timeline geometry: beats <-> pixels, snapping, grid density and the row
// layout of every track (lanes, piano roll, automation). Pure functions.

import { BEATS_PER_BAR, patternHits, trackAutomations, trackKind, trackNotes, type SongJson, type TrackJson } from "./song";

export type Snap = "bar" | "beat" | "8th" | "16th" | "off";

export const SNAP_BEATS: Record<Snap, number> = { bar: 4, beat: 1, "8th": 0.5, "16th": 0.25, off: 0 };
export const SNAP_LABEL: Record<Snap, string> = { bar: "Compás", beat: "Tiempo", "8th": "1/8", "16th": "1/16", off: "Libre" };

export interface View {
  /** horizontal zoom */
  pxPerBeat: number;
  /** horizontal scroll in px (0 = bar 1) */
  scrollX: number;
  /** vertical zoom multiplier for track heights */
  zoomY: number;
  snap: Snap;
  /** expanded piano rolls / step grids */
  expanded: Record<string, boolean>;
  /** automation lanes shown under the track */
  showAuto: Record<string, boolean>;
}

export const MIN_PX_PER_BEAT = 4;
export const MAX_PX_PER_BEAT = 400;

export function defaultView(): View {
  return { pxPerBeat: 28, scrollX: 0, zoomY: 1, snap: "16th", expanded: {}, showAuto: {} };
}

export function snapBeat(beat: number, snap: Snap, free = false): number {
  const step = SNAP_BEATS[snap];
  if (free || !step) return beat;
  return Math.round(beat / step) * step;
}

export function floorSnap(beat: number, snap: Snap, free = false): number {
  const step = SNAP_BEATS[snap];
  if (free || !step) return beat;
  return Math.floor(beat / step + 1e-9) * step;
}

export function beatToX(beat0: number, v: View): number {
  return beat0 * v.pxPerBeat - v.scrollX;
}

export function xToBeat(x: number, v: View): number {
  return (x + v.scrollX) / v.pxPerBeat;
}

/** Smallest grid subdivision (beats) whose lines are at least `minPx` apart. */
export function gridStep(pxPerBeat: number, minPx = 9): number {
  for (const s of [0.25, 0.5, 1, 4, 8, 16, 32, 64]) if (s * pxPerBeat >= minPx) return s;
  return 128;
}

/** Bar label step so numbers don't collide. */
export function barLabelStep(pxPerBeat: number): number {
  const barPx = pxPerBeat * BEATS_PER_BAR;
  for (const s of [1, 2, 4, 8, 16, 32]) if (s * barPx >= 34) return s;
  return 64;
}

/** Zoom around an anchor x (px in the canvas), keeping that beat under it. */
export function zoomAt(v: View, factor: number, anchorX: number): View {
  const beat = xToBeat(anchorX, v);
  const ppb = Math.min(MAX_PX_PER_BEAT, Math.max(MIN_PX_PER_BEAT, v.pxPerBeat * factor));
  return { ...v, pxPerBeat: ppb, scrollX: Math.max(0, beat * ppb - anchorX) };
}

export function clampScroll(v: View, totalBeats: number, width: number): View {
  const max = Math.max(0, totalBeats * v.pxPerBeat - width + 40);
  return { ...v, scrollX: Math.min(max, Math.max(0, v.scrollX)) };
}

// ------------------------------------------------------------------ rows

export interface LaneRow {
  name: string;
  y: number;
  h: number;
}

export interface AutoRow {
  target: string;
  label: string;
  y: number;
  h: number;
}

export interface Row {
  trackId: string;
  kind: "clips" | "pattern" | "instrument";
  y: number;
  /** full height incl. automation lanes */
  h: number;
  /** content area (clips / lanes / notes) */
  contentY: number;
  contentH: number;
  lanes: LaneRow[];
  /** piano roll pitch range (instrument tracks) */
  pitchLo: number;
  pitchHi: number;
  autos: AutoRow[];
  expanded: boolean;
}

export const LANE_H = 20;
export const AUTO_H = 54;
export const TRACK_GAP = 6;

export function autoLabel(target: string): string {
  const parts = target.split(".");
  if (parts[2] === "gain_db") return "Volumen (dB)";
  if (parts[2] === "pan") return "Paneo";
  if (parts[2] === "fx") return `${parts[3]} · ${parts.slice(4).join(".")}`;
  return target;
}

export function noteRange(song: SongJson, t: TrackJson): [number, number] {
  const notes = trackNotes(song, t);
  if (!notes.length) return [48, 72];
  let lo = 127;
  let hi = 0;
  for (const n of notes) {
    lo = Math.min(lo, n.pitch);
    hi = Math.max(hi, n.pitch);
  }
  lo = Math.max(0, lo - 3);
  hi = Math.min(127, hi + 3);
  if (hi - lo < 12) {
    const mid = Math.round((lo + hi) / 2);
    lo = Math.max(0, mid - 6);
    hi = Math.min(127, lo + 12);
  }
  return [lo, hi];
}

/** Rows for the song. With `fitH` (px available) and the default vertical
 *  zoom, short songs grow to fill the space instead of leaving it empty. */
export function layoutRows(song: SongJson, v: View, fitH = 0): { rows: Row[]; height: number } {
  const base = layoutRowsAt(song, v, v.zoomY);
  if (!fitH || v.zoomY !== 1 || base.height >= fitH - 8) return base;
  const factor = Math.min(2.6, (fitH - 8) / Math.max(1, base.height));
  return layoutRowsAt(song, v, factor);
}

function layoutRowsAt(song: SongJson, v: View, zoomY: number): { rows: Row[]; height: number } {
  const rows: Row[] = [];
  let y = 0;
  for (const t of song.tracks ?? []) {
    const kind = trackKind(t);
    const expanded = v.expanded[t.id] ?? kind === "pattern";
    let contentH: number;
    let lanes: LaneRow[] = [];
    let pitchLo = 0;
    let pitchHi = 0;
    if (kind === "pattern") {
      const names = Object.keys(t.pattern?.lanes ?? {});
      const laneH = Math.round(LANE_H * Math.min(zoomY, 1.5));
      if (expanded) {
        lanes = names.map((name, i) => ({ name, y: y + 4 + i * laneH, h: laneH }));
        contentH = names.length * laneH + 8;
      } else contentH = Math.round(36 * zoomY);
    } else if (kind === "instrument") {
      [pitchLo, pitchHi] = noteRange(song, t);
      contentH = expanded ? Math.min(420, Math.max(160, (pitchHi - pitchLo + 1) * Math.round(9 * zoomY))) : Math.round(52 * zoomY);
    } else contentH = Math.round(70 * zoomY);
    const contentY = y;
    let h = contentH;
    const autos: AutoRow[] = [];
    if (v.showAuto[t.id]) {
      for (const a of trackAutomations(song, t.id)) {
        autos.push({ target: a.target, label: autoLabel(a.target), y: y + h, h: AUTO_H });
        h += AUTO_H;
      }
    }
    rows.push({ trackId: t.id, kind, y, h, contentY, contentH, lanes, pitchLo, pitchHi, autos, expanded });
    y += h + TRACK_GAP;
  }
  return { rows, height: y };
}

/** Count of hits (perf budget checks). */
export function hitCount(song: SongJson): number {
  return (song.tracks ?? []).reduce((n, t) => n + patternHits(song, t).length, 0);
}

export function fmtBarBeat(beat0: number): string {
  const bar = Math.floor(beat0 / BEATS_PER_BAR + 1e-9) + 1;
  const inBar = beat0 - (bar - 1) * BEATS_PER_BAR + 1;
  const b = Math.floor(inBar + 1e-9);
  const sub = Math.round((inBar - b) * 4);
  return sub ? `${bar}.${b}.${sub + 1}` : `${bar}.${b}`;
}

export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}
