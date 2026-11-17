import { useMemo, useState } from "react";
import { copyText } from "../hooks/hooks";
import { regenRequest } from "../text/es";
import type { VersionId } from "../api/types";
import { useTick } from "../hooks/hooks";
import { useApp, useEngine, useStore } from "../state/react";
import { alarmCounts, clockTime, commentCounts, latestRatings, readableDiff, timeAgo, versionTree } from "../state/derive";
import * as I from "./icons";

export function VersionsPanel() {
  const store = useStore();
  const versions = useApp((s) => s.versions);
  const noAudio = useApp((s) => s.noAudio);
  const metas = useApp((s) => s.metas);
  const metrics = useApp((s) => s.metrics);
  const feedback = useApp((s) => s.feedback);
  const a = useApp((s) => s.a);
  const b = useApp((s) => s.b);
  const active = useApp((s) => s.active);
  const blind = useApp((s) => s.blind);
  const diffs = useApp((s) => s.diffs);
  const eng = useEngine();
  useTick(30_000);
  const all = useMemo(() => [...versions, ...noAudio], [versions, noAudio]);
  const ghostSet = useMemo(() => new Set(noAudio.map((v) => v.version)), [noAudio]);
  const info = useMemo(() => new Map(all.map((v) => [v.version, v])), [all]);
  const rows = useMemo(() => versionTree(all, metas), [all, metas]);
  const [copied, setCopied] = useState<string | null>(null);
  const ratings = useMemo(() => latestRatings(feedback), [feedback]);
  const counts = useMemo(() => commentCounts(feedback), [feedback]);
  const [open, setOpen] = useState<Record<VersionId, boolean>>({});

  const hidden = !!blind && !blind.picked;

  return (
    <section className="card" aria-labelledby="versions-title" data-testid="versions">
      <div className="card-head">
        <h2 className="card-title" id="versions-title">
          Versiones
        </h2>
        <span className="card-sub">
          {all.length}
          {noAudio.length > 0 ? ` · ${noAudio.length} sin audio` : ""}
        </span>
        <span className="header-spacer" />
        <span className="hint hide-narrow">
          <kbd>1</kbd>…<kbd>9</kbd> para saltar
        </span>
      </div>
      {hidden ? (
        <div className="curtain">
          Modo ciego activo: la lista queda oculta para que no sepas cuál es cuál. Usá <kbd>X</kbd>/<kbd>Y</kbd> y elegí una.
        </div>
      ) : (
        <ol className="vlist">
          {rows.map((r) => {
            const meta = metas[r.version];
            const al = alarmCounts(metrics[r.version]);
            const rating = ratings[r.version] ?? null;
            const isActive = r.version === active;
            const ghost = ghostSet.has(r.version);
            const n = versions.findIndex((v) => v.version === r.version) + 1;
            const request = meta?.request ?? info.get(r.version)?.request ?? null;
            const rerenderable = info.get(r.version)?.rerenderable;
            const diff = meta?.diff ?? [];
            const expanded = !!open[r.version];
            return (
              <li
                key={r.version}
                className={`vrow${isActive ? " active" : ""}${r.depth > 0 ? " branch" : ""}${ghost ? " ghost" : ""}`}
                style={{ ["--depth" as string]: r.depth }}
                onClick={ghost ? undefined : () => void store.listen(r.version)}
                data-testid={`version-${r.version}`}
                data-noaudio={ghost ? "true" : undefined}
                aria-current={isActive ? "true" : undefined}
              >
                <div className="vmain">
                  <div className="vhead">
                  <span className="vnum">
                  {isActive && (
                    <span className={`eq${eng.playing ? "" : " paused"}`} aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                  )}
                  {ghost ? (
                    <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>
                      {r.version}
                    </span>
                  ) : (
                  <button
                    type="button"
                    className="linkish mono"
                    style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)", textDecoration: "none" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      void store.listen(r.version);
                    }}
                    aria-label={`Escuchar ${r.version}${n <= 9 ? ` (tecla ${n})` : ""}`}
                  >
                    {r.version}
                  </button>
                  )}
                  </span>
                    <span className="vtime" title={meta?.created_at ?? ""}>
                      {timeAgo(r.createdAt)} · {clockTime(r.createdAt)}
                    </span>
                    {r.branched && r.parent && <span className="tag">de {r.parent}</span>}
                    {ghost && (
                      <span className="tag" data-testid={`noaudio-tag-${r.version}`}>
                        sin audio
                      </span>
                    )}
                    {al.error > 0 && (
                      <span className="tag err" title="Alarmas graves">
                        <I.Alert size={11} /> {al.error}
                      </span>
                    )}
                    {al.warn > 0 && (
                      <span className="tag warn" title="Alarmas">
                        <I.Alert size={11} /> {al.warn}
                      </span>
                    )}
                    {al.info > 0 && (
                      <span className="tag info" title="Avisos">
                        <I.Info size={11} /> {al.info}
                      </span>
                    )}
                  </div>
                  {request && <p className="vreq">{request}</p>}
                  {r.summary && <p className="vsum">{r.summary}</p>}
                  {expanded && r.parent && (() => {
                    const md = diffs[r.version];
                    if (md === "loading") return <p className="hint">Leyendo los cambios…</p>;
                    if (md === "error" || !md) return null;
                    return (
                      <div className="musical-diff" data-testid={`musical-diff-${r.version}`}>
                        {md.changes.length > 0 && (
                          <ul className="changes" aria-label={`Qué cambió respecto de ${r.parent}`}>
                            {md.changes.map((c, i) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        )}
                        {md.metrics.length > 0 && <p className="hint">Oídos: {md.metrics.join("; ")}</p>}
                      </div>
                    );
                  })()}
                  {expanded && diff.length > 0 && (
                    <ul className="diff" aria-label={`Cambios vs ${r.parent ?? "la anterior"}`}>
                      {diff.map((d, i) => {
                        const rd = readableDiff(d);
                        return (
                          <li key={i} title={d.path}>
                            <span className="where">
                              {rd.where}
                              {rd.what ? ` · ${rd.what}` : ""}
                            </span>
                            <span className="chg">
                              {rd.kind === "added" ? (
                                <>
                                  <span className="tag ok">nuevo</span> <span className="to">{rd.to}</span>
                                </>
                              ) : rd.kind === "removed" ? (
                                <>
                                  <span className="tag err">quitado</span> <span className="from">{rd.from}</span>
                                </>
                              ) : (
                                <>
                                  <span className="from">{rd.from}</span> → <span className="to">{rd.to}</span>
                                </>
                              )}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {ghost && (
                    <div className="noaudio" onClick={(e) => e.stopPropagation()} data-testid={`noaudio-${r.version}`}>
                      <p>
                        <strong>Sin audio:</strong> {rerenderable === false ? "no hay forma de regenerarla sola; pedile al agente que la reconstruya" : "pedile al agente que la regenere"} (render de {r.version}).
                      </p>
                      <button
                        type="button"
                        className="btn small"
                        data-testid={`copy-regen-${r.version}`}
                        onClick={async () => {
                          const ok = await copyText(regenRequest(r.version));
                          setCopied(ok ? r.version : null);
                          if (ok) store.toast("Copiado: pegáselo al agente", "ok", undefined, 3000);
                          else store.toast("No pude copiar. Decile: " + regenRequest(r.version), "warn", undefined, 9000);
                        }}
                      >
                        <I.Copy size={13} /> {copied === r.version ? "Copiado" : "Copiar el pedido"}
                      </button>
                    </div>
                  )}
                  <div className="vfoot" onClick={(e) => e.stopPropagation()}>
                    {!ghost && (
                    <span className="ab-pick" role="group" aria-label={`Usar ${r.version} en A/B`}>
                      <button type="button" className="a" aria-pressed={a === r.version} onClick={() => store.setA(r.version)} aria-label={`${r.version} como A`}>
                        A
                      </button>
                      <button type="button" className="b" aria-pressed={b === r.version} onClick={() => store.setB(r.version)} aria-label={`${r.version} como B`} data-testid={`set-b-${r.version}`}>
                        B
                      </button>
                    </span>
                    )}
                    {ghost ? (
                      rating && (
                        <span className="tag" title="Tu valoración de esa versión">
                          {rating === "like" ? <I.ThumbUp size={11} /> : <I.ThumbDown size={11} />} {rating === "like" ? "me gustó" : "no me gustó"}
                        </span>
                      )
                    ) : (
                    <span className="rate">
                      <button
                        type="button"
                        className="like"
                        aria-pressed={rating === "like"}
                        aria-label={`Me gusta ${r.version}`}
                        onClick={() => void store.rate(r.version, "like")}
                        data-testid={`like-${r.version}`}
                      >
                        <I.ThumbUp size={15} />
                      </button>
                      <button type="button" className="dislike" aria-pressed={rating === "dislike"} aria-label={`No me gusta ${r.version}`} onClick={() => void store.rate(r.version, "dislike")}>
                        <I.ThumbDown size={15} />
                      </button>
                    </span>
                    )}
                    {(counts[r.version] ?? 0) > 0 && (
                      <span className="tag" title="Comentarios">
                        <I.Comment size={11} /> {counts[r.version]}
                      </span>
                    )}
                    <span className="header-spacer" />
                    {(diff.length > 0 || r.parent) && (
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => {
                          if (!expanded) void store.loadDiff(r.version);
                          setOpen({ ...open, [r.version]: !expanded });
                        }}
                        aria-expanded={expanded}
                        data-testid={`changes-${r.version}`}
                      >
                        {expanded ? "ocultar cambios" : diff.length ? `${diff.length} cambio${diff.length === 1 ? "" : "s"}` : "sin cambios"}
                      </button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
