import { useEffect, useMemo, useRef, useState } from "react";
import type { CommentEvent } from "../api/types";
import { isMixerDefault } from "../audio/mixer";
import { useLayout, useRaf } from "../hooks/hooks";
import { formatBarBeat, formatClock } from "../music/time";
import { useApp, useEngine, useSideLabel, useStore } from "../state/react";
import { isPending, newestVersionTs } from "../state/derive";
import { timingOf } from "../state/store";
import { fmtBpm, fmtDb } from "../text/es";
import * as I from "./icons";
import { Waveform, type WaveMarker } from "./Waveform";


export function Player() {
  const store = useStore();
  const active = useApp((s) => s.active);
  const metas = useApp((s) => s.metas);
  const metrics = useApp((s) => s.metrics);
  const peaks = useApp((s) => s.peaks);
  const feedback = useApp((s) => s.feedback);
  const versions = useApp((s) => s.versions);
  const blind = useApp((s) => s.blind);
  const loopOn = useApp((s) => s.loopOn);
  const loopRegion = useApp((s) => s.loopRegion);
  const theme = useApp((s) => s.theme);
  const draft = useApp((s) => s.draft);
  const mixer = useApp((s) => s.mixer);
  const eng = useEngine();
  const label = useSideLabel();
  const meta = active ? metas[active] : undefined;
  const timing = timingOf(meta);
  const hidden = !!blind && !blind.picked;
  const relLoop = useMemo(() => store.relLoop(), [store, loopRegion, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const markers = useMemo<WaveMarker[]>(() => {
    if (!active || hidden) return [];
    const newest = newestVersionTs(metas, versions);
    const out: WaveMarker[] = feedback
      .filter((e): e is CommentEvent => e.type === "comment" && e.version === active && e.at_s != null)
      .map((e) => ({ kind: "comment", atS: e.at_s as number, label: e.text, pending: isPending(e, newest), event: e }));
    for (const a of metrics[active]?.alarms ?? []) {
      if (a.at_s != null) out.push({ kind: "alarm", atS: a.at_s, label: a.message, severity: a.severity });
    }
    return out;
  }, [active, hidden, feedback, metas, versions, metrics]);

  const wantStems = !isMixerDefault(mixer);
  const layout = useLayout();
  const sectionRef = useRef<HTMLElement>(null);
  const [offscreen, setOffscreen] = useState(false);
  // While typing (on-screen keyboard open) the pinned bar would eat a fifth of
  // the visible height: hide it until the field loses focus.
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    const isField = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !["range", "checkbox", "radio", "button"].includes((t as HTMLInputElement).type)));
    const on = (e: FocusEvent) => setTyping(isField(e.target));
    const off = () => setTyping(false);
    document.addEventListener("focusin", on);
    document.addEventListener("focusout", off);
    return () => {
      document.removeEventListener("focusin", on);
      document.removeEventListener("focusout", off);
    };
  }, []);
  useEffect(() => {
    // Watch the TRANSPORT row (play button), not the whole card: on a phone the
    // card's A/B bar can still be on screen after the play button scrolled
    // away behind the sticky header, and then there was no way to play.
    const el = sectionRef.current?.querySelector(".transport");
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => setOffscreen(e.intersectionRatio < 0.9), { threshold: [0, 0.9, 1], rootMargin: "-56px 0px 0px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    // M from the phone mini-bar: bring the comment box into view
    if (draft && offscreen) sectionRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="card player" aria-label="Reproductor" ref={sectionRef}>
      {layout === "narrow" && offscreen && !typing && <MiniBar />}
      <div className="player-top">
        <div className="now-version mono" data-testid="now-version">
          {label(active)}
        </div>
        <div className="now-meta">
          <span className="now-summary">
            {hidden ? "Modo ciego: escuchá sin saber cuál es cuál." : meta?.summary ?? meta?.request ?? ""}
          </span>
          {timing && meta && !hidden && (
            <span className="card-sub mono">
              {fmtBpm(meta.bpm)} BPM · {meta.key} · compases {meta.range_bars[0]}–{meta.range_bars[1] - 1}
            </span>
          )}
        </div>
        {wantStems && (
          <span className="stem-badge" role="status" data-testid="stems-badge">
            <I.Layers size={14} />
            {eng.listening === "stems" ? "escuchando stems sin master" : "cargando stems…"}
          </span>
        )}
      </div>
      <Waveform
        timing={timing}
        peaks={active ? peaks[active] : undefined}
        loop={relLoop}
        loopOn={loopOn}
        markers={markers}
        theme={theme}
        getPosition={() => store.engine.getPosition()}
        onSeek={(s) => store.seek(s)}
        onLoop={(rel) => store.setLoopRel(rel, true)}
        label={`Forma de onda de ${label(active)}`}
      />
      <Transport />
      {draft && <QuickComment />}
      <ABBar />
    </section>
  );
}

/** Phone: compact transport pinned to the bottom while the player is scrolled away. */
function MiniBar() {
  const store = useStore();
  const eng = useEngine();
  const a = useApp((s) => s.a);
  const b = useApp((s) => s.b);
  const active = useApp((s) => s.active);
  const label = useSideLabel();
  return (
    <div className="minibar" role="region" aria-label="Controles rápidos">
      <button type="button" className="play-btn" onClick={() => store.togglePlay()} aria-label={eng.playing ? "Pausa" : "Reproducir"}>
        {eng.playing ? <I.Pause size={18} /> : <I.Play size={18} />}
      </button>
      <TimeReadout />
      <span className="grow" />
      <button type="button" className="btn small" onClick={() => void store.flip()} disabled={!a || !b} aria-label="Cambiar A/B">
        <span className="mono">{label(active)}</span> <I.Swap size={14} />
      </button>
      <button type="button" className="btn small" onClick={() => store.openDraft()} aria-label="Comentar en este punto">
        <I.Comment size={15} />
      </button>
    </div>
  );
}

function TimeReadout() {
  const store = useStore();
  const active = useApp((s) => s.active);
  const metas = useApp((s) => s.metas);
  const timing = timingOf(active ? metas[active] : undefined);
  const barsRef = useRef<HTMLSpanElement>(null);
  const secsRef = useRef<HTMLSpanElement>(null);
  const last = useRef("");
  useRaf(() => {
    if (!timing) return;
    const pos = store.engine.getPosition();
    const bb = formatBarBeat(pos, timing);
    const secs = `${formatClock(pos)} / ${formatClock(timing.durationS)}`;
    const key = bb + secs;
    if (key === last.current) return;
    last.current = key;
    if (barsRef.current) barsRef.current.textContent = bb;
    if (secsRef.current) secsRef.current.textContent = secs;
  });
  return (
    <div className="time" aria-label="Posición (compás.tiempo y segundos)">
      <span className="time-bars" ref={barsRef} data-testid="time-bars">
        {timing ? formatBarBeat(0, timing) : "–.–"}
      </span>
      <span className="time-secs" ref={secsRef}>
        0:00.0
      </span>
    </div>
  );
}

function Transport() {
  const store = useStore();
  const eng = useEngine();
  const loopOn = useApp((s) => s.loopOn);
  const volume = useApp((s) => s.volume);
  const active = useApp((s) => s.active);
  const ready = !!active && eng.ready.includes(active);
  const loading = !!active && !ready;
  return (
    <div className="transport">
      <button
        type="button"
        className="play-btn"
        onClick={() => store.togglePlay()}
        aria-label={eng.playing ? "Pausa (espacio)" : "Reproducir (espacio)"}
        disabled={!active}
        data-testid="play"
      >
        {loading && !eng.playing ? <span className="spinner" /> : eng.playing ? <I.Pause size={22} /> : <I.Play size={22} />}
      </button>
      <TimeReadout />
      <button
        type="button"
        className="icon-btn"
        aria-pressed={loopOn}
        aria-label="Loop por compases (L)"
        title="Loop (L) — arrastrá sobre la onda para elegir los compases"
        onClick={() => store.toggleLoop()}
        data-testid="loop-toggle"
      >
        <I.Loop />
      </button>
      <button
        type="button"
        className="btn small"
        onClick={() => store.openDraft()}
        disabled={!active}
        title="Comentar en este punto (M)"
        data-testid="comment-here"
      >
        <I.Comment size={15} /> Comentar <kbd className="hide-narrow">M</kbd>
      </button>
      <span className="grow" />
      {eng.audio === "suspended" && !eng.playing && <span className="hint hide-narrow">Tocá play para activar el audio</span>}
      {eng.audio === "unsupported" && <span className="hint">Este navegador no tiene Web Audio</span>}
      <label className="volume">
        {volume === 0 ? <I.Mute /> : <I.Volume />}
        <span className="sr-only">Volumen</span>
        <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => store.setVolume(Number(e.target.value))} />
      </label>
    </div>
  );
}

