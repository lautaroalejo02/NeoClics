import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useRaf } from "../../hooks/hooks";
import { useApp } from "../../state/react";
import { addHit, addNote, addPoint, changeNotes, deleteHits, hitSel, moveClip, moveHits, movePoint, noteSelOf, notesIn, resizeClipLeft, resizeClipRight, setHitVelocity } from "../../editor/actions";
import { RULER_H, draw, noteKey, readTheme, yToValue, type DragPreview, type Theme } from "../../editor/draw";
import { hitTest, pitchAt, rowAt, type Target } from "../../editor/hit";
import { useEditor, useEditorStore } from "../../editor/react";
import { drawTimes } from "../../editor/perf";
import type { HitSel, Selection } from "../../editor/selection";
import { BEATS_PER_BAR, clipBaseLength, clipStart0, passBeats, patternHits, trackById } from "../../editor/song";
import { SNAP_BEATS, beatToX, clamp, layoutRows, snapBeat, xToBeat, zoomAt } from "../../editor/timeline";

interface DragState {
  pointerId: number;
  target: Target;
  x0: number;
  y0: number;
  beat0: number;
  moved: boolean;
  alt: boolean;
  shift: boolean;
  touch: boolean;
  mode: "clip-move" | "clip-left" | "clip-right" | "hit" | "note" | "note-right" | "point" | "marquee" | "pan" | "loop" | "seek";
  scroll0: number;
  scrollY0: number;
  vel0?: number;
  axis?: "x" | "y";
  longPress?: ReturnType<typeof setTimeout>;
}

const MOVE_PX = 4;

