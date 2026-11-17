import { useEffect, useRef, useState } from "react";
import type { Analysis, ChopAdjust, ChopCandidate, ChopCandidateSet, GridPreview } from "../api/types";
import { computePeaks } from "../audio/signal";
import { decodeWav } from "../audio/wav";
import { globalCandidateId } from "../feedback/events";
import { useRaf } from "../hooks/hooks";
import { formatClock } from "../music/time";
import { useApp, useEngine, useStore } from "../state/react";
import { timeAgo } from "../state/derive";
import type { MuestraClient } from "../api/client";
import * as I from "./icons";

const ID_RE = /^[a-z][a-z0-9_-]{0,63}$/;

/** Decoded preview metadata for the mini waveform (small, cached). */
interface PreviewInfo {
  peaks: Float32Array;
  duration: number;
}
const previewCache = new Map<string, Promise<PreviewInfo>>();

function loadPreview(client: MuestraClient, path: string): Promise<PreviewInfo> {
  let p = previewCache.get(path);
  if (!p) {
    p = client.fetchFile(path).then((buf) => {
      const w = decodeWav(buf);
      return { peaks: computePeaks(w.channels, 220), duration: w.frames / w.sampleRate };
    });
    p.catch(() => previewCache.delete(path));
    previewCache.set(path, p);
  }
  return p;
}

export function ChopsPanel() {
  const sets = useApp((s) => s.chopSets);
  const [openSet, setOpenSet] = useState<string | null>(null);
  if (sets.length === 0) {
    return (
      <div>
        <GridPanel />
        <div className="empty-small" data-testid="chops-empty">
          Todavía no hay candidatos de chop. Cuando le pidas al agente algo como <em>«armame un loop con el riff del inicio»</em>, busca cortes alineados a los
          downbeats, renderiza previews y los vas a ver acá para elegir otro si el suyo no te convence.
        </div>
      </div>
    );
  }
  const current = openSet ?? sets[0].set_id;
  return (
    <div data-testid="chops">
      <GridPanel />
      {sets.length > 1 && (
        <div className="chips" role="tablist" aria-label="Sets de candidatos" style={{ marginBottom: 10 }}>
          {sets.map((s) => (
            <button key={s.set_id} type="button" role="tab" className="chip" aria-pressed={s.set_id === current} aria-selected={s.set_id === current} onClick={() => setOpenSet(s.set_id)}>
              {s.set_id} · {s.source_id}
            </button>
          ))}
        </div>
      )}
      {sets
        .filter((s) => s.set_id === current)
        .map((s) => (
          <CandidateSet key={s.set_id} set={s} />
        ))}
    </div>
  );
}

/** "Escuchar la grilla": the source's beat grid as clicks over the record
 *  (source.preview_grid), so the human can say if the "one" and the tempo are right. */
export function GridPanel() {
  const analyses = useApp((s) => s.analyses);
  const previews = useApp((s) => s.gridPreviews);
  const ids = [...new Set([...Object.keys(analyses), ...Object.keys(previews)])].sort();
  const shown = ids.filter((id) => analyses[id]?.grid || previews[id]?.previews?.length);
  if (shown.length === 0) return null;
  return (
    <div data-testid="grid-panel" style={{ marginBottom: 14 }}>
      {shown.map((id) => (
        <SourceGrid key={id} sourceId={id} />
      ))}
    </div>
  );
}

