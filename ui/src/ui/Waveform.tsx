import { useEffect, useMemo, useRef, useState } from "react";
import type { Alarm, CommentEvent } from "../api/types";
import { useRaf, useSize } from "../hooks/hooks";
import {
  BEATS_PER_BAR,
  barSeconds,
  beatsToSeconds,
  formatBarBeat,
  formatClock,
  loopFromDrag,
  moveLoopEdge,
  secondsToBeats,
  shiftLoop,
  type LoopBeats,
  type Timing,
} from "../music/time";

const RULER_H = 18;

export interface WaveMarker {
  kind: "comment" | "alarm";
  atS: number;
  label: string;
  pending?: boolean;
  severity?: Alarm["severity"];
  event?: CommentEvent;
}

interface Props {
  timing: Timing | null;
  peaks: Float32Array | undefined;
  /** render-relative loop (beats) and whether it is active */
  loop: LoopBeats | null;
  loopOn: boolean;
  markers: WaveMarker[];
  theme: string;
  getPosition: () => number;
  onSeek: (s: number) => void;
  onLoop: (rel: LoopBeats) => void;
  label: string;
}

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || "#888";
}

function drawWave(canvas: HTMLCanvasElement, peaks: Float32Array, w: number, h: number, color: string) {
  const dpr = Math.min(3, window.devicePixelRatio || 1);
  canvas.width = Math.max(1, Math.round(w * dpr));
  canvas.height = Math.max(1, Math.round(h * dpr));
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const top = RULER_H + 4;
  const ah = h - top;
  const mid = top + ah / 2;
  const n = peaks.length / 2;
  if (n === 0) return;
  ctx.fillStyle = color;
  const step = 2; // 1px bar + 1px gap at CSS resolution
  for (let x = 0; x < w; x += step) {
    const b0 = Math.floor((x / w) * n);
    const b1 = Math.max(b0 + 1, Math.floor(((x + step) / w) * n));
    let mn = 0;
    let mx = 0;
    for (let b = b0; b < b1 && b < n; b++) {
      if (peaks[b * 2] < mn) mn = peaks[b * 2];
      if (peaks[b * 2 + 1] > mx) mx = peaks[b * 2 + 1];
    }
    const yTop = mid - Math.min(1, mx) * (ah / 2) * 0.96;
    const yBot = mid - Math.max(-1, mn) * (ah / 2) * 0.96;
    ctx.fillRect(x, yTop, 1.2, Math.max(1, yBot - yTop));
  }
}

function drawRuler(canvas: HTMLCanvasElement, t: Timing, w: number, h: number, el: Element) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const ink = cssVar(el, "--ink-3");
  const line = cssVar(el, "--line");
  const grid = cssVar(el, "--grid");
  const pxPerS = w / t.durationS;
  const bs = barSeconds(t.bpm);
  const nBars = Math.ceil(t.durationS / bs - 1e-6);
  const pxPerBar = bs * pxPerS;
  const every = pxPerBar >= 34 ? 1 : pxPerBar >= 17 ? 2 : pxPerBar >= 8 ? 4 : 8;
  ctx.font = `500 10.5px ${getComputedStyle(el).getPropertyValue("--font-mono") || "monospace"}`;
  ctx.textBaseline = "middle";
  for (let i = 0; i <= nBars; i++) {
    const x = Math.round(i * pxPerBar) + 0.5;
    // bar grid through the waveform
    ctx.fillStyle = grid;
    ctx.fillRect(x - 0.5, RULER_H, 1, h - RULER_H);
    ctx.fillStyle = line;
    ctx.fillRect(x - 0.5, RULER_H - 7, 1, 7);
    if (i < nBars && i % every === 0) {
      ctx.fillStyle = ink;
      ctx.fillText(String(t.firstBar + i), x + 4, RULER_H / 2);
    }
    if (pxPerBar >= 48 && i < nBars) {
      ctx.fillStyle = line;
      for (let b = 1; b < BEATS_PER_BAR; b++) ctx.fillRect(Math.round(x + (b * pxPerBar) / BEATS_PER_BAR), RULER_H - 3, 1, 3);
    }
  }
}

