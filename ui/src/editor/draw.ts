// Canvas drawing of the arrangement. Everything is computed from the song,
// the view and the layout; only what is visible is drawn (virtualised), so a
// 32-bar song with hundreds of hits and notes stays well under a frame.

import type { PieceSet } from "./arrange";
import { findMaterial } from "./arrange";
import {
  BEATS_PER_BAR,
  automationFor,
  automationValue,
  clipPasses,
  noteName,
  patternHits,
  scalePitchClasses,
  tonicOf,
  trackById,
  trackNotes,
  type SongJson,
} from "./song";
import type { Selection } from "./selection";
import { barLabelStep, beatToX, gridStep, type Row, type View } from "./timeline";

export const RULER_H = 26;

export interface Theme {
  bg: string;
  row: string;
  rowAlt: string;
  line: string;
  bar: string;
  ink: string;
  ink2: string;
  ink3: string;
  a: string;
  b: string;
  sun: string;
  warn: string;
  ok: string;
  sel: string;
  wave: string;
  loop: string;
}

export function readTheme(el: HTMLElement): Theme {
  const cs = getComputedStyle(el);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return {
    bg: v("--surface", "#141629"),
    row: v("--bg-2", "#101224"),
    rowAlt: v("--surface-2", "#1a1d35"),
    line: v("--line", "#2a2e4d"),
    bar: v("--line-2", "#363b60"),
    ink: v("--ink", "#eceaf6"),
    ink2: v("--ink-2", "#b4b3cc"),
    ink3: v("--ink-3", "#7f809f"),
    a: v("--a", "#e0468a"),
    b: v("--b", "#3593d6"),
    sun: v("--sun-3", "#ffbe5c"),
    warn: v("--warn", "#f0b545"),
    ok: v("--ok", "#43c59e"),
    sel: v("--focus", "#ffd36b"),
    wave: v("--wave-played", "#f27cb0"),
    loop: v("--b-soft", "rgba(53,147,214,0.18)"),
  };
}

// ------------------------------------------------------------------ peaks

const peakCache = new WeakMap<AudioBuffer, Float32Array>();
const PEAK_HOP = 128;

/** max |sample| per PEAK_HOP frames (mono), computed once per buffer. */
export function peaksOf(b: AudioBuffer): Float32Array {
  let p = peakCache.get(b);
  if (p) return p;
  const n = Math.ceil(b.length / PEAK_HOP);
  p = new Float32Array(n);
  const chans = Array.from({ length: b.numberOfChannels }, (_, i) => b.getChannelData(i));
  for (let i = 0; i < n; i++) {
    let m = 0;
    const end = Math.min(b.length, (i + 1) * PEAK_HOP);
    for (const ch of chans) for (let j = i * PEAK_HOP; j < end; j++) m = Math.max(m, Math.abs(ch[j]));
    p[i] = m;
  }
  peakCache.set(b, p);
  return p;
}

function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}