function SourceGrid({ sourceId }: { sourceId: string }) {
  const analysis = useApp((s) => s.analyses[sourceId]);
  const index = useApp((s) => s.gridPreviews[sourceId]);
  const grid = analysis?.grid ?? null;
  const bpm = analysis?.bpm_global ?? index?.bpm;
  const raw = grid?.raw?.bpm_global ?? index?.raw_bpm;
  const corrected = !!grid && (grid.tempo_factor !== 1 || grid.downbeat_shift_beats !== 0);
  return (
    <div className="justif" data-testid={`grid-${sourceId}`}>
      <span aria-hidden="true" style={{ color: grid?.ask_human ? "var(--warn)" : "var(--info)" }}>
        <I.Info />
      </span>
      <div style={{ flex: 1 }}>
        <div>
          <strong>Grilla de {sourceId}</strong>
          {bpm ? ` · ${Math.round(bpm * 10) / 10} BPM` : ""}
          {corrected && raw ? ` (el detector leyó ${Math.round(raw * 10) / 10})` : ""}
          {grid?.ask_human && (
            <span className="tag a" style={{ marginLeft: 8 }}>
              confirmala de oído
            </span>
          )}
        </div>
        {grid?.reason && <div className="hint">{grid.reason}</div>}
        {index?.previews?.length ? (
          <div className="fb-compose-row" style={{ flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            <span className="hint">Escuchar la grilla (click suave en cada negra, agudo en el uno):</span>
            {index.previews.map((p) => (
              <GridPreviewButton key={p.path} sourceId={sourceId} preview={p} />
            ))}
          </div>
        ) : (
          <div className="hint">Pedile al agente «haceme escuchar la grilla» para oír los clicks sobre el disco.</div>
        )}
      </div>
    </div>
  );
}

function GridPreviewButton({ sourceId, preview }: { sourceId: string; preview: GridPreview }) {
  const store = useStore();
  const eng = useEngine();
  const key = `grid:${sourceId}:${preview.path}`;
  const playing = eng.preview === key;
  const toggle = () => {
    if (playing) store.engine.stopPreview();
    else void store.engine.playPreview(key, preview.path);
  };
  return (
    <button type="button" className="btn small" onClick={toggle} aria-pressed={playing} title={`${preview.bpm} BPM · ${formatClock(preview.start_s)}–${formatClock(preview.end_s)}`} data-testid="grid-preview">
      {playing ? <I.Stop size={13} /> : <I.Play size={14} />} {preview.applied ? "Actual" : preview.label}
    </button>
  );
}

function localId(set: ChopCandidateSet, id: string | undefined | null): string | null {
  if (!id) return null;
  return id.startsWith(`${set.set_id}/`) ? id.slice(set.set_id.length + 1) : id;
}

function CandidateSet({ set }: { set: ChopCandidateSet }) {
  const store = useStore();
  const analyses = useApp((s) => s.analyses);
  const songChops = useApp((s) => s.songChops);
  const analysis = analyses[set.source_id];
  const pickId = localId(set, set.pick?.candidate_id);
  const chosenId = localId(set, set.chosen?.candidate_id);
  const [sel, setSel] = useState<string>(chosenId ?? pickId ?? set.candidates[0]?.id ?? "");
  const [adjust, setAdjust] = useState<Record<string, ChopAdjust>>({});
  const defaultChop = songChops.find((c) => c.source === set.source_id)?.id ?? "c1";
  const [chopEdit, setChopId] = useState<string | null>(null);
  const chopId = chopEdit ?? defaultChop;
  const [busy, setBusy] = useState(false);
  useEffect(() => () => store.engine.stopPreview(), [store]);
  const selCand = set.candidates.find((c) => c.id === sel);
  const adj = adjust[sel] ?? { start_beats: 0, length_beats: 0 };
  const validId = ID_RE.test(chopId);
  const q = set.query;

  return (
    <div>
      <div className="fb-compose-row">
        <span className="card-sub">
          {set.set_id} · fuente <strong>{set.source_id}</strong>
          {q.section ? ` · sección «${q.section}»` : ""}
          {q.stem ? ` · stem ${q.stem}` : ""}
          {q.length_beats ? ` · ${q.length_beats} beats` : ""} · {timeAgo(set.created_at)}
        </span>
        {set.chosen && (
          <span className={`tag ${set.chosen.mode === "manual" ? "a" : ""}`}>
            elegido: {localId(set, set.chosen.candidate_id)} ({set.chosen.mode === "manual" ? "vos" : "agente"})
          </span>
        )}
      </div>
      {set.pick && (
        <div className="justif" style={{ marginTop: 10 }}>
          <span aria-hidden="true" style={{ color: "var(--info)" }}>
            <I.Info />
          </span>
          <span>
            <strong>El agente eligió {localId(set, set.pick.candidate_id)}</strong>: {set.pick.justification}
          </span>
        </div>
      )}
      <SourceStrip set={set} analysis={analysis} sel={sel} onSelect={setSel} />
      <div className="cands">
        {set.candidates.map((c) => (
          <CandidateCard
            key={c.id}
            set={set}
            cand={c}
            selected={c.id === sel}
            picked={c.id === pickId}
            chosen={c.id === chosenId}
            adjust={adjust[c.id] ?? { start_beats: 0, length_beats: 0 }}
            onSelect={() => setSel(c.id)}
            onAdjust={(a) => setAdjust({ ...adjust, [c.id]: a })}
          />
        ))}
      </div>
      {selCand && (
        <form
          className="banner"
          style={{ marginTop: 12 }}
          onSubmit={async (e) => {
            e.preventDefault();
            if (!validId) return;
            setBusy(true);
            const ok = await store.chooseChop(set, selCand.id, chopId, adj.start_beats || adj.length_beats ? adj : null);
            setBusy(false);
            if (ok) store.engine.stopPreview();
          }}
        >
          <div className="grow">
            <strong>Usar {globalCandidateId(set.set_id, selCand.id)}</strong>
            <div className="hint">
              {adj.start_beats || adj.length_beats
                ? `Con ajuste: inicio ${signed(adj.start_beats)} beats, largo ${selCand.length_beats + adj.length_beats} beats.`
                : "Sin ajustes."}{" "}
              Pisa la elección del agente y queda anotado en el feedback.
            </div>
          </div>
          <label className="field">
            chop en song.yaml
            <input value={chopId} onChange={(e) => setChopId(e.target.value.trim())} aria-invalid={!validId} size={8} />
          </label>
          <button type="submit" className="btn primary" disabled={!validId || busy} data-testid="confirm-chop">
            {busy ? <span className="spinner" /> : <I.Check />} Confirmar
          </button>
        </form>
      )}
    </div>
  );
}

function SourceStrip({ set, analysis, sel, onSelect }: { set: ChopCandidateSet; analysis: Analysis | undefined; sel: string; onSelect: (id: string) => void }) {
  const maxEnd = Math.max(...set.candidates.map((c) => c.end_s));
  const dur = analysis?.duration_s ?? maxEnd * 1.08;
  const pct = (s: number) => `${(Math.max(0, Math.min(dur, s)) / dur) * 100}%`;
  return (
    <div>
      <div className="srcstrip" style={{ height: 24 + set.candidates.length * 21 }} aria-label={`Dónde está cada candidato en ${set.source_id}`} role="group">
        {analysis?.sections.map((s, i) => (
          <span key={i} className="sec" style={{ left: pct(s.start_s), width: `calc(${pct(s.end_s - s.start_s)})` }}>
            {s.label}
          </span>
        ))}
        {analysis?.downbeats_s.map((d, i) => <span key={i} className="db-tick" style={{ left: pct(d) }} />)}
        {set.candidates.map((c, i) => (
          <button
            key={c.id}
            type="button"
            className={`cand${c.id === sel ? " sel" : ""}`}
            style={{ left: pct(c.start_s), width: pct(c.end_s - c.start_s), top: 20 + i * 21 }}
            onClick={() => onSelect(c.id)}
            aria-label={`${c.id}: ${formatClock(c.start_s)} a ${formatClock(c.end_s)}`}
          >
            {c.id}
          </button>
        ))}
      </div>
      <div className="fb-compose-row hint">
        <span>0:00</span>
        <span>{analysis ? `${set.source_id} · ${formatClock(dur)} · ${analysis.bpm_global} BPM${analysis.tempo_variable ? " (tempo variable)" : ""}` : "Sin análisis de la fuente: muestro solo los candidatos."}</span>
        <span>{formatClock(dur)}</span>
      </div>
    </div>
  );
}

function CandidateCard(p: {
  set: ChopCandidateSet;
  cand: ChopCandidate;
  selected: boolean;
  picked: boolean;
  chosen: boolean;
  adjust: ChopAdjust;
  onSelect: () => void;
  onAdjust: (a: ChopAdjust) => void;
}) {
  const store = useStore();
  const eng = useEngine();
  const { cand, set, adjust } = p;
  const key = globalCandidateId(set.set_id, cand.id);
  const playing = eng.preview === key;
  const [info, setInfo] = useState<PreviewInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!cand.preview) return;
    let live = true;
    loadPreview(store.client, cand.preview).then(
      (i) => live && setInfo(i),
      (e: unknown) => live && setErr(String(e)),
    );
    return () => {
      live = false;
    };
  }, [cand.preview, store.client]);
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !info) return;
    const w = c.clientWidth || 200;
    const h = c.clientHeight || 46;
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    c.width = w * dpr;
    c.height = h * dpr;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = getComputedStyle(c).getPropertyValue("--wave").trim() || "#888";
    const n = info.peaks.length / 2;
    for (let x = 0; x < w; x += 2) {
      const b = Math.min(n - 1, Math.floor((x / w) * n));
      const mx = info.peaks[b * 2 + 1];
      const mn = info.peaks[b * 2];
      ctx.fillRect(x, h / 2 - mx * h * 0.45, 1.2, Math.max(1, (mx - mn) * h * 0.45));
    }
  }, [info]);

  const beatS = info ? info.duration / cand.length_beats : (cand.end_s - cand.start_s) / cand.length_beats;
  const regionStart = adjust.start_beats * beatS;
  const regionEnd = (adjust.start_beats + cand.length_beats + adjust.length_beats) * beatS;
  const dur = info?.duration ?? cand.length_beats * beatS;
  const outside = regionStart < -1e-6 || regionEnd > dur + 1e-6;
  const adjusted = adjust.start_beats !== 0 || adjust.length_beats !== 0;
  const region = { start: Math.max(0, regionStart), end: Math.min(dur, regionEnd) };

  useEffect(() => {
    // re-audition live when nudging while it plays
    if (playing && cand.preview) void store.engine.playPreview(key, cand.preview, region);
  }, [adjust.start_beats, adjust.length_beats]); // eslint-disable-line react-hooks/exhaustive-deps

  useRaf(() => {
    const pos = playing ? store.engine.getPreviewPosition() : null;
    if (phRef.current) {
      phRef.current.style.display = pos == null ? "none" : "block";
      if (pos != null) phRef.current.style.left = `${(pos / dur) * 100}%`;
    }
  }, playing);

  const toggle = () => {
    p.onSelect();
    if (playing) store.engine.stopPreview();
    else if (cand.preview) void store.engine.playPreview(key, cand.preview, region);
  };
  const score = cand.score?.value ?? null;
  const len = cand.length_beats + adjust.length_beats;
  // ids are global (csNNN/candN); the card names the candidate inside its set
  const name = localId(set, cand.id) ?? cand.id;

  return (
    <article className={`cand-card${p.selected ? " sel" : ""}`} onClick={p.onSelect} data-testid={`cand-${name}`} aria-label={`Candidato ${name}`}>
      <div className="cand-head">
        <strong className="mono">{name}</strong>
        {p.picked && <span className="tag info">el agente</span>}
        {p.chosen && <span className="tag ok">en uso</span>}
        <span className="header-spacer" />
        <span className="tag">{cand.stem}</span>
      </div>
      <div className="cand-wave">
        <canvas ref={canvasRef} aria-hidden="true" />
        {adjusted && info && <div className="region" style={{ left: `${(region.start / dur) * 100}%`, width: `${((region.end - region.start) / dur) * 100}%` }} />}
        <div className="ph" ref={phRef} style={{ display: "none" }} />
      </div>
      <div className="fb-compose-row">
        <button type="button" className="btn small" onClick={(e) => (e.stopPropagation(), toggle())} disabled={!cand.preview} aria-pressed={playing} data-testid={`preview-${name}`}>
          {playing ? <I.Stop size={13} /> : <I.Play size={14} />} {playing ? "Parar" : "Escuchar en loop"}
        </button>
        <span className="hint mono">
          {formatClock(cand.start_s)}–{formatClock(cand.end_s)}
          {cand.local_bpm ? ` · ${Math.round(cand.local_bpm)} BPM` : ""}
        </span>
      </div>
      {score != null && (
        <div title={`Puntaje ${score.toFixed(2)}`}>
          <div className="fb-compose-row hint">
            <span>puntaje</span>
            <span className="mono">{Math.round(score * 100)}</span>
          </div>
          <div className="score" aria-hidden="true">
            <i style={{ width: `${Math.max(2, Math.min(100, score * 100))}%` }} />
          </div>
        </div>
      )}
      {cand.score?.reasons && cand.score.reasons.length > 0 && (
        <ul className="reasons">
          {cand.score.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="nudge" onClick={(e) => e.stopPropagation()}>
        <span>Inicio</span>
        <Stepper value={adjust.start_beats} fmt={(v) => `${signed(v)} beats`} onChange={(v) => p.onAdjust({ ...adjust, start_beats: v })} min={-16} max={16} label={`Inicio de ${name}`} />
        <span>Largo</span>
        <Stepper value={len} fmt={(v) => `${v} beats`} onChange={(v) => p.onAdjust({ ...adjust, length_beats: v - cand.length_beats })} min={1} max={64} label={`Largo de ${name}`} />
      </div>
      {outside && <p className="hint" style={{ margin: 0, color: "var(--warn)" }}>El preview no cubre este ajuste: escuchás la parte que hay; el agente renderiza el corte completo al confirmar.</p>}
      {err && <p className="hint" style={{ margin: 0, color: "var(--err)" }}>No pude cargar el preview.</p>}
    </article>
  );
}

function Stepper({ value, onChange, min, max, fmt, label }: { value: number; onChange: (v: number) => void; min: number; max: number; fmt: (v: number) => string; label: string }) {
  return (
    <span className="stepper" role="group" aria-label={label}>
      <button type="button" onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min} aria-label={`${label}: un beat menos`}>
        −
      </button>
      <output aria-live="polite">{fmt(value)}</output>
      <button type="button" onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max} aria-label={`${label}: un beat más`}>
        +
      </button>
    </span>
  );
}

function signed(n: number): string {
  return `${n > 0 ? "+" : ""}${n}`;
}

