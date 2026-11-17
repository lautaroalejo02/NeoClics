import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isTypingTarget, useLayout, useRaf } from "../../hooks/hooks";
import { useApp, useEngine, useStore } from "../../state/react";
import {
  copySelection,
  deleteClips,
  deleteHits,
  deleteNotes,
  deletePoints,
  duplicateClips,
  changeNotes,
  moveClip,
  moveHits,
  paste,
  setHitVelocity,
  setTrackField,
  splitAt,
  togglePassMute,
} from "../../editor/actions";
import { RULER_H } from "../../editor/draw";
import { useEditor, useEditorStore } from "../../editor/react";
import { selCount, selectionLabel } from "../../editor/selection";
import { BEATS_PER_BAR, clipBaseLength, clipStart0, passBeats, patternHits, trackById, trackKind, type SoundJson } from "../../editor/song";
import { SNAP_BEATS, SNAP_LABEL, fmtBarBeat, layoutRows, zoomAt, type Snap } from "../../editor/timeline";
import * as I from "../icons";
import { Inspector } from "./Inspector";
import { Timeline } from "./Timeline";
import { SourceView } from "./SourceView";
import { EDITOR_SHORTCUTS } from "../../editor/shortcuts";


export function EditorView({ onListen }: { onListen: () => void }) {
  const editor = useEditorStore();
  const store = useStore();
  const status = useEditor((s) => s.status);
  const error = useEditor((s) => s.error);
  const song = useEditor((s) => s.song);
  const layout = useLayout();
  const inspector = useEditor((s) => s.inspector);
  const source = useEditor((s) => s.source);
  const [help, setHelp] = useState(false);
  const askRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void editor.init();
  }, [editor]);

  const audition = useCallback(
    (sound: string | SoundJson, gain = 1) => {
      void editor.soundBuffer(typeof sound === "string" ? { sample: sound } : sound).then((b) => b && editor.engine.playOneShot(b, { gain }));
    },
    [editor],
  );

  useEditorKeys(() => setHelp(true), () => askRef.current?.focus());

  if (status === "loading" || status === "off") return <div className="card ed-empty">Cargando el arreglo…</div>;
  if (status === "error" || !song)
    return (
      <div className="card ed-empty" role="alert">
        <p>{error ?? "No pude cargar la canción."}</p>
        <button type="button" className="btn" onClick={() => void editor.init()}>
          Reintentar
        </button>{" "}
        <button type="button" className="btn ghost" onClick={onListen}>
          Ir a escuchar versiones
        </button>
      </div>
    );

  return (
    <section className={`editor ${layout}`} aria-label="Arreglo">
      <Toolbar onHelp={() => setHelp(true)} onAsk={() => askRef.current?.focus()} />
      <div className={`ed-body ${inspector ? "with-insp" : ""}`}>
        <div className="ed-main card">
          <TrackHeaders />
          <Timeline onAudition={audition} />
          <Overview />
        </div>
        {inspector && <Inspector askRef={askRef} onAudition={audition} narrow={layout === "narrow"} onClose={() => store.state && editor.setInspector(false)} />}
      </div>
      {help && <EditorHelp onClose={() => setHelp(false)} />}
      {source && <SourceView sourceId={source.id} onClose={() => editor.openSource(null)} />}
    </section>
  );
}

