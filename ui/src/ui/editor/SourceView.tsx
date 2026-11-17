import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useApp } from "../../state/react";
import { colorFor, peaksOf, readTheme } from "../../editor/draw";
import type { Op } from "../../editor/ops";
import { round6, tidyClip } from "../../editor/ops";
import { useEditor, useEditorStore } from "../../editor/react";
import { BEATS_PER_BAR, trackKind, type ChopJson } from "../../editor/song";
import * as I from "../icons";
import { anchorFor, beatTime, chopSpan, timeBeat } from "../../editor/sourcegrid";

// --------------------------------------------------------------- view

type Drag = { kind: "new"; a: number; b: number } | { kind: "edge"; chop: string; side: "start" | "end"; a: number; b: number };

export function SourceView({ sourceId, onClose }: { sourceId: string; onClose: () => void }) {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const analyses = useApp((s) => s.analyses);
  const themeName = useApp((s) => s.theme);
  const a = analyses[sourceId];
  const chops = (song.chops ?? []).filter((c) => c.source === sourceId);
  const [stem, setStem] = useState<string>(chops[0]?.stem ?? "mix");
  const [view, setView] = useState<{ t0: number; t1: number } | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [region, setRegion] = useState<[number, number] | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [w, setW] = useState(900);
  const H = 220;

  const audioKey = `${sourceId}:${stem}`;
  const [loaded, setLoaded] = useState<{ key: string; buffer: AudioBuffer | null; err: string | null } | null>(null);
  const buffer = loaded?.key === audioKey ? loaded.buffer : null;
  const err = loaded?.key === audioKey ? loaded.err : null;
  useEffect(() => {
    let alive = true;
    void editor.app.client
      .tool<{ source_piece: string | null; pieces: { id: string; path: string }[] }>("song_pieces", { dry: false, wet: false, tracks: [], source: sourceId, stem })
      .then(async (r) => {
        const path = r.pieces.find((p) => p.id === r.source_piece)?.path;
        if (!path) throw new Error("sin audio");
        const b = await editor.engine.loadBuffer(path);
        if (alive) setLoaded({ key: audioKey, buffer: b, err: null });
      })
      .catch((e) => alive && setLoaded({ key: audioKey, buffer: null, err: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
  }, [editor, sourceId, stem, audioKey]);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // initial window: around the existing chops (or the whole file)
  const initial = (() => {
    if (!a) return { t0: 0, t1: 1 };
    const spans = chops.map((c) => chopSpan(a, c));
    if (!spans.length) return { t0: 0, t1: a.duration_s };
    const t0 = beatTime(a.beats_s, Math.min(...spans.map((s) => s[0])) - 8);
    const t1 = beatTime(a.beats_s, Math.max(...spans.map((s) => s[1])) + 8);
    return { t0: Math.max(0, t0), t1: Math.min(a.duration_s, t1) };
  })();
  const v = view ?? initial;
  const stop = useRef<(() => void) | null>(null);
  const tx = (t: number) => ((t - v.t0) / (v.t1 - v.t0)) * w;
  const xt = (x: number) => v.t0 + (x / w) * (v.t1 - v.t0);
  const snapPos = (pos: number, free: boolean) => (free ? pos : Math.round(pos));

  useEffect(() => {
    const c = canvas.current;
    if (!c || !a || !wrap.current) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(w * dpr);
    c.height = Math.round(H * dpr);
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const th = readTheme(wrap.current);
    g.fillStyle = th.row;
    g.fillRect(0, 0, w, H);
    // reliable / doubtful regions
    for (const r of a.reliability?.regions ?? []) {
      g.fillStyle = r.reliable ? "rgba(67,197,158,0.07)" : "rgba(255,107,107,0.10)";
      g.fillRect(tx(r.start_s), 0, tx(r.end_s) - tx(r.start_s), H);
    }
    // beat grid + downbeats with bar numbers
    const pxPerBeat = w / Math.max(1, timeBeat(a.beats_s, v.t1) - timeBeat(a.beats_s, v.t0));
    if (pxPerBeat > 5)
      for (const t of a.beats_s) {
        if (t < v.t0 || t > v.t1) continue;
        g.fillStyle = th.line;
        g.fillRect(Math.round(tx(t)), 18, 1, H - 18);
      }
    g.font = "600 10px 'JetBrains Mono Variable', ui-monospace, monospace";
    g.textBaseline = "top";
    a.downbeats_s.forEach((t, i) => {
      if (t < v.t0 || t > v.t1) return;
      const x = Math.round(tx(t));
      g.fillStyle = th.bar;
      g.fillRect(x, 0, 1.5, H);
      if (pxPerBeat * 4 > 26 || i % 4 === 0) {
        g.fillStyle = th.ink2;
        g.fillText(String(i + 1), x + 3, 3);
      }
    });
    // waveform
    if (buffer) {
      const peaks = peaksOf(buffer);
      let max = 0;
      for (let i = 0; i < peaks.length; i++) max = Math.max(max, peaks[i]);
      const hop = 128 / buffer.sampleRate;
      const mid = 18 + (H - 18) / 2;
      g.fillStyle = th.wave;
      for (let x = 0; x < w; x++) {
        const fa = Math.floor(xt(x) / hop);
        const fb = Math.max(fa + 1, Math.ceil(xt(x + 1) / hop));
        let m = 0;
        for (let i = Math.max(0, fa); i < Math.min(peaks.length, fb); i++) m = Math.max(m, peaks[i]);
        const hh = (m / (max || 1)) * ((H - 26) / 2);
        g.fillRect(x, mid - hh, 1, Math.max(1, hh * 2));
      }
    }
    // chops as regions
    for (const ch of chops) {
      let [s, e] = chopSpan(a, ch);
      if (drag?.kind === "edge" && drag.chop === ch.id) [s, e] = drag.side === "start" ? [drag.b, e] : [s, drag.b];
      const x0 = tx(beatTime(a.beats_s, s));
      const x1 = tx(beatTime(a.beats_s, e));
      const col = colorFor(ch.id, th);
      g.fillStyle = col + "33";
      g.fillRect(x0, 18, x1 - x0, H - 18);
      g.fillStyle = col;
      g.fillRect(x0, 18, 2, H - 18);
      g.fillRect(x1 - 2, 18, 2, H - 18);
      g.font = "600 11px 'Inter Variable', system-ui, sans-serif";
      g.fillText(`${ch.id} · ${ch.length_beats} beats`, x0 + 6, 22);
    }
    const sel = drag?.kind === "new" ? [Math.min(drag.a, drag.b), Math.max(drag.a, drag.b)] : region;
    if (sel) {
      const x0 = tx(beatTime(a.beats_s, sel[0]));
      const x1 = tx(beatTime(a.beats_s, sel[1]));
      g.fillStyle = "rgba(255,211,107,0.18)";
      g.fillRect(x0, 18, x1 - x0, H - 18);
      g.strokeStyle = th.sel;
      g.strokeRect(x0 + 0.5, 18.5, x1 - x0 - 1, H - 19);
    }
  }, [a, buffer, w, v.t0, v.t1, chops, drag, region, themeName]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!a) return null;

  const posAt = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return timeBeat(a.beats_s, xt(e.clientX - r.left));
  };
  const onDown = (e: React.PointerEvent) => {
    const pos = posAt(e);
    const r = canvas.current!.getBoundingClientRect();
    const x = e.clientX - r.left;
    for (const ch of chops) {
      const [s, en] = chopSpan(a, ch);
      if (Math.abs(tx(beatTime(a.beats_s, s)) - x) < 7) return start({ kind: "edge", chop: ch.id, side: "start", a: s, b: s }, e);
      if (Math.abs(tx(beatTime(a.beats_s, en)) - x) < 7) return start({ kind: "edge", chop: ch.id, side: "end", a: en, b: en }, e);
    }
    start({ kind: "new", a: snapPos(pos, e.altKey), b: snapPos(pos, e.altKey) }, e);
  };
  const start = (d: Drag, e: React.PointerEvent) => {
    setDrag(d);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    setDrag({ ...drag, b: snapPos(posAt(e), e.altKey) });
  };
  const onUp = () => {
    const d = drag;
    setDrag(null);
    if (!d) return;
    if (d.kind === "new") {
      const lo = Math.min(d.a, d.b);
      const hi = Math.max(d.a, d.b);
      if (hi - lo >= 0.25) setRegion([lo, hi]);
      else {
        // click: play from here
        setRegion(null);
        audition(lo, lo + 8);
      }
      return;
    }
    const ch = chops.find((c) => c.id === d.chop);
    if (!ch || Math.abs(d.b - d.a) < 1e-6) return;
    const [s, e] = chopSpan(a, ch);
    const ns = d.side === "start" ? Math.min(d.b, e - 0.25) : s;
    const ne = d.side === "end" ? Math.max(d.b, s + 0.25) : e;
    const anchor = anchorFor(a, ns);
    const ops: Op[] = [];
    const base = `chops.${ch.id}`;
    if (anchor.start_downbeat !== ch.start_downbeat) ops.push({ op: "set", path: `${base}.start_downbeat`, value: anchor.start_downbeat, expect: ch.start_downbeat });
    if (Math.abs(anchor.start_offset_beats - (ch.start_offset_beats ?? 0)) > 1e-6) ops.push({ op: "set", path: `${base}.start_offset_beats`, value: anchor.start_offset_beats, expect: ch.start_offset_beats ?? null });
    if (Math.abs(ne - ns - ch.length_beats) > 1e-6) ops.push({ op: "set", path: `${base}.length_beats`, value: round6(ne - ns), expect: ch.length_beats });
    editor.edit(ops, `ajustar el chop ${ch.id} en la fuente`);
  };

  const audition = (p0: number, p1: number) => {
    if (!buffer) return;
    const t0 = beatTime(a.beats_s, p0);
    const t1 = beatTime(a.beats_s, p1);
    stop.current?.();
    stop.current = editor.engine.playOneShot(buffer, { offset: Math.max(0, t0), duration: Math.max(0.05, t1 - t0) });
  };
  const create = () => {
    if (!region) return;
    const ids = new Set((song.chops ?? []).map((c) => c.id));
    let n = 1;
    while (ids.has(`c${n}`)) n++;
    const id = `c${n}`;
    const len = round6(Math.min(128, region[1] - region[0]));
    const chop: ChopJson = { id, source: sourceId, stem, ...anchorFor(a, region[0]), length_beats: len, warp: { mode: "resample", pitch_semitones: 0 } };
    if (!chop.start_offset_beats) delete chop.start_offset_beats;
    const ops: Op[] = [{ op: "insert", path: "chops", value: chop }];
    // drop it on the focused (or first) clip track, at the playhead's bar
    const target = (song.tracks ?? []).find((t) => t.id === editor.state.focusTrack && trackKind(t) === "clips") ?? (song.tracks ?? []).find((t) => trackKind(t) === "clips");
    let where = "";
    if (target) {
      const bar = Math.floor(editor.playheadBeat() / BEATS_PER_BAR) + 1;
      const fits = (bar - 1) * BEATS_PER_BAR + len <= song.meta.length_bars * BEATS_PER_BAR;
      const at = fits ? bar : 1;
      ops.push({ op: "insert", path: `tracks.${target.id}.clips`, value: tidyClip({ chop: id, at_bar: at }) });
      where = ` y lo puse en ${target.id}, compás ${at}`;
    }
    if (editor.edit(ops, `crear el chop ${id}`)) {
      editor.app.toast(`Chop ${id} creado${where}.`, "ok");
      setRegion(null);
    }
  };
  const zoom = (f: number) => {
    const mid = (v.t0 + v.t1) / 2;
    const half = Math.min(a.duration_s, Math.max(1, ((v.t1 - v.t0) / 2) * f));
    setView({ t0: Math.max(0, mid - half), t1: Math.min(a.duration_s, mid + half) });
  };
  const pan = (dt: number) => {
    const span = v.t1 - v.t0;
    const t0 = Math.max(0, Math.min(a.duration_s - span, v.t0 + dt));
    setView({ t0, t1: t0 + span });
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog source-view" role="dialog" aria-modal="true" aria-label={`Fuente ${sourceId}`} onClick={(e) => e.stopPropagation()} data-testid="source-view">
        <div className="sv-head">
          <h2>Fuente {sourceId}</h2>
          <span className="muted small-text">
            {a.bpm_global ? `${a.bpm_global.toFixed(1)} BPM · ` : ""}
            {a.downbeats_s.length} compases{a.reliability ? ` · grilla confiable en ${Math.round(a.reliability.confidence * 100)}%` : ""}
          </span>
          <span className="header-spacer" />
          <label className="ed-snap">
            <span className="sr-only">Stem</span>
            <select value={stem} onChange={(e) => setStem(e.target.value)}>
              {["mix", ...Object.keys(a.stems ?? {})].map((s) => (
                <option key={s} value={s}>
                  {s === "mix" ? "Mezcla original" : `Stem ${s}`}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Cerrar">
            <I.X />
          </button>
        </div>
        <div className="sv-canvas" ref={wrap} onWheel={(e) => (e.ctrlKey ? zoom(e.deltaY > 0 ? 1.25 : 0.8) : pan(((e.deltaX || e.deltaY) / w) * (v.t1 - v.t0)))}>
          <canvas ref={canvas} style={{ width: w, height: H }} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} aria-label="Forma de onda de la fuente con su grilla" />
          {!buffer && !err && <div className="sv-loading muted">Preparando el audio de la fuente…</div>}
          {err && <div className="sv-loading">No pude cargar la fuente: {err}</div>}
        </div>
        <div className="insp-row">
          <span className="seg">
            <button type="button" onClick={() => zoom(0.66)} aria-label="Acercar">
              +
            </button>
            <button type="button" onClick={() => zoom(1.5)} aria-label="Alejar">
              −
            </button>
          </span>
          <span className="muted small-text">Arrastrá para marcar una región (se pega a los tiempos del disco; Alt = libre) · arrastrá los bordes de un chop para cambiarlo · clic para escuchar desde ahí.</span>
          <span className="header-spacer" />
          {region && (
            <>
              <span className="mono small-text">{round6(region[1] - region[0])} beats</span>
              <button type="button" className="btn small" onClick={() => audition(region[0], region[1])}>
                <I.Play size={14} /> Escuchar
              </button>
              <button type="button" className="btn small primary" onClick={create} data-testid="sv-create">
                Crear chop
              </button>
            </>
          )}
          {!region &&
            chops.map((c) => (
              <button key={c.id} type="button" className="btn small" onClick={() => audition(...chopSpan(a, c))}>
                <I.Play size={14} /> {c.id}
              </button>
            ))}
        </div>
      </div>
    </div>
  );
}
