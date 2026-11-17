// "Borradores y previews": playable audio that is NOT a version — partial or
// experimental renders the agent did not version, and the previews it makes
// (source grid check, chop candidates). Shown only when the server exposes
// any (`drafts` / `previews` in GET /project, `draft.created` events).
import { useRef } from "react";
import type { DraftInfo } from "../api/types";
import { useRaf } from "../hooks/hooks";
import { formatClock } from "../music/time";
import { useApp, useEngine, useStore } from "../state/react";
import { timeAgo } from "../state/derive";
import * as I from "./icons";

const KIND_LABEL: Record<NonNullable<DraftInfo["kind"]>, string> = {
  draft: "borrador",
  grid: "chequeo de la grilla",
  chop: "preview de chop",
  preview: "preview",
};

export function DraftsArea() {
  const drafts = useApp((s) => s.drafts);
  if (drafts.length === 0) return null;
  return (
    <section className="drafts" aria-labelledby="drafts-title" data-testid="drafts" style={{ padding: "0 14px 14px" }}>
      <details open={drafts.length <= 4}>
        <summary id="drafts-title" style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
          Borradores y previews <span className="tag">{drafts.length}</span>
        </summary>
        <p className="hint" style={{ margin: "6px 0 10px" }}>
          Audio que el agente generó sin guardarlo como versión: no aparece en la lista de versiones ni se compara en A/B.
        </p>
        <ul className="drafts-list">
          {drafts.map((d) => (
            <DraftRow key={d.id} d={d} />
          ))}
        </ul>
      </details>
    </section>
  );
}

function DraftRow({ d }: { d: DraftInfo }) {
  const store = useStore();
  const eng = useEngine();
  const key = `draft:${d.id}`;
  const playing = eng.preview === key;
  const barRef = useRef<HTMLSpanElement>(null);
  useRaf(() => {
    const pos = playing ? store.engine.getPreviewPosition() : null;
    if (barRef.current) barRef.current.style.width = pos != null && d.duration_s ? `${Math.min(100, (pos / d.duration_s) * 100)}%` : "0%";
  }, playing);
  const kind = d.kind ?? "draft";
  const name = d.label || d.path.split("/").pop() || d.id;
  return (
    <li data-testid={`draft-${d.id}`}>
      <button type="button" className="btn small" aria-pressed={playing} onClick={() => void store.playDraft(d)} data-testid={`play-draft-${d.id}`} aria-label={`${playing ? "Parar" : "Escuchar"} ${name}`}>
        {playing ? <I.Stop size={13} /> : <I.Play size={14} />}
      </button>
      <div className="grow">
        <div>
          <strong>{name}</strong> <span className="tag">{KIND_LABEL[kind]}</span> <span className="tag warn">no es una versión</span>
        </div>
        <div className="hint">
          {d.range_bars ? `compases ${d.range_bars[0]}–${d.range_bars[1] - 1}` : null}
          {d.range_bars && d.duration_s ? " · " : null}
          {d.duration_s ? formatClock(d.duration_s) : null}
          {d.created_at ? `${d.range_bars || d.duration_s ? " · " : ""}${timeAgo(d.created_at)}` : null}
        </div>
        {playing && (
          <div className="score" aria-hidden="true" style={{ marginTop: 4 }}>
            <i ref={barRef} style={{ width: "0%" }} />
          </div>
        )}
      </div>
    </li>
  );
}