function Toolbar({ onHelp, onAsk }: { onHelp: () => void; onAsk: () => void }) {
  const editor = useEditorStore();
  const eng = useEngine();
  const view = useEditor((s) => s.view);
  const canUndo = useEditor((s) => s.canUndo);
  const canRedo = useEditor((s) => s.canRedo);
  const undoLabel = useEditor((s) => s.undoLabel);
  const redoLabel = useEditor((s) => s.redoLabel);
  const dirty = useEditor((s) => s.dirty);
  const changes = useEditor((s) => s.changes);
  const baseVersion = useEditor((s) => s.baseVersion);
  const saving = useEditor((s) => s.saving);
  const listen = useEditor((s) => s.listen);
  const final = useEditor((s) => s.final);
  const inFlight = useEditor((s) => s.inFlight);
  const rendering = useEditor((s) => s.rendering);
  const loop = useEditor((s) => s.loop);
  const stretch = useEditor((s) => s.stretchMode);
  const sel = useEditor((s) => s.selection);
  const inspector = useEditor((s) => s.inspector);
  const song = useEditor((s) => s.song)!;
  const posRef = useRef<HTMLSpanElement>(null);
  const [confirm, setConfirm] = useState(false);
  useRaf(() => {
    if (posRef.current) posRef.current.textContent = fmtBarBeat(editor.playheadBeat());
  });
  const playing = eng.playing && (eng.source === "editor" || listen === "final");
  return (
    <div className="ed-toolbar card" role="toolbar" aria-label="Herramientas del arreglo">
      <div className="ed-group">
        <button type="button" className="btn primary ed-play" onClick={() => editor.togglePlay()} aria-label={playing ? "Pausa" : "Play"} data-testid="ed-play" title="Play / pausa (Espacio)">
          {playing ? <I.Pause size={18} /> : <I.Play size={18} />}
        </button>
        <span className="ed-pos mono" ref={posRef} aria-label="Posición (compás.tiempo)">
          1.1
        </span>
        <button
          type="button"
          className="icon-btn"
          aria-pressed={!!loop}
          onClick={() => editor.setLoop(loop ? null : { startBeat: Math.floor(editor.playheadBeat() / 4) * 4, endBeat: Math.floor(editor.playheadBeat() / 4) * 4 + 16 })}
          title="Loop (L) · arrastrá sobre la regla"
          aria-label="Loop"
        >
          <I.Loop size={16} />
        </button>
      </div>
      <div className="ed-group">
        <label className="ed-snap" title="Imán de la grilla (Alt lo apaga mientras arrastrás)">
          <span className="sr-only">Grilla</span>
          <select value={view.snap} onChange={(e) => editor.setView({ snap: e.target.value as Snap })} data-testid="ed-snap">
            {(Object.keys(SNAP_BEATS) as Snap[]).map((s) => (
              <option key={s} value={s}>
                Grilla: {SNAP_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn small" aria-pressed={stretch} onClick={() => editor.setStretchMode(!stretch)} title="Estirar (E): el borde derecho cambia el largo sin cortar, sin cambiar el tono">
          Estirar
        </button>
        <span className="seg" role="group" aria-label="Zoom">
          <button type="button" className="btn small" onClick={() => editor.setView(zoomAt(view, 1 / 1.4, 200))} aria-label="Alejar">
            −
          </button>
          <button type="button" className="btn small" onClick={() => editor.setView(zoomAt(view, 1.4, 200))} aria-label="Acercar">
            +
          </button>
        </span>
      </div>
      <div className="ed-group">
        <button type="button" className="btn small" disabled={!canUndo} onClick={() => editor.undoEdit()} title={undoLabel ? `Deshacer: ${undoLabel} (Ctrl+Z)` : "Deshacer (Ctrl+Z)"} data-testid="ed-undo">
          <span aria-hidden="true">↶</span>
          <span className="lbl">Deshacer</span>
        </button>
        <button type="button" className="btn small" disabled={!canRedo} onClick={() => editor.redoEdit()} title={redoLabel ? `Rehacer: ${redoLabel} (Ctrl+Y)` : "Rehacer (Ctrl+Y)"} data-testid="ed-redo">
          <span aria-hidden="true">↷</span>
          <span className="lbl">Rehacer</span>
        </button>
      </div>
      <div className="ed-group ed-listen">
        <button
          type="button"
          className={`pill ${listen === "final" ? "final" : ""}`}
          onClick={() => void editor.toggleFinal()}
          title="F: escuchar el render real del estado actual (borrador, sin crear versión) o volver a la vista previa"
          data-testid="ed-listen"
        >
          <span className={`dot ${listen === "final" ? "ok" : rendering.length || final?.state === "rendering" ? "warn" : "info"}`} />
          {listen === "final"
            ? `Render final${final?.state === "stale" ? " (desactualizado)" : ""}`
            : final?.state === "rendering"
              ? "Renderizando…"
              : rendering.length
                ? `Vista previa · preparando ${rendering[0]}`
                : "Vista previa del editor"}
          <kbd className="hide-narrow">F</kbd>
        </button>
        {inFlight > 0 && <span className="muted small-text" aria-live="polite">guardando…</span>}
      </div>
      <span className="ed-spacer" />
      <div className="ed-group ed-save">
        {dirty ? (
          <span className="ed-dirty" title={changes.join("\n")} data-testid="ed-dirty">
            <span className="dot warn" /> {changes.length || "Cambios"} {changes.length === 1 ? "cambio" : "cambios"} sin guardar{baseVersion ? ` (vs ${baseVersion})` : ""}
          </span>
        ) : (
          <span className="muted small-text hide-narrow" data-testid="ed-clean">
            {baseVersion ? `Igual a ${baseVersion}` : "Sin versiones todavía"}
          </span>
        )}
        {dirty && baseVersion && (
          <>
            {confirm ? (
              <span className="ed-confirm">
                ¿Descartar?{" "}
                <button type="button" className="btn small" onClick={() => (editor.discard(), setConfirm(false))} data-testid="ed-discard-yes">
                  Sí, volver a {baseVersion}
                </button>
                <button type="button" className="btn small ghost" onClick={() => setConfirm(false)}>
                  No
                </button>
              </span>
            ) : (
              <button type="button" className="btn small ghost" onClick={() => setConfirm(true)} data-testid="ed-discard">
                Descartar
              </button>
            )}
          </>
        )}
        <button type="button" className="btn small primary" disabled={!dirty || !!saving} onClick={() => void editor.saveVersion()} data-testid="ed-save" title={dirty ? "Crear una versión con estos cambios (renderiza, con un resumen en castellano)" : "No hay cambios"}>
          {saving ? "Guardando…" : "Guardar versión"}
        </button>
      </div>
      <div className="ed-group">
        <button type="button" className="btn small" onClick={onAsk} disabled={selCount(sel) === 0} title={selCount(sel) ? `Pedile al agente algo sobre: ${selectionLabel(song, sel)} (P)` : "Seleccioná algo primero"} data-testid="ed-ask-btn">
          Pedile al agente
        </button>
        <button type="button" className="icon-btn" aria-pressed={inspector} onClick={() => editor.setInspector(!inspector)} title="Panel de detalles" aria-label="Panel de detalles">
          <I.Layers />
        </button>
        <button type="button" className="icon-btn hide-narrow" onClick={onHelp} aria-label="Atajos del editor (?)" title="Atajos (?)">
          <I.Keyboard />
        </button>
        {(song.sources ?? []).length > 0 && (
          <button type="button" className="btn small ghost" onClick={() => editor.openSource(song.sources![0].id)} title="Ver la fuente con su grilla y sus chops; marcar un chop nuevo" data-testid="ed-source">
            Fuente
          </button>
        )}
      </div>
    </div>
  );
}

const KIND_LABEL = { clips: "sample", pattern: "batería", instrument: "instrumento" } as const;
const STATE_LABEL: Record<string, [string, string]> = {
  exact: ["ok", "Suena exacto (con sus efectos)"],
  preview: ["info", "Vista previa: sin algunos efectos hasta que llegue su versión con efectos"],
  waiting: ["warn", "Preparando audio…"],
  silent: ["err", "Sin audio todavía"],
};

function TrackHeaders() {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const view = useEditor((s) => s.view);
  const scrollY = useEditor((s) => s.scrollY);
  const status = useEditor((s) => s.trackStatus);
  const rendering = useEditor((s) => s.rendering);
  const solo = useEditor((s) => s.solo);
  const sel = useEditor((s) => s.selection);
  const fitH = useEditor((s) => s.fitH);
  const { rows } = useMemo(() => layoutRows(song, view, fitH), [song, view, fitH]);
  return (
    <div className="ed-heads" aria-label="Tracks">
      <div className="ed-heads-ruler" style={{ height: RULER_H }}>
        <span className="muted">Tracks</span>
      </div>
      <div className="ed-heads-rows" style={{ transform: `translateY(${-scrollY}px)` }}>
        {rows.map((r) => {
          const t = trackById(song, r.trackId)!;
          const st = status[t.id];
          const [dot, tip] = STATE_LABEL[rendering.includes(t.id) ? "waiting" : (st?.state ?? "waiting")] ?? STATE_LABEL.waiting;
          const selected = sel.kind === "track" && sel.track === t.id;
          const autos = (song.automation ?? []).filter((a) => a.target.startsWith(`tracks.${t.id}.`)).length;
          return (
            <div key={t.id} className={`ed-head ${selected ? "sel" : ""} ${r.lanes.length ? "has-lanes" : ""}`} style={{ top: r.y, height: r.h }} data-testid={`ed-head-${t.id}`}>
              <div className="ed-head-top">
                <button type="button" className="ed-head-name" onClick={() => editor.select({ kind: "track", track: t.id })} title={`${t.id} · ${KIND_LABEL[trackKind(t)]}`}>
                  <span className={`dot ${dot}`} title={st?.stale ? "Las notas cambiaron: re-renderizando el instrumento" : tip} />
                  {t.id}
                </button>
                <button type="button" className="ed-ms" aria-pressed={!!t.mute} onClick={() => editor.apply(setTrackField(song, t.id, "mute", !t.mute, t.mute ? `activar ${t.id}` : `mutear ${t.id}`))} title="Mute (se guarda en la canción)" aria-label={`Mute ${t.id}`}>
                  M
                </button>
                <button type="button" className="ed-ms solo" aria-pressed={solo.includes(t.id)} onClick={() => editor.setSolo(t.id, !solo.includes(t.id))} title="Solo (solo para escuchar, no se guarda)" aria-label={`Solo ${t.id}`}>
                  S
                </button>
              </div>
              {r.h >= 44 && (
                <div className="ed-head-sub">
                  <span className="muted">{KIND_LABEL[trackKind(t)]}</span>
                  <span className="mono">{(t.gain_db ?? 0) > 0 ? "+" : ""}{(t.gain_db ?? 0).toFixed(1)} dB</span>
                  {trackKind(t) !== "clips" && (
                    <button type="button" className="ed-mini" onClick={() => editor.setView({ expanded: { ...view.expanded, [t.id]: !r.expanded } })} aria-expanded={r.expanded} title={r.expanded ? "Compactar" : "Expandir"}>
                      {r.expanded ? "▾" : "▸"}
                    </button>
                  )}
                  {autos > 0 && (
                    <button type="button" className="ed-mini" aria-pressed={!!view.showAuto[t.id]} onClick={() => editor.setView({ showAuto: { ...view.showAuto, [t.id]: !view.showAuto[t.id] } })} title="Mostrar automatización">
                      auto
                    </button>
                  )}
                </div>
              )}
              {r.lanes.map((l) => (
                <div key={l.name} className="ed-lane-label" style={{ top: l.y - r.y, height: l.h }}>
                  {l.name}
                </div>
              ))}
              {r.autos.map((a) => (
                <div key={a.target} className="ed-lane-label auto" style={{ top: a.y - r.y, height: a.h }}>
                  {a.label}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Thin overview of the whole song with the visible window (drag to scroll). */
function Overview() {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const view = useEditor((s) => s.view);
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const total = song.meta.length_bars * BEATS_PER_BAR;
  const visible = Math.min(1, w / (total * view.pxPerBeat));
  const left = Math.min(1 - visible, view.scrollX / (total * view.pxPerBeat));
  const onDown = (e: React.PointerEvent) => {
    const el = ref.current!;
    const r = el.getBoundingClientRect();
    const go = (cx: number) => editor.setView({ scrollX: Math.max(0, ((cx - r.left) / r.width - visible / 2) * total * view.pxPerBeat) }, w);
    go(e.clientX);
    const move = (ev: PointerEvent) => go(ev.clientX);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div className="ed-overview" ref={ref} onPointerDown={onDown} aria-label="Vista general" role="scrollbar" aria-valuenow={Math.round(left * 100)} aria-controls="timeline">
      {Array.from({ length: song.meta.length_bars }, (_, i) => (
        <span key={i} className="ed-ov-bar" style={{ left: `${(i / song.meta.length_bars) * 100}%` }} />
      ))}
      <span className="ed-ov-win" style={{ left: `${left * 100}%`, width: `${visible * 100}%` }} />
    </div>
  );
}

function EditorHelp({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Atajos del arreglo" onClick={(e) => e.stopPropagation()}>
        <h2>Atajos del arreglo</h2>
        <dl className="keys">
          {EDITOR_SHORTCUTS.map(([keys, what]) => (
            <div key={what} style={{ display: "contents" }}>
              <dt>
                {keys.map((k) => (
                  <kbd key={k}>{k}</kbd>
                ))}
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
        <p className="muted small-text">Todo lo que hacés acá cambia la canción de trabajo (song.yaml) al instante; una versión se crea solo con «Guardar versión».</p>
        <div style={{ textAlign: "right", marginTop: 12 }}>
          <button type="button" className="btn" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
}

/** Editor keyboard (only while the Arreglo view is shown). */
function useEditorKeys(openHelp: () => void, focusAsk: () => void): void {
  const editor = useEditorStore();
  const store = useStore();
  const theme = useApp((s) => s.theme);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      if (document.querySelector(".overlay")) return;
      const s = editor.state;
      const song = s.song;
      if (!song) return;
      const sel = s.selection;
      const ctrl = e.ctrlKey || e.metaKey;
      const k = e.key;
      const step = SNAP_BEATS[s.view.snap] || 0.25;
      const done = () => e.preventDefault();
      if (ctrl && (k === "z" || k === "Z")) {
        done();
        if (e.shiftKey) editor.redoEdit();
        else editor.undoEdit();
        return;
      }
      if (ctrl && (k === "y" || k === "Y")) {
        done();
        editor.redoEdit();
        return;
      }
      if (ctrl && (k === "d" || k === "D")) {
        done();
        if (sel.kind === "clips") editor.apply(duplicateClips(song, sel.track, [...new Set(sel.items.map((i) => i.clip))]));
        else if (sel.kind === "hits" || sel.kind === "notes") {
          const cb = copySelection(song, sel);
          const span = cb && "origin" in cb ? 4 : 0;
          if (cb) editor.apply(paste(song, cb, sel.track, cb.origin + span));
        }
        return;
      }
      if (ctrl && (k === "c" || k === "C")) {
        const cb = copySelection(song, sel);
        if (cb) {
          editor.clipboard = cb;
          store.toast("Copiado", "info", undefined, 1200);
          done();
        }
        return;
      }
      if (ctrl && (k === "v" || k === "V")) {
        const cb = editor.clipboard;
        const track = (sel.kind !== "none" && "track" in sel && sel.track) || s.focusTrack || cb?.track;
        if (cb && track) {
          const at = Math.floor(editor.playheadBeat() / step) * step;
          editor.apply(paste(song, cb, track, at));
          done();
        }
        return;
      }
      if (ctrl || e.altKey) return;
      switch (k) {
        case " ":
          done();
          editor.togglePlay();
          return;
        case "Escape":
          editor.select({ kind: "none" });
          return;
        case "?":
          openHelp();
          done();
          return;
        case "f":
        case "F":
          void editor.toggleFinal();
          done();
          return;
        case "e":
        case "E":
          editor.setStretchMode(!s.stretchMode);
          return;
        case "p":
        case "P":
          if (sel.kind !== "none") {
            done();
            if (!s.inspector) editor.setInspector(true);
            setTimeout(focusAsk, 30);
          }
          return;
        case "l":
        case "L": {
          const b = Math.floor(editor.playheadBeat() / 4) * 4;
          editor.setLoop(s.loop ? null : { startBeat: b, endBeat: b + 16 });
          return;
        }
        case "+":
        case "=":
          editor.setView(zoomAt(s.view, 1.4, 200));
          return;
        case "-":
          editor.setView(zoomAt(s.view, 1 / 1.4, 200));
          return;
        case "s":
        case "S": {
          // split the selected clip (or the clip under the playhead on the focused track)
          const beat = editor.playheadBeat();
          const track = sel.kind === "clips" ? sel.track : s.focusTrack;
          const t = track ? trackById(song, track) : undefined;
          if (!t?.clips) return;
          const ci = sel.kind === "clips" ? sel.items[0].clip : t.clips.findIndex((c) => clipStart0(c) < beat && clipStart0(c) + passBeats(c, clipBaseLength(song, c)) * (c.repeat ?? 1) > beat);
          if (ci >= 0) {
            done();
            if (!editor.apply(splitAt(song, t.id, ci, beat))) store.toast("Poné el cursor de reproducción adentro del clip para cortarlo", "info");
          }
          return;
        }
        case "Delete":
        case "Backspace":
          done();
          if (sel.kind === "clips") editor.apply(deleteClips(song, sel.track, sel.items));
          else if (sel.kind === "hits") editor.apply(deleteHits(song, sel.track, sel.items));
          else if (sel.kind === "notes") editor.apply(deleteNotes(song, sel.track, sel.items));
          else if (sel.kind === "points") editor.apply(deletePoints(song, sel.target, sel.items));
          return;
        case "0":
          if (sel.kind === "clips") editor.apply(togglePassMute(song, sel.track, sel.items));
          else if (sel.kind === "hits") editor.apply(deleteHits(song, sel.track, sel.items));
          else if (sel.kind === "track") {
            const t = trackById(song, sel.track);
            if (t) editor.apply(setTrackField(song, t.id, "mute", !t.mute, t.mute ? `activar ${t.id}` : `mutear ${t.id}`));
          }
          return;
        case "ArrowLeft":
        case "ArrowRight": {
          const d = (k === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? BEATS_PER_BAR : step);
          if (sel.kind === "clips") {
            done();
            for (const ci of [...new Set(sel.items.map((i) => i.clip))]) editor.apply(moveClip(editor.state.song!, sel.track, ci, d));
          } else if (sel.kind === "hits") {
            done();
            editor.apply(moveHits(song, sel.track, sel.items, d, false));
          } else if (sel.kind === "notes") {
            done();
            editor.apply(changeNotes(song, sel.track, sel.items, { beats: d }));
            editor.select({ ...sel, items: sel.items.map((n) => ({ ...n, beat: n.beat + d })) });
          } else {
            done();
            editor.seekBeat(Math.max(0, editor.playheadBeat() + d));
          }
          return;
        }
        case "ArrowUp":
        case "ArrowDown": {
          const up = k === "ArrowUp" ? 1 : -1;
          if (sel.kind === "notes") {
            done();
            const dp = up * (e.shiftKey ? 12 : 1);
            editor.apply(changeNotes(song, sel.track, sel.items, { pitch: dp }));
            editor.select({ ...sel, items: sel.items.map((n) => ({ ...n, pitch: n.pitch + dp })) });
          } else if (sel.kind === "hits") {
            done();
            const t = trackById(song, sel.track);
            const all = t ? patternHits(song, t) : [];
            const v = all.find((h) => h.lane === sel.items[0].lane && Math.abs(h.beat0 - sel.items[0].beat0) < 1e-6)?.velocity ?? 1;
            editor.apply(setHitVelocity(song, sel.track, sel.items, v + up * 0.11));
          }
          return;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor, store, openHelp, focusAsk, theme]);
}