function alpha(color: string, a: number): string {
  if (color.startsWith("#") && color.length === 7) {
    const n = parseInt(color.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  return color;
}

/** stable palette per chop / sample */
export function colorFor(key: string, th: Theme): string {
  const palette = [th.a, th.b, th.sun, th.ok, "#a77bff", "#ff8a6b"];
  let h = 0;
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}

export interface DragPreview {
  /** clip move: track + clip index + beat delta */
  clip?: { track: string; clip: number; dBeats: number; mode: "move" | "left" | "right" | "stretch"; dLen: number };
  hit?: { track: string; lane: string; fromBeat: number; toBeat: number; velocity?: number };
  note?: { track: string; key: string; dBeats: number; dPitch: number; dLen: number };
  point?: { target: string; index: number; bar: number; value: number };
  marquee?: { x0: number; y0: number; x1: number; y1: number };
  loop?: { a: number; b: number };
}

export interface DrawInput {
  song: SongJson;
  view: View;
  rows: Row[];
  width: number;
  height: number;
  scrollY: number;
  selection: Selection;
  pieces: PieceSet;
  theme: Theme;
  loop: { startBeat: number; endBeat: number } | null;
  drag: DragPreview | null;
  requests: { track: string | null; beat0: number; text: string }[];
  status: Record<string, { state: string }>;
  stretchMode: boolean;
}

export function draw(g: CanvasRenderingContext2D, d: DrawInput): void {
  const { song, view, rows, width, height, theme: th } = d;
  const totalBeats = song.meta.length_bars * BEATS_PER_BAR;
  g.fillStyle = th.bg;
  g.fillRect(0, 0, width, height);
  const firstBeat = Math.max(0, view.scrollX / view.pxPerBeat);
  const lastBeat = Math.min(totalBeats, (view.scrollX + width) / view.pxPerBeat);
  const step = gridStep(view.pxPerBeat);
  const endX = beatToX(totalBeats, view);

  g.save();
  g.beginPath();
  g.rect(0, RULER_H, width, height - RULER_H);
  g.clip();
  g.translate(0, RULER_H - d.scrollY);
  const viewTop = d.scrollY;
  const viewBottom = d.scrollY + height - RULER_H;
  rows.forEach((row, i) => {
    if (row.y + row.h < viewTop || row.y > viewBottom) return;
    g.fillStyle = i % 2 ? th.rowAlt : th.row;
    g.fillRect(0, row.y, Math.min(width, endX), row.h);
    drawGrid(g, d, row.y, row.h, firstBeat, lastBeat, step);
    const track = trackById(song, row.trackId);
    if (!track) return;
    if (row.kind === "clips") drawClips(g, d, row);
    else if (row.kind === "pattern") drawPattern(g, d, row);
    else drawNotes(g, d, row);
    for (const a of row.autos) drawAutomation(g, d, row, a.target, a.y, a.h);
    // pending requests pinned on this track
    for (const r of d.requests) {
      if (r.track !== row.trackId) continue;
      const x = beatToX(r.beat0, view);
      if (x < -10 || x > width + 10) continue;
      g.fillStyle = th.sun;
      g.beginPath();
      g.moveTo(x, row.y + 2);
      g.lineTo(x + 9, row.y + 6);
      g.lineTo(x, row.y + 10);
      g.closePath();
      g.fill();
      g.fillRect(x - 1, row.y + 2, 1.5, 14);
    }
  });
  if (d.drag?.marquee) {
    const m = d.drag.marquee;
    g.fillStyle = alpha(th.sel, 0.1);
    g.strokeStyle = th.sel;
    g.lineWidth = 1;
    const x = Math.min(m.x0, m.x1);
    const y = Math.min(m.y0, m.y1);
    g.fillRect(x, y, Math.abs(m.x1 - m.x0), Math.abs(m.y1 - m.y0));
    g.strokeRect(x + 0.5, y + 0.5, Math.abs(m.x1 - m.x0), Math.abs(m.y1 - m.y0));
  }
  g.restore();
  // past the song end
  if (endX < width) {
    g.fillStyle = alpha(th.ink3, 0.06);
    g.fillRect(Math.max(0, endX), RULER_H, width - endX, height);
  }
  drawRuler(g, d, firstBeat, lastBeat);
}

function drawGrid(g: CanvasRenderingContext2D, d: DrawInput, y: number, h: number, first: number, last: number, step: number): void {
  const { view, theme: th } = d;
  const start = Math.floor(first / step) * step;
  for (let b = start; b <= last + 1e-9; b += step) {
    const x = Math.round(beatToX(b, view)) + 0.5;
    const isBar = Math.abs(b / BEATS_PER_BAR - Math.round(b / BEATS_PER_BAR)) < 1e-9;
    const isBeat = Math.abs(b - Math.round(b)) < 1e-9;
    g.strokeStyle = isBar ? th.bar : th.line;
    g.globalAlpha = isBar ? 1 : isBeat ? 0.55 : 0.22;
    g.beginPath();
    g.moveTo(x, y);
    g.lineTo(x, y + h);
    g.stroke();
  }
  g.globalAlpha = 1;
}

function drawRuler(g: CanvasRenderingContext2D, d: DrawInput, first: number, last: number): void {
  const { view, theme: th, width } = d;
  g.fillStyle = th.bg;
  g.fillRect(0, 0, width, RULER_H);
  const loop = d.drag?.loop ? { startBeat: Math.min(d.drag.loop.a, d.drag.loop.b), endBeat: Math.max(d.drag.loop.a, d.drag.loop.b) } : d.loop;
  if (loop) {
    const x0 = beatToX(loop.startBeat, view);
    const x1 = beatToX(loop.endBeat, view);
    g.fillStyle = alpha(th.b, 0.28);
    g.fillRect(x0, 3, x1 - x0, RULER_H - 6);
    g.fillStyle = th.b;
    g.fillRect(x0, 3, 2, RULER_H - 6);
    g.fillRect(x1 - 2, 3, 2, RULER_H - 6);
  }
  const labelStep = barLabelStep(view.pxPerBeat);
  g.font = "600 11px 'JetBrains Mono Variable', ui-monospace, monospace";
  g.textBaseline = "middle";
  const startBar = Math.floor(first / BEATS_PER_BAR);
  const endBar = Math.ceil(last / BEATS_PER_BAR);
  for (let bar = startBar; bar <= endBar; bar++) {
    const x = Math.round(beatToX(bar * BEATS_PER_BAR, view)) + 0.5;
    g.strokeStyle = th.bar;
    g.beginPath();
    g.moveTo(x, RULER_H - 9);
    g.lineTo(x, RULER_H);
    g.stroke();
    if (bar % labelStep === 0 && bar < d.song.meta.length_bars) {
      g.fillStyle = th.ink2;
      g.fillText(String(bar + 1), x + 4, RULER_H / 2);
    }
    if (view.pxPerBeat >= 12) {
      for (let b = 1; b < BEATS_PER_BAR; b++) {
        const bx = Math.round(beatToX(bar * BEATS_PER_BAR + b, view)) + 0.5;
        g.strokeStyle = th.line;
        g.beginPath();
        g.moveTo(bx, RULER_H - 5);
        g.lineTo(bx, RULER_H);
        g.stroke();
      }
    }
  }
  g.strokeStyle = th.line;
  g.beginPath();
  g.moveTo(0, RULER_H - 0.5);
  g.lineTo(width, RULER_H - 0.5);
  g.stroke();
}

function drawClips(g: CanvasRenderingContext2D, d: DrawInput, row: Row): void {
  const { song, view, theme: th, width } = d;
  const track = trackById(song, row.trackId)!;
  const passes = clipPasses(song, track);
  const sel = d.selection.kind === "clips" && d.selection.track === row.trackId ? d.selection.items : [];
  const y = row.contentY + 4;
  const h = row.contentH - 8;
  const spb = 60 / song.meta.bpm;
  const drag = d.drag?.clip?.track === row.trackId ? d.drag.clip : null;
  for (const p of passes) {
    let start = p.start0;
    let len = p.length;
    if (drag && drag.clip === p.clipIndex) {
      if (drag.mode === "move") start += drag.dBeats;
      else if (drag.mode === "left") {
        start += drag.dBeats;
        len -= drag.dBeats;
      } else len += drag.dLen;
    }
    const x0 = beatToX(start, view);
    const x1 = beatToX(start + len, view);
    if (x1 < 0 || x0 > width) continue;
    const color = colorFor(p.chop ?? p.sample ?? "?", th);
    const isSel = sel.some((s) => s.clip === p.clipIndex && (s.instance == null || s.instance === p.instance));
    const clipSel = sel.some((s) => s.clip === p.clipIndex);
    g.globalAlpha = p.mute ? 0.35 : 1;
    roundRect(g, x0 + 0.5, y, Math.max(2, x1 - x0 - 1), h, 5);
    g.fillStyle = alpha(color, p.mute ? 0.1 : 0.2);
    g.fill();
    g.strokeStyle = isSel ? th.sel : clipSel ? alpha(th.sel, 0.6) : alpha(color, 0.75);
    g.lineWidth = isSel ? 2 : 1;
    g.stroke();
    // waveform from the piece (if loaded)
    const found = findMaterial(d.pieces, song, track, p);
    if (found && x1 - x0 > 3 && !p.mute) {
      const peaks = peaksOf(found.m.buffer);
      const sr = found.m.buffer.sampleRate;
      const mid = y + h / 2 + 6;
      const amp = (h - 18) / 2;
      g.fillStyle = alpha(color, 0.85);
      const xa = Math.max(0, Math.floor(x0));
      const xb = Math.min(width, Math.ceil(x1));
      const gain = 10 ** (p.gainDb / 20);
      for (let x = xa; x < xb; x++) {
        const fracA = (x - x0) / (x1 - x0);
        const fracB = (x + 1 - x0) / (x1 - x0);
        const ta = p.reverse ? 1 - fracB : fracA;
        const tb = p.reverse ? 1 - fracA : fracB;
        const fa = Math.floor(((found.offsetBeats + ta * len) * spb * sr) / PEAK_HOP);
        const fb = Math.ceil(((found.offsetBeats + tb * len) * spb * sr) / PEAK_HOP);
        let m = 0;
        for (let i = Math.max(0, fa); i < Math.min(peaks.length, Math.max(fa + 1, fb)); i++) m = Math.max(m, peaks[i]);
        const hh = Math.max(0.5, Math.min(1, m * gain) * amp);
        g.fillRect(x, mid - hh, 1, hh * 2);
      }
    } else if (!found && !p.mute) {
      g.fillStyle = alpha(th.ink3, 0.5);
      g.font = "11px 'Inter Variable', system-ui, sans-serif";
      g.fillText("preparando audio…", x0 + 6, y + h / 2 + 8);
    }
    g.globalAlpha = 1;
    // labels: on the first pass of a clip, and badges per pass
    g.font = "600 11px 'Inter Variable', system-ui, sans-serif";
    g.textBaseline = "top";
    const clip = track.clips![p.clipIndex];
    if (p.instance === 1 && x1 - x0 > 26) {
      const name = clip.chop ?? String(clip.sample ?? "").split("/").pop()!.replace(/\.[^.]+$/, "");
      const rep = (clip.repeat ?? 1) > 1 ? ` ×${clip.repeat}` : "";
      g.fillStyle = th.ink;
      g.fillText(`${name}${rep}`, Math.max(x0, 0) + 6, y + 4, Math.max(10, x1 - x0 - 10));
    }
    const badges: string[] = [];
    if (p.mute) badges.push("mute");
    if (p.reverse) badges.push("⟲");
    if (p.pitch) badges.push(`${p.pitch > 0 ? "+" : ""}${p.pitch} st`);
    if (p.stretch !== 1) badges.push(`×${p.stretch}`);
    if (p.gainDb && clip.instances?.[String(p.instance)]?.gain_db) badges.push(`${p.gainDb > 0 ? "+" : ""}${p.gainDb} dB`);
    if (badges.length && x1 - x0 > 30) {
      g.fillStyle = th.sun;
      g.font = "600 10px 'Inter Variable', system-ui, sans-serif";
      g.fillText(badges.join(" · "), x0 + 6, y + h - 14, x1 - x0 - 10);
    }
    // pass boundary (repeat seams)
    if (p.instance > 1) {
      g.strokeStyle = alpha(color, 0.9);
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(Math.round(x0) + 0.5, y + 3);
      g.lineTo(Math.round(x0) + 0.5, y + h - 3);
      g.stroke();
      g.setLineDash([]);
    }
  }
}

function drawPattern(g: CanvasRenderingContext2D, d: DrawInput, row: Row): void {
  const { song, view, theme: th, width } = d;
  const track = trackById(song, row.trackId)!;
  const p = track.pattern!;
  const spb = p.steps_per_bar ?? 16;
  const stepPx = (view.pxPerBeat * BEATS_PER_BAR) / spb;
  const hits = patternHits(song, track);
  const selHits = d.selection.kind === "hits" && d.selection.track === row.trackId ? d.selection.items : [];
  const at = track.at_bar ?? 1;
  const until = track.until_bar ?? song.meta.length_bars + 1;
  const firstBar = Math.max(at, Math.floor(view.scrollX / view.pxPerBeat / BEATS_PER_BAR) + 1);
  const lastBar = Math.min(until - 1, Math.ceil((view.scrollX + width) / view.pxPerBeat / BEATS_PER_BAR));
  if (!row.expanded) {
    // collapsed: all lanes as ticks
    const names = Object.keys(p.lanes);
    for (const h of hits) {
      const x = beatToX(h.beat0, view);
      if (x < -4 || x > width + 4) continue;
      const li = names.indexOf(h.lane);
      const yy = row.contentY + 4 + (li / Math.max(1, names.length)) * (row.contentH - 8);
      g.fillStyle = h.origin === "free" ? th.b : h.origin === "override" ? th.sun : th.a;
      g.globalAlpha = 0.4 + 0.6 * h.velocity;
      g.fillRect(x, yy, Math.max(2, stepPx * 0.6), Math.max(2, (row.contentH - 8) / names.length - 1));
    }
    g.globalAlpha = 1;
    return;
  }
  for (const lane of row.lanes) {
    const lj = p.lanes[lane.name];
    // overridden bars are tinted
    for (const k of Object.keys(lj?.overrides ?? {})) {
      const bar = Number(k);
      if (bar < firstBar - 1 || bar > lastBar + 1) continue;
      const x0 = beatToX((bar - 1) * BEATS_PER_BAR, view);
      g.fillStyle = alpha(th.sun, 0.07);
      g.fillRect(x0, lane.y, view.pxPerBeat * BEATS_PER_BAR, lane.h);
    }
    // empty cells (when there is room)
    if (stepPx >= 7) {
      g.fillStyle = alpha(th.ink3, 0.1);
      for (let bar = firstBar; bar <= lastBar; bar++) {
        for (let s = 0; s < spb; s++) {
          const x = beatToX((bar - 1) * BEATS_PER_BAR + (s * BEATS_PER_BAR) / spb, view);
          roundRect(g, x + 1, lane.y + 2, stepPx - 2, lane.h - 4, 2);
          g.fill();
        }
      }
    }
    g.strokeStyle = th.line;
    g.beginPath();
    g.moveTo(0, lane.y + lane.h + 0.5);
    g.lineTo(width, lane.y + lane.h + 0.5);
    g.stroke();
  }
  const laneY = new Map(row.lanes.map((l) => [l.name, l]));
  const drag = d.drag?.hit?.track === row.trackId ? d.drag.hit : null;
  for (const h of hits) {
    const lane = laneY.get(h.lane);
    if (!lane) continue;
    let beat = h.beat0;
    let vel = h.velocity;
    if (drag && drag.lane === h.lane && Math.abs(drag.fromBeat - h.beat0) < 1e-6) {
      beat = drag.toBeat;
      if (drag.velocity != null) vel = drag.velocity;
    }
    const x = beatToX(beat, view);
    if (x < -stepPx || x > width + 2) continue;
    const sel = selHits.some((s) => s.lane === h.lane && Math.abs(s.beat0 - h.beat0) < 1e-6);
    const color = h.origin === "free" ? th.b : h.origin === "override" ? th.sun : th.a;
    const w = Math.max(3, Math.min(stepPx - 2, 22));
    const fullH = lane.h - 4;
    const hh = Math.max(3, fullH * (0.35 + 0.65 * vel));
    g.fillStyle = color;
    g.globalAlpha = 0.45 + 0.55 * vel;
    if (h.origin === "free") {
      // diamond at the exact time
      const cx = x;
      const cy = lane.y + lane.h / 2;
      const r = Math.min(lane.h / 2 - 2, 7);
      g.beginPath();
      g.moveTo(cx, cy - r);
      g.lineTo(cx + r, cy);
      g.lineTo(cx, cy + r);
      g.lineTo(cx - r, cy);
      g.closePath();
      g.fill();
    } else {
      roundRect(g, x + 1, lane.y + 2 + (fullH - hh), w, hh, 2);
      g.fill();
    }
    g.globalAlpha = 1;
    if (sel) {
      g.strokeStyle = th.sel;
      g.lineWidth = 2;
      if (h.origin === "free") g.strokeRect(x - 7, lane.y + 2, 14, lane.h - 4);
      else {
        roundRect(g, x + 0.5, lane.y + 1.5, w + 1, fullH + 1, 3);
        g.stroke();
      }
      g.lineWidth = 1;
    }
  }
}

function drawNotes(g: CanvasRenderingContext2D, d: DrawInput, row: Row): void {
  const { song, view, theme: th, width } = d;
  const track = trackById(song, row.trackId)!;
  if (track.midi) {
    g.fillStyle = th.ink3;
    g.font = "12px 'Inter Variable', system-ui, sans-serif";
    g.textBaseline = "middle";
    g.fillText(`Notas desde ${track.midi} (para editarlas, pedile al agente que las pase a notes/phrases)`, 12, row.contentY + row.contentH / 2);
    return;
  }
  const notes = trackNotes(song, track);
  const lo = row.pitchLo;
  const hi = row.pitchHi;
  const rowH = row.contentH / (hi - lo + 1);
  const scale = scalePitchClasses(song.meta.key);
  const tonic = tonicOf(song.meta.key);
  if (row.expanded) {
    for (let p = lo; p <= hi; p++) {
      const y = row.contentY + (hi - p) * rowH;
      const pc = p % 12;
      if (pc === tonic) g.fillStyle = alpha(th.a, 0.1);
      else if (scale.has(pc)) g.fillStyle = alpha(th.ink, 0.035);
      else g.fillStyle = alpha("#000000", 0.12);
      g.fillRect(0, y, width, rowH);
      if (rowH >= 9 && (pc === 0 || pc === tonic)) {
        g.fillStyle = pc === tonic ? th.a : th.ink3;
        g.font = "10px 'JetBrains Mono Variable', ui-monospace, monospace";
        g.textBaseline = "middle";
        g.fillText(noteName(p), 4, y + rowH / 2);
      }
    }
  }
  const sel = d.selection.kind === "notes" && d.selection.track === row.trackId ? d.selection.items : [];
  const drag = d.drag?.note?.track === row.trackId ? d.drag.note : null;
  for (const n of notes) {
    const key = noteKey(n);
    let beat0 = n.beat - 1;
    let pitch = n.pitch;
    let len = n.length;
    if (drag && sel.some((s) => noteKeyOf(s) === key || drag.key === key)) {
      if (sel.some((s) => noteKeyOf(s) === key) || drag.key === key) {
        beat0 += drag.dBeats;
        pitch += drag.dPitch;
        len = Math.max(0.0625, len + drag.dLen);
      }
    }
    const x0 = beatToX(beat0, view);
    const x1 = beatToX(beat0 + len, view);
    if (x1 < 0 || x0 > width) continue;
    const y = row.contentY + (hi - pitch) * rowH;
    const isSel = sel.some((s) => noteKeyOf(s) === key);
    g.fillStyle = n.phrase ? th.b : th.a;
    g.globalAlpha = 0.5 + 0.5 * (n.velocity / 127);
    roundRect(g, x0 + 0.5, y + 0.5, Math.max(2, x1 - x0 - 1), Math.max(2, rowH - 1), Math.min(3, rowH / 3));
    g.fill();
    g.globalAlpha = 1;
    if (isSel) {
      g.strokeStyle = th.sel;
      g.lineWidth = 2;
      g.stroke();
      g.lineWidth = 1;
    }
    if (row.expanded && rowH >= 11 && x1 - x0 > 26) {
      g.fillStyle = "#14101a";
      g.font = "600 9px 'Inter Variable', system-ui, sans-serif";
      g.textBaseline = "middle";
      g.fillText(noteName(pitch), x0 + 3, y + rowH / 2, x1 - x0 - 4);
    }
  }
}

export function noteKey(n: { beat: number; pitch: number; index: number | null; phrase: string | null; phraseIndex: number | null }): string {
  return n.phrase ? `p:${n.phrase}:${n.phraseIndex}:${n.beat}` : `n:${n.index}`;
}

export function noteKeyOf(n: { beat: number; index: number | null; phrase: string | null; phraseIndex: number | null }): string {
  return n.phrase ? `p:${n.phrase}:${n.phraseIndex}:${n.beat}` : `n:${n.index}`;
}

export function autoRange(target: string): [number, number] {
  if (target.endsWith("cutoff_hz") || target.endsWith("frequency_hz")) return [20, 20000];
  if (target.endsWith(".pan")) return [-1, 1];
  if (target.endsWith("gain_db") || target.endsWith("_db")) return [-60, 12];
  if (target.endsWith("depth") || target.endsWith("mix") || target.endsWith("level") || target.endsWith("feedback")) return [0, 1];
  return [0, 1];
}

export function autoLog(target: string): boolean {
  return target.endsWith("_hz");
}

export function valueToY(v: number, target: string, y: number, h: number): number {
  const [lo, hi] = autoRange(target);
  const t = autoLog(target) ? (Math.log(Math.max(lo, v)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) : (v - lo) / (hi - lo);
  return y + h - 6 - Math.min(1, Math.max(0, t)) * (h - 12);
}

export function yToValue(py: number, target: string, y: number, h: number): number {
  const [lo, hi] = autoRange(target);
  const t = Math.min(1, Math.max(0, (y + h - 6 - py) / (h - 12)));
  return autoLog(target) ? Math.exp(Math.log(lo) + t * (Math.log(hi) - Math.log(lo))) : lo + t * (hi - lo);
}

function drawAutomation(g: CanvasRenderingContext2D, d: DrawInput, row: Row, target: string, y: number, h: number): void {
  const { song, view, theme: th, width } = d;
  const a = automationFor(song, target);
  g.fillStyle = alpha(th.ink, 0.02);
  g.fillRect(0, y, width, h);
  g.strokeStyle = th.line;
  g.beginPath();
  g.moveTo(0, y + 0.5);
  g.lineTo(width, y + 0.5);
  g.stroke();
  if (!a) return;
  const pts = a.points.map(([bar, v], i) => {
    const dp = d.drag?.point;
    if (dp && dp.target === target && dp.index === i) return [dp.bar, dp.value] as [number, number];
    return [bar, v] as [number, number];
  });
  const preview = { ...a, points: pts };
  g.strokeStyle = th.b;
  g.lineWidth = 2;
  g.beginPath();
  for (let x = 0; x <= width; x += 3) {
    const bar = (x + view.scrollX) / view.pxPerBeat / BEATS_PER_BAR + 1;
    if (bar > song.meta.length_bars + 1) break;
    const yy = valueToY(automationValue(preview, bar), target, y, h);
    if (x === 0) g.moveTo(x, yy);
    else g.lineTo(x, yy);
  }
  g.stroke();
  g.lineWidth = 1;
  const sel = d.selection.kind === "points" && d.selection.target === target ? d.selection.items : [];
  pts.forEach(([bar, v], i) => {
    const x = beatToX((bar - 1) * BEATS_PER_BAR, view);
    const yy = valueToY(v, target, y, h);
    g.beginPath();
    g.arc(x, yy, sel.includes(i) ? 6 : 4.5, 0, Math.PI * 2);
    g.fillStyle = sel.includes(i) ? th.sel : th.b;
    g.fill();
    g.strokeStyle = th.bg;
    g.stroke();
  });
  g.fillStyle = th.ink3;
  g.font = "10px 'Inter Variable', system-ui, sans-serif";
  g.textBaseline = "top";
  g.fillText(row.autos.find((x) => x.target === target)?.label ?? target, 6, y + 4);
}