export function Timeline({ onAudition }: { onAudition: (sample: string, gain?: number) => void }) {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const view = useEditor((s) => s.view);
  const selection = useEditor((s) => s.selection);
  const rev = useEditor((s) => s.rev);
  const loop = useEditor((s) => s.loop);
  const scrollY = useEditor((s) => s.scrollY);
  const stretchMode = useEditor((s) => s.stretchMode);
  const status = useEditor((s) => s.trackStatus);
  const themeName = useApp((s) => s.theme);
  const feedback = useApp((s) => s.feedback);
  const versions = useApp((s) => s.versions);
  const metas = useApp((s) => s.metas);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 300 });
  const [dragPreview, setDragPreview] = useState<DragPreview | null>(null);
  const drag = useRef<DragState | null>(null);
  const lastClick = useRef<{ t: number; x: number; y: number }>({ t: 0, x: 0, y: 0 });
  const themeRef = useRef<Theme | null>(null);

  const fitH = useEditor((s) => s.fitH);
  const { rows, height } = useMemo(() => layoutRows(song, view, fitH), [song, view, fitH]);

  // pending agent requests pinned on the timeline (newer than the last version)
  const requests = useMemo(() => {
    const latest = versions.length ? Date.parse(metas[versions[versions.length - 1].version]?.created_at ?? "") || 0 : 0;
    return feedback
      .filter((e) => e.type === "comment" && e.selection && Date.parse(e.ts) > latest)
      .map((e) => {
        const sel = (e as { selection?: { track?: string; start_beat?: number } }).selection!;
        return { track: sel.track ?? null, beat0: (sel.start_beat ?? 1) - 1, text: (e as { text: string }).text };
      });
  }, [feedback, versions, metas]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
      editor.setFitHeight(el.clientHeight - RULER_H);
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, [editor]);

  useEffect(() => {
    if (wrapRef.current) themeRef.current = readTheme(wrapRef.current);
  }, [themeName]);

  // redraw (one frame) on any visual change
  const frame = useRef(0);
  const redraw = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const c = canvasRef.current;
      if (!c || !wrapRef.current) return;
      const dpr = window.devicePixelRatio || 1;
      if (c.width !== Math.round(size.w * dpr) || c.height !== Math.round(size.h * dpr)) {
        c.width = Math.round(size.w * dpr);
        c.height = Math.round(size.h * dpr);
      }
      const g = c.getContext("2d");
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      const t0 = performance.now();
      draw(g, {
        song,
        view,
        rows,
        width: size.w,
        height: size.h,
        scrollY,
        selection,
        pieces: editor.piecesSet(),
        theme: themeRef.current ?? readTheme(wrapRef.current),
        loop,
        drag: dragPreview,
        requests,
        status,
        stretchMode,
      });
      drawTimes.push(performance.now() - t0);
      if (drawTimes.length > 300) drawTimes.shift();
    });
  }, [song, view, rows, size, scrollY, selection, editor, loop, dragPreview, requests, status, stretchMode]);
  useEffect(redraw, [redraw, rev, themeName]);

  // playhead: written to the DOM every frame, no React render
  useRaf(() => {
    const el = headRef.current;
    if (!el) return;
    const beat = editor.playheadBeat();
    const x = beatToX(beat, editor.state.view);
    el.style.transform = `translateX(${Math.round(x)}px)`;
    el.style.opacity = x < -2 || x > size.w + 2 ? "0" : "1";
  });

  const maxScrollY = Math.max(0, height + RULER_H - size.h + 20);
  const setScrollY = (y: number) => editor.setScrollY(clamp(y, 0, maxScrollY));
  const setScrollX = (x: number) => editor.setView({ scrollX: Math.max(0, x) }, size.w);

  // ------------------------------------------------------------ helpers

  const local = (e: { clientX: number; clientY: number }) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const free = (e: { altKey: boolean }) => e.altKey || view.snap === "off";
  const snap = (b: number, e: { altKey: boolean }) => snapBeat(b, view.snap, free(e));

  const selectHit = (track: string, h: HitSel, add: boolean) => {
    const cur = selection.kind === "hits" && selection.track === track ? selection.items : [];
    const has = cur.some((x) => x.lane === h.lane && Math.abs(x.beat0 - h.beat0) < 1e-6);
    const items = add ? (has ? cur.filter((x) => !(x.lane === h.lane && Math.abs(x.beat0 - h.beat0) < 1e-6)) : [...cur, h]) : [h];
    editor.select(items.length ? { kind: "hits", track, items } : { kind: "none" });
  };

  // ------------------------------------------------------------ pointer

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || e.button === 2) return;
    const { x, y } = local(e);
    const target = hitTest(song, view, rows, x, y, scrollY);
    const touch = e.pointerType === "touch";
    let mode: DragState["mode"];
    switch (target.type) {
      case "ruler":
        mode = "loop";
        break;
      case "clip":
        mode = target.edge === "left" ? "clip-left" : target.edge === "right" ? "clip-right" : "clip-move";
        break;
      case "hit":
        mode = "hit";
        break;
      case "note":
        mode = target.edge === "right" ? "note-right" : "note";
        break;
      case "point":
        mode = "point";
        break;
      default:
        mode = touch ? "pan" : "marquee";
    }
    if (target.type !== "none" && "track" in target) editor.setFocusTrack(target.track);
    const vel0 = target.type === "hit" ? target.hit.velocity : undefined;
    const st: DragState = { pointerId: e.pointerId, target, x0: x, y0: y, beat0: target.type === "none" ? 0 : xToBeat(x, view), moved: false, alt: e.altKey, shift: e.shiftKey, touch, mode, scroll0: view.scrollX, scrollY0: scrollY, vel0 };
    if (touch && (target.type === "hit" || target.type === "clip" || target.type === "note")) {
      // long press selects (phones have no shift)
      st.longPress = setTimeout(() => {
        if (drag.current === st && !st.moved) {
          st.mode = "pan";
          if (target.type === "hit") selectHit(target.track, hitSel(target.hit), true);
          if (target.type === "clip") editor.select({ kind: "clips", track: target.track, items: [{ clip: target.clip, instance: target.instance }] });
          if (target.type === "note") editor.select({ kind: "notes", track: target.track, items: [noteSelOf(target.note)] });
          navigator.vibrate?.(10);
        }
      }, 420);
    }
    drag.current = st;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const st = drag.current;
    if (!st || st.pointerId !== e.pointerId) {
      updateCursor(e);
      return;
    }
    const { x, y } = local(e);
    const dx = x - st.x0;
    const dy = y - st.y0;
    if (!st.moved && Math.hypot(dx, dy) < MOVE_PX) return;
    if (!st.moved && st.longPress) clearTimeout(st.longPress);
    st.moved = true;
    const t = st.target;
    const dRaw = dx / view.pxPerBeat;
    switch (st.mode) {
      case "pan":
        setScrollX(st.scroll0 - dx);
        setScrollY(st.scrollY0 - dy);
        return;
      case "loop": {
        const a = snapBeat(st.beat0, view.snap === "off" ? "beat" : view.snap, e.altKey);
        const b = snapBeat(xToBeat(x, view), view.snap === "off" ? "beat" : view.snap, e.altKey);
        setDragPreview({ loop: { a, b } });
        return;
      }
      case "clip-move":
      case "clip-left":
      case "clip-right": {
        if (t.type !== "clip") return;
        const clip = trackById(song, t.track)?.clips?.[t.clip];
        if (!clip) return;
        const start = clipStart0(clip);
        if (st.mode === "clip-move") {
          const to = Math.max(0, snap(start + dRaw, e));
          setDragPreview({ clip: { track: t.track, clip: t.clip, dBeats: to - start, mode: "move", dLen: 0 } });
        } else if (st.mode === "clip-left") {
          const to = snap(start + dRaw, e);
          setDragPreview({ clip: { track: t.track, clip: t.clip, dBeats: to - start, mode: "left", dLen: 0 } });
        } else {
          const len = passBeats(clip, clipBaseLength(song, clip)) * (clip.repeat ?? 1);
          const end = snap(start + len + dRaw, e);
          setDragPreview({ clip: { track: t.track, clip: t.clip, dBeats: 0, mode: stretchMode ? "stretch" : "right", dLen: (end - start - len) / (clip.repeat ?? 1) } });
        }
        return;
      }
      case "hit": {
        if (t.type !== "hit") return;
        if (!st.axis) st.axis = Math.abs(dy) > Math.abs(dx) ? "y" : "x";
        if (st.axis === "y") {
          const v = clamp((st.vel0 ?? 1) - dy / 70, 0.05, 1);
          setDragPreview({ hit: { track: t.track, lane: t.hit.lane, fromBeat: t.hit.beat0, toBeat: t.hit.beat0, velocity: v } });
        } else {
          const to = t.hit.origin === "free" || free(e) ? Math.max(0, t.hit.beat0 + dRaw) : snapBeat(t.hit.beat0 + dRaw, "16th");
          setDragPreview({ hit: { track: t.track, lane: t.hit.lane, fromBeat: t.hit.beat0, toBeat: to } });
        }
        return;
      }
      case "note":
      case "note-right": {
        if (t.type !== "note") return;
        const row = rowAt(rows, y - RULER_H + scrollY);
        const pitch = row && row.trackId === t.track ? pitchAt(row, y - RULER_H + scrollY) : t.pitch;
        const key = noteKey(t.note);
        if (st.mode === "note") {
          const to = snap(t.note.beat - 1 + dRaw, e);
          setDragPreview({ note: { track: t.track, key, dBeats: to - (t.note.beat - 1), dPitch: pitch - t.note.pitch, dLen: 0 } });
        } else {
          const end = snap(t.note.beat - 1 + t.note.length + dRaw, e);
          setDragPreview({ note: { track: t.track, key, dBeats: 0, dPitch: 0, dLen: end - (t.note.beat - 1 + t.note.length) } });
        }
        return;
      }
      case "point": {
        if (t.type !== "point") return;
        const row = rows.find((r) => r.trackId === t.track);
        const a = row?.autos.find((x) => x.target === t.target);
        if (!a) return;
        const beat = snap(xToBeat(x, view), e);
        const value = yToValue(y - RULER_H + scrollY, t.target, a.y, a.h);
        setDragPreview({ point: { target: t.target, index: t.index, bar: beat / BEATS_PER_BAR + 1, value } });
        return;
      }
      case "marquee":
        setDragPreview({ marquee: { x0: st.x0, y0: st.y0 - RULER_H + scrollY, x1: x, y1: y - RULER_H + scrollY } });
        return;
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const st = drag.current;
    if (!st || st.pointerId !== e.pointerId) return;
    if (st.longPress) clearTimeout(st.longPress);
    drag.current = null;
    const pv = dragPreview;
    setDragPreview(null);
    const t = st.target;
    if (!st.moved) {
      click(st, e);
      return;
    }
    switch (st.mode) {
      case "loop":
        if (pv?.loop) {
          const a = Math.min(pv.loop.a, pv.loop.b);
          const b = Math.max(pv.loop.a, pv.loop.b);
          editor.setLoop(b - a >= 0.25 ? { startBeat: a, endBeat: b } : null);
        }
        return;
      case "clip-move":
        if (t.type === "clip" && pv?.clip) editor.apply(moveClip(song, t.track, t.clip, pv.clip.dBeats));
        return;
      case "clip-left":
        if (t.type === "clip" && pv?.clip) editor.apply(resizeClipLeft(song, t.track, t.clip, pv.clip.dBeats, SNAP_BEATS[view.snap]));
        return;
      case "clip-right":
        if (t.type === "clip" && pv?.clip) {
          const clip = trackById(song, t.track)?.clips?.[t.clip];
          editor.apply(resizeClipRight(song, t.track, t.clip, pv.clip.dLen * (clip?.repeat ?? 1), stretchMode, SNAP_BEATS[view.snap]));
        }
        return;
      case "hit":
        if (t.type === "hit" && pv?.hit) {
          const items = selection.kind === "hits" && selection.track === t.track && selection.items.some((h) => h.lane === t.hit.lane && Math.abs(h.beat0 - t.hit.beat0) < 1e-6) ? selection.items : [hitSel(t.hit)];
          if (pv.hit.velocity != null) editor.apply(setHitVelocity(song, t.track, items, pv.hit.velocity));
          else editor.apply(moveHits(song, t.track, items, pv.hit.toBeat - t.hit.beat0, t.hit.origin === "free" || free(e)));
        }
        return;
      case "note":
      case "note-right":
        if (t.type === "note" && pv?.note) {
          const items = selection.kind === "notes" && selection.track === t.track && selection.items.some((n) => noteKey({ ...n }) === noteKey(t.note)) ? selection.items : [noteSelOf(t.note)];
          editor.apply(changeNotes(song, t.track, items, { beats: pv.note.dBeats, pitch: pv.note.dPitch, length: pv.note.dLen }));
        }
        return;
      case "point":
        if (t.type === "point" && pv?.point) editor.apply(movePoint(song, t.target, t.index, pv.point.bar, pv.point.value));
        return;
      case "marquee":
        if (pv?.marquee) marqueeSelect(pv.marquee, st);
        return;
    }
  };

  const marqueeSelect = (m: NonNullable<DragPreview["marquee"]>, st: DragState) => {
    const b0 = xToBeat(Math.min(m.x0, m.x1), view);
    const b1 = xToBeat(Math.max(m.x0, m.x1), view);
    const yA = Math.min(m.y0, m.y1);
    const yB = Math.max(m.y0, m.y1);
    const row = rowAt(rows, m.y0);
    if (!row) return;
    const track = trackById(song, row.trackId);
    if (!track) return;
    let sel: Selection = { kind: "range", track: row.trackId, lane: null, startBeat: b0, endBeat: b1 };
    if (row.kind === "pattern" && row.expanded) {
      const lanes = row.lanes.filter((l) => l.y + l.h > yA && l.y < yB).map((l) => l.name);
      const items = patternHits(song, track)
        .filter((h) => lanes.includes(h.lane) && h.beat0 >= b0 - 1e-6 && h.beat0 < b1)
        .map(hitSel);
      sel = items.length ? { kind: "hits", track: row.trackId, items } : { kind: "range", track: row.trackId, lane: lanes.length === 1 ? lanes[0] : null, startBeat: b0, endBeat: b1 };
    } else if (row.kind === "instrument") {
      const pA = pitchAt(row, yB);
      const pB = pitchAt(row, yA);
      const items = notesIn(song, row.trackId, b0, b1, pA, pB);
      if (items.length) sel = { kind: "notes", track: row.trackId, items };
    } else if (row.kind === "clips") {
      const items: { clip: number; instance: number | null }[] = [];
      track.clips?.forEach((c, ci) => {
        const len = passBeats(c, clipBaseLength(song, c));
        for (let r = 0; r < (c.repeat ?? 1); r++) {
          const s0 = clipStart0(c) + r * len;
          if (s0 < b1 && s0 + len > b0) items.push({ clip: ci, instance: r + 1 });
        }
      });
      if (items.length) sel = { kind: "clips", track: row.trackId, items };
    }
    void st;
    editor.select(sel);
  };

  const click = (st: DragState, e: React.PointerEvent) => {
    const t = st.target;
    const now = performance.now();
    const dbl = now - lastClick.current.t < 320 && Math.hypot(st.x0 - lastClick.current.x, st.y0 - lastClick.current.y) < 6;
    lastClick.current = { t: now, x: st.x0, y: st.y0 };
    switch (t.type) {
      case "ruler":
        editor.seekBeat(Math.max(0, snapBeat(t.beat, "beat", e.altKey)));
        return;
      case "clip":
        if (dbl) editor.select({ kind: "clips", track: t.track, items: [{ clip: t.clip, instance: null }] });
        else {
          const cur = selection.kind === "clips" && selection.track === t.track ? selection.items : [];
          const it = { clip: t.clip, instance: t.instance };
          const has = cur.some((c) => c.clip === it.clip && c.instance === it.instance);
          editor.select(st.shift ? { kind: "clips", track: t.track, items: has ? cur.filter((c) => !(c.clip === it.clip && c.instance === it.instance)) : [...cur, it] } : { kind: "clips", track: t.track, items: [it] });
        }
        return;
      case "hit":
        if (st.shift || st.touch) selectHit(t.track, hitSel(t.hit), true);
        else {
          // click a hit = take it out (Ctrl+Z brings it back)
          editor.apply(deleteHits(song, t.track, [hitSel(t.hit)]));
        }
        return;
      case "cell": {
        const isFree = free(e);
        const beat = isFree ? t.beat : (t.bar - 1) * BEATS_PER_BAR + (t.step * BEATS_PER_BAR) / (trackById(song, t.track)?.pattern?.steps_per_bar ?? 16);
        if (editor.apply(addHit(song, t.track, t.lane, beat, isFree ? null : { bar: t.bar, step: t.step }, e.ctrlKey || e.metaKey ? 0.6 : 1))) {
          const lane = trackById(song, t.track)?.pattern?.lanes[t.lane];
          if (lane) onAudition(lane.sample, e.ctrlKey || e.metaKey ? 0.6 : 1);
        }
        return;
      }
      case "note": {
        const it = noteSelOf(t.note);
        const cur = selection.kind === "notes" && selection.track === t.track ? selection.items : [];
        editor.select(st.shift ? { kind: "notes", track: t.track, items: [...cur, it] } : { kind: "notes", track: t.track, items: [it] });
        return;
      }
      case "roll":
        if (dbl) {
          const step = SNAP_BEATS[view.snap] || 0.25;
          editor.apply(addNote(song, t.track, Math.max(0, floorTo(t.beat, step)), t.pitch, Math.max(step, 0.5)));
        } else editor.select({ kind: "none" });
        return;
      case "point":
        editor.select({ kind: "points", track: t.track, target: t.target, items: [t.index] });
        return;
      case "auto":
        if (dbl) {
          const beat = snap(t.beat, e);
          editor.apply(addPoint(song, t.target, beat / BEATS_PER_BAR + 1, yToValue(st.y0 - RULER_H + scrollY, t.target, t.y, t.h)));
        } else editor.select({ kind: "none" });
        return;
      case "row":
        editor.select({ kind: "none" });
        return;
      default:
        editor.select({ kind: "none" });
    }
  };

  const updateCursor = (e: React.PointerEvent) => {
    const c = canvasRef.current;
    if (!c) return;
    const { x, y } = local(e);
    const t = hitTest(song, view, rows, x, y, scrollY);
    let cur = "default";
    if (t.type === "clip") cur = t.edge ? (stretchMode && t.edge === "right" ? "col-resize" : "ew-resize") : "grab";
    else if (t.type === "hit" || t.type === "point") cur = "pointer";
    else if (t.type === "cell") cur = "copy";
    else if (t.type === "note") cur = t.edge ? "ew-resize" : "grab";
    else if (t.type === "ruler") cur = "text";
    if (c.style.cursor !== cur) c.style.cursor = cur;
  };

  const onWheel = (e: React.WheelEvent) => {
    const { x, y } = local(e);
    if (e.ctrlKey || e.metaKey) {
      editor.setView(zoomAt(view, Math.exp(-e.deltaY * 0.002), x), size.w);
      return;
    }
    if (selection.kind === "hits" && !e.shiftKey) {
      const t = hitTest(song, view, rows, x, y, scrollY);
      if (t.type === "hit" && selection.items.some((h) => h.lane === t.hit.lane && Math.abs(h.beat0 - t.hit.beat0) < 1e-6)) {
        editor.apply(setHitVelocity(song, t.track, selection.items, t.hit.velocity - Math.sign(e.deltaY) * 0.06));
        return;
      }
    }
    const dx = e.shiftKey ? e.deltaY : e.deltaX;
    const dy = e.shiftKey ? 0 : e.deltaY;
    if (dx) setScrollX(view.scrollX + dx);
    if (dy) setScrollY(scrollY + dy);
  };

  // non-passive wheel so ctrl+wheel zoom doesn't zoom the page
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  }, []);

  return (
    <div className="tl-wrap" ref={wrapRef} data-testid="timeline">
      <canvas
        ref={canvasRef}
        className="tl-canvas"
        style={{ width: size.w, height: size.h }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          drag.current = null;
          setDragPreview(null);
        }}
        onWheel={onWheel}
        onContextMenu={(e) => e.preventDefault()}
        aria-label="Arreglo: línea de tiempo"
        role="application"
      />
      <div className="tl-playhead" ref={headRef} aria-hidden="true" />
    </div>
  );
}


function floorTo(b: number, step: number): number {
  return Math.floor(b / step + 1e-9) * step;
}
