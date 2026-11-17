import { useMemo, useState } from "react";
import type { FeedbackEvent, VersionId } from "../api/types";
import { eventKey } from "../feedback/events";
import { useTick } from "../hooks/hooks";
import { formatBarBeat, formatClock } from "../music/time";
import { useApp, useStore } from "../state/react";
import { isPending, newestVersionTs, timeAgo } from "../state/derive";
import { timingOf } from "../state/store";
import * as I from "./icons";

export function FeedbackPanel() {
  const store = useStore();
  const feedback = useApp((s) => s.feedback);
  const sendStatus = useApp((s) => s.sendStatus);
  const metas = useApp((s) => s.metas);
  const versions = useApp((s) => s.versions);
  const active = useApp((s) => s.active);
  const blind = useApp((s) => s.blind);
  const [scope, setScope] = useState<"all" | "this">("all");
  const [text, setText] = useState("");
  const [attach, setAttach] = useState(true);
  useTick(30_000);
  const hidden = !!blind && !blind.picked;
  const newest = newestVersionTs(metas, versions);

  const items = useMemo(() => {
    const list = feedback.filter((e) => scope === "all" || versionOf(e) === active);
    return [...list].reverse();
  }, [feedback, scope, active]);
  const pendingCount = useMemo(() => feedback.filter((e) => isPending(e, newest)).length, [feedback, newest]);

  const send = async () => {
    const ok = await store.generalComment(text, attach && !hidden ? active : null);
    if (ok) setText("");
  };

  const jump = async (e: FeedbackEvent) => {
    if (e.type !== "comment" || e.at_s == null) return;
    if (e.version && e.version !== active && !hidden) await store.listen(e.version);
    store.seek(e.at_s);
  };

  return (
    <section className="card" aria-labelledby="fb-title" data-testid="feedback">
      <div className="card-head">
        <h2 className="card-title" id="fb-title">
          Feedback para el agente
        </h2>
        {pendingCount > 0 && (
          <span className="tag pending" title="El agente todavía no lo leyó: lo lee antes de la próxima versión" data-testid="pending-count">
            {pendingCount} pendiente{pendingCount === 1 ? "" : "s"}
          </span>
        )}
      </div>
      <div className="card-body">
        <form
          className="fb-compose"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label className="sr-only" htmlFor="general-comment">
            Comentario general
          </label>
          <textarea
            id="general-comment"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Comentario general: qué te gusta, qué cambiarías…"
            maxLength={4000}
            data-testid="general-comment"
          />
          <div className="fb-compose-row">
            {active && !hidden ? (
              <label className="toggle">
                <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
                sobre {active}
              </label>
            ) : (
              <span />
            )}
            <button type="submit" className="btn primary small" disabled={!text.trim()}>
              Enviar
            </button>
          </div>
        </form>
        <div className="fb-compose-row" style={{ marginTop: 14 }}>
          <div className="seg" role="group" aria-label="Filtrar">
            <button type="button" aria-pressed={scope === "all"} onClick={() => setScope("all")}>
              Todo
            </button>
            <button type="button" aria-pressed={scope === "this"} onClick={() => setScope("this")} disabled={hidden}>
              Esta versión
            </button>
          </div>
          <span className="hint">
            <kbd>M</kbd> comenta en el punto que suena
          </span>
        </div>
        {hidden ? (
          <p className="empty-small">Oculto mientras comparás a ciegas.</p>
        ) : items.length === 0 ? (
          <p className="empty-small">Todavía no dejaste feedback{scope === "this" ? " en esta versión" : ""}. Apretá M mientras escuchás para marcar un momento.</p>
        ) : (
          <ul className="fb-list" data-testid="feedback-list">
            {items.map((e) => {
              const k = eventKey(e);
              const st = sendStatus[k];
              const pending = isPending(e, newest);
              return (
                <li key={k} className={`fb-item${st === "error" ? " error" : ""}`}>
                  <span className="fb-icon" aria-hidden="true">
                    {iconFor(e)}
                  </span>
                  <div>
                    <div className="fb-meta">
                      {headline(e, metas, (v) => store.listen(v), jump)}
                      <span>{timeAgo(e.ts)}</span>
                      {st === "sending" && <span className="tag">enviando…</span>}
                      {st === "error" && (
                        <>
                          <span className="tag err">no se guardó</span>
                          <button type="button" className="linkish" onClick={() => void store.append(e)}>
                            reintentar
                          </button>
                          <button type="button" className="linkish" onClick={() => store.discard(e)}>
                            descartar
                          </button>
                        </>
                      )}
                      {!st && pending && (
                        <span className="tag pending" title="El agente lo lee antes de la próxima versión">
                          pendiente de leer por el agente
                        </span>
                      )}
                    </div>
                    {body(e)}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <p className="legend-pending">
          <I.Info size={13} /> “Pendiente” = todavía no hay una versión más nueva que este feedback: el agente lo va a leer en la próxima iteración.
        </p>
      </div>
    </section>
  );
}

function versionOf(e: FeedbackEvent): VersionId | null {
  if (e.type === "ab_choice") return e.winner;
  return e.version ?? null;
}

function iconFor(e: FeedbackEvent) {
  switch (e.type) {
    case "rating":
      return e.value === "like" ? <I.ThumbUp size={15} /> : <I.ThumbDown size={15} />;
    case "comment":
      return <I.Comment size={15} />;
    case "ab_choice":
      return <I.Swap size={15} />;
    case "chop_choice":
      return <I.Layers size={15} />;
  }
}

function headline(
  e: FeedbackEvent,
  metas: Record<string, import("../api/types").RenderMeta>,
  listen: (v: VersionId) => void,
  jump: (e: FeedbackEvent) => void,
) {
  const vtag = (v: VersionId | null | undefined) =>
    v ? (
      <button type="button" className="tag" style={{ border: 0 }} onClick={() => listen(v)} title={`Escuchar ${v}`}>
        {v}
      </button>
    ) : null;
  if (e.type === "comment") {
    const t = e.version ? timingOf(metas[e.version]) : null;
    return (
      <>
        {vtag(e.version) ?? <span className="tag">general</span>}
        {e.at_s != null && (
          <button type="button" className="fb-at" onClick={() => jump(e)} title="Ir a este punto">
            {t ? `${formatBarBeat(e.at_s, t)} · ` : ""}
            {formatClock(e.at_s)}
          </button>
        )}
        {e.track && <span className="tag">{e.track}</span>}
      </>
    );
  }
  if (e.type === "rating") return vtag(e.version);
  if (e.type === "ab_choice")
    return (
      <>
        {vtag(e.a)} vs {vtag(e.b)}
      </>
    );
  return vtag(e.version) ?? <span className="tag">chop</span>;
}

function body(e: FeedbackEvent) {
  switch (e.type) {
    case "comment":
      return <p className="fb-text">{e.text}</p>;
    case "rating":
      return <p className="fb-text">{e.value === "like" ? "Me gusta" : "No me gusta"}</p>;
    case "ab_choice":
      return (
        <p className="fb-text">
          En la comparación ganó <strong className="mono">{e.winner}</strong>
        </p>
      );
    case "chop_choice":
      return (
        <p className="fb-text">
          Elegiste el chop <span className="mono">{e.candidate_id}</span>
          {e.adjust && (e.adjust.start_beats || e.adjust.length_beats)
            ? ` (inicio ${signed(e.adjust.start_beats)}, largo ${signed(e.adjust.length_beats)} beats)`
            : ""}
        </p>
      );
  }
}

function signed(n: number): string {
  return `${n > 0 ? "+" : ""}${n}`;
}