function QuickComment() {
  const store = useStore();
  const draft = useApp((s) => s.draft);
  const metas = useApp((s) => s.metas);
  const label = useSideLabel();
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  if (!draft) return null;
  const meta = draft.version ? metas[draft.version] : undefined;
  const timing = timingOf(meta);
  const tracks = meta?.tracks ?? [];
  const submit = () => {
    if (!text.trim()) return;
    void store.submitDraft(text);
    setText("");
  };
  return (
    <form
      className="qc"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          store.closeDraft();
        }
      }}
      aria-label="Comentario en el punto de reproducción"
      data-testid="quick-comment"
    >
      <div className="qc-head">
        <span className="tag a">{label(draft.version)}</span>
        {timing && (
          <span className="mono">
            compás {formatBarBeat(draft.atS, timing)} · {formatClock(draft.atS)}
          </span>
        )}
        <span className="grow" />
        <span className="hint hide-narrow">
          <kbd>Enter</kbd> envía · <kbd>Esc</kbd> cancela
        </span>
      </div>
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="¿Qué pasa acá? (ej: el bombo se come al sample)"
        aria-label="Texto del comentario"
        maxLength={2000}
        data-testid="quick-comment-input"
      />
      {tracks.length > 0 && (
        <div className="chips" role="group" aria-label="Pista (opcional)">
          <button type="button" className="chip" aria-pressed={draft.track == null} onClick={() => store.updateDraft({ track: null })}>
            toda la mezcla
          </button>
          {tracks.map((t) => (
            <button key={t.id} type="button" className="chip" aria-pressed={draft.track === t.id} onClick={() => store.updateDraft({ track: t.id })}>
              {t.id}
            </button>
          ))}
        </div>
      )}
      <div className="fb-compose-row">
        <span className="hint">Lo lee el agente antes de la próxima versión.</span>
        <span style={{ display: "flex", gap: 6 }}>
          <button type="button" className="btn ghost small" onClick={() => store.closeDraft()}>
            Cancelar
          </button>
          <button type="submit" className="btn primary small" disabled={!text.trim()}>
            Enviar
          </button>
        </span>
      </div>
    </form>
  );
}