export function Waveform(p: Props) {
  const [wrapRef, size] = useSize<HTMLDivElement>();
  const baseRef = useRef<HTMLCanvasElement>(null);
  const playedRef = useRef<HTMLCanvasElement>(null);
  const playedWrap = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<LoopBeats | null>(null);
  const [tip, setTip] = useState<{ x: number; text: string } | null>(null);
  const gesture = useRef<{ mode: "pending" | "loop" | "start" | "end" | "move"; x0: number; beat0: number; base: LoopBeats | null; id: number } | null>(null);
  const { timing, peaks } = p;
  const w = size.w;
  const h = size.h;

  useEffect(() => {
    const base = baseRef.current;
    const played = playedRef.current;
    const el = wrapRef.current;
    if (!base || !played || !el || !w || !h) return;
    if (peaks) {
      drawWave(base, peaks, w, h, cssVar(el, "--wave"));
      drawWave(played, peaks, w, h, cssVar(el, "--wave-played"));
    } else {
      const dpr = Math.min(3, window.devicePixelRatio || 1);
      base.width = Math.round(w * dpr);
      base.height = Math.round(h * dpr);
      base.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
      played.width = 1;
      played.height = 1;
    }
    if (timing) drawRuler(base, timing, w, h, el);
  }, [peaks, timing, w, h, p.theme, wrapRef]);

  useRaf(() => {
    if (!timing || !w) return;
    const pos = p.getPosition();
    const frac = Math.min(1, Math.max(0, pos / timing.durationS));
    if (headRef.current) headRef.current.style.transform = `translateX(${(frac * w).toFixed(1)}px)`;
    if (playedWrap.current) playedWrap.current.style.clipPath = `inset(0 ${((1 - frac) * 100).toFixed(3)}% 0 0)`;
  });

  const maxBeats = timing ? secondsToBeats(timing.durationS, timing.bpm) : 0;
  const beatAtX = (clientX: number): number => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || !timing) return 0;
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return frac * maxBeats;
  };
  const xOfS = (s: number) => (timing ? (s / timing.durationS) * w : 0);

  const shown = drag ?? p.loop;

  const onDown = (e: React.PointerEvent, mode: "pending" | "start" | "end" | "move") => {
    if (!timing || e.button > 0) return;
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    gesture.current = { mode, x0: e.clientX, beat0: beatAtX(e.clientX), base: p.loop, id: e.pointerId };
  };

  const onMove = (e: React.PointerEvent) => {
    const g = gesture.current;
    if (!g || !timing || g.id !== e.pointerId) return;
    const beat = beatAtX(e.clientX);
    if (g.mode === "pending" && Math.abs(e.clientX - g.x0) > 6) g.mode = "loop";
    if (g.mode === "loop") setDrag(loopFromDrag(g.beat0, beat, maxBeats));
    else if ((g.mode === "start" || g.mode === "end") && g.base) setDrag(moveLoopEdge(g.base, g.mode, beat, maxBeats));
    else if (g.mode === "move" && g.base) setDrag(shiftLoop(g.base, beat - g.beat0, maxBeats));
  };

  const onUp = (e: React.PointerEvent) => {
    const g = gesture.current;
    gesture.current = null;
    if (!g || !timing) return;
    if (g.mode === "pending") {
      p.onSeek(beatsToSeconds(beatAtX(e.clientX), timing.bpm));
    } else if (g.mode === "move" && Math.abs(e.clientX - g.x0) <= 6) {
      p.onSeek(beatsToSeconds(beatAtX(e.clientX), timing.bpm));
    } else if (drag) {
      p.onLoop(drag);
    }
    setDrag(null);
  };

  const ariaNow = timing ? formatBarBeat(p.getPosition(), timing) : "";
  const markers = useMemo(() => (timing ? p.markers.filter((m) => m.atS >= 0 && m.atS <= timing.durationS) : []), [p.markers, timing]);

  return (
    <div
      className="wave"
      ref={wrapRef}
      onPointerDown={(e) => onDown(e, "pending")}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={() => {
        gesture.current = null;
        setDrag(null);
      }}
      role="group"
      aria-label={`${p.label}. Clic para ir a un punto, arrastrá para marcar un loop por compases.`}
      data-testid="waveform"
    >
      {!peaks && <div className="wave-skel" aria-hidden="true" />}
      <canvas ref={baseRef} aria-hidden="true" />
      <div className="wave-played" ref={playedWrap} aria-hidden="true">
        <canvas ref={playedRef} />
      </div>
      {shown && timing && w > 0 && (
        <div
          className={`loop-region${p.loopOn || drag ? "" : " off"}`}
          style={{ left: xOfS(beatsToSeconds(shown.startBeat, timing.bpm)), width: xOfS(beatsToSeconds(shown.endBeat - shown.startBeat, timing.bpm)) }}
          onPointerDown={(e) => onDown(e, "move")}
          aria-label={`Loop compases ${timing.firstBar + shown.startBeat / BEATS_PER_BAR} a ${timing.firstBar + shown.endBeat / BEATS_PER_BAR - 1}`}
          data-testid="loop-region"
        >
          <button type="button" className="loop-handle start" aria-label="Mover inicio del loop" onPointerDown={(e) => onDown(e, "start")} tabIndex={-1} />
          <button type="button" className="loop-handle end" aria-label="Mover fin del loop" onPointerDown={(e) => onDown(e, "end")} tabIndex={-1} />
        </div>
      )}
      {timing &&
        w > 0 &&
        markers.map((m, i) => (
          <button
            key={`${m.kind}-${i}-${m.atS}`}
            type="button"
            className={`marker ${m.kind}${m.pending ? " pending" : ""}${m.severity === "error" ? " error" : ""}`}
            style={{ left: xOfS(m.atS) }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              p.onSeek(m.atS);
            }}
            onMouseEnter={() => setTip({ x: xOfS(m.atS), text: m.label })}
            onMouseLeave={() => setTip(null)}
            onFocus={() => setTip({ x: xOfS(m.atS), text: m.label })}
            onBlur={() => setTip(null)}
            aria-label={`${m.kind === "comment" ? "Comentario" : "Alarma"} en ${formatBarBeat(m.atS, timing)} (${formatClock(m.atS)}): ${m.label}`}
          >
            {m.kind === "comment" ? (
              <span className="pin" />
            ) : (
              <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" fill="currentColor" />
              </svg>
            )}
          </button>
        ))}
      <div className="playhead" ref={headRef} aria-hidden="true" />
      {tip && (
        <div className="wave-tip" style={{ left: Math.min(Math.max(tip.x, 110), Math.max(110, w - 110)) }} role="tooltip">
          {tip.text}
        </div>
      )}
      <span className="sr-only" aria-live="off">
        {ariaNow}
      </span>
    </div>
  );
}