function ABBar() {
  const store = useStore();
  const a = useApp((s) => s.a);
  const b = useApp((s) => s.b);
  const active = useApp((s) => s.active);
  const blind = useApp((s) => s.blind);
  const loudnessMatch = useApp((s) => s.loudnessMatch);
  const metrics = useApp((s) => s.metrics);
  const label = useSideLabel();
  const off = useMemo(() => store.levelOffsets(), [store, a, b, metrics]); // eslint-disable-line react-hooks/exhaustive-deps

  if (blind) {
    const picked = blind.picked;
    return (
      <div className="ab" data-testid="ab-bar">
        <div className="ab-sides" role="group" aria-label="Modo ciego">
          {(["x", "y"] as const).map((slot) => (
            <button
              key={slot}
              type="button"
              className={`ab-side ${slot === "x" ? "a" : "b"}`}
              aria-pressed={active === blind[slot]}
              onClick={() => void store.hearBlind(slot)}
              data-testid={`blind-${slot}`}
            >
              <span className="letter">{slot.toUpperCase()}</span>
              <span className="v">{picked ? blind[slot] : "oculto"}</span>
            </button>
          ))}
        </div>
        {picked ? (
          <div className="blind-reveal" role="status" data-testid="blind-reveal">
            <I.Eye />
            <span>
              Elegiste <strong>{picked.toUpperCase()}</strong>: era <strong className="mono">{blind[picked]}</strong>. Lo anoté para el agente.
            </span>
            <button type="button" className="btn small" onClick={() => store.stopBlind()}>
              Salir del modo ciego
            </button>
          </div>
        ) : (
          <div className="ab-opts">
            <span className="hint">¿Cuál va?</span>
            <button type="button" className="btn small" onClick={() => void store.chooseWinner(blind.x)} data-testid="pick-x">
              Me quedo con X
            </button>
            <button type="button" className="btn small" onClick={() => void store.chooseWinner(blind.y)} data-testid="pick-y">
              Me quedo con Y
            </button>
            <button type="button" className="btn ghost small" onClick={() => store.stopBlind()}>
              Cancelar
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ab" data-testid="ab-bar">
      <div className="ab-sides" role="group" aria-label="Comparación A/B">
        <button type="button" className="ab-side a" aria-pressed={!!a && active === a} onClick={() => void store.hearSide("a")} disabled={!a} data-testid="hear-a">
          <span className="letter">A</span>
          <span className="v">{label(a)}</span>
        </button>
        <button type="button" className="ab-side b" aria-pressed={!!b && active === b} onClick={() => void store.hearSide("b")} disabled={!b} data-testid="hear-b">
          <span className="letter">B</span>
          <span className="v">{b ? label(b) : "elegí una"}</span>
        </button>
      </div>
      <button type="button" className="icon-btn" onClick={() => void store.flip()} aria-label="Cambiar A/B (S)" title="Cambiar A/B (S)" disabled={!a || !b} data-testid="flip">
        <I.Swap />
      </button>
      <div className="ab-opts">
        <label className="toggle" title="Lo más fuerte siempre parece mejor: igualamos el loudness (LUFS) para comparar en serio.">
          <input type="checkbox" checked={loudnessMatch} onChange={(e) => store.setLoudnessMatch(e.target.checked)} data-testid="loudness-match" />
          Igualar volumen
        </label>
        <button type="button" className="btn small" onClick={() => store.startBlind()} disabled={!a || !b} data-testid="blind-start">
          <I.Eye size={14} /> A ciegas
        </button>
        {a && b && (
          <span className="hide-narrow" style={{ display: "inline-flex", gap: 4 }}>
            <button type="button" className="btn ghost small" onClick={() => void store.chooseWinner(a)} title="Anotar que preferís A">
              Gana A
            </button>
            <button type="button" className="btn ghost small" onClick={() => void store.chooseWinner(b)} title="Anotar que preferís B">
              Gana B
            </button>
          </span>
        )}
      </div>
      <p className="ab-note" data-testid="loudness-note">
        {!a || !b
          ? "Elegí B en la lista de versiones para comparar en el mismo punto."
          : loudnessMatch
            ? off
              ? Object.entries(off).some(([, d]) => d < -0.05)
                ? `Volumen igualado: ${Object.entries(off)
                    .filter(([, d]) => d < -0.05)
                    .map(([v, d]) => `${v} baja ${fmtDb(Math.abs(d))}`)
                    .join(", ")} (lo más fuerte siempre suena "mejor").`
                : "Ya suenan igual de fuerte (LUFS)."
              : "No hay LUFS de alguna versión: no puedo igualar el volumen."
            : "Sin igualar: ojo, la más fuerte va a parecer mejor."}
      </p>
    </div>
  );
}
