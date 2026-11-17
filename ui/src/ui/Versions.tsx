import { useMemo, useState } from "react";
import type { VersionId } from "../api/types";
import { useTick } from "../hooks/hooks";
import { useApp, useEngine, useStore } from "../state/react";
import { alarmCounts, clockTime, commentCounts, latestRatings, readableDiff, timeAgo, versionTree } from "../state/derive";
import * as I from "./icons";

export function VersionsPanel() {
  const store = useStore();
  const versions = useApp((s) => s.versions);
  const metas = useApp((s) => s.metas);
  const metrics = useApp((s) => s.metrics);
  const feedback = useApp((s) => s.feedback);
  const a = useApp((s) => s.a);
  const b = useApp((s) => s.b);
  const active = useApp((s) => s.active);
  const blind = useApp((s) => s.blind);
  const eng = useEngine();
  useTick(30_000);
  const rows = useMemo(() => versionTree(versions, metas), [versions, metas]);
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
        <span className="card-sub">{versions.length}</span>
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
            const n = versions.findIndex((v) => v.version === r.version) + 1;
            const diff = meta?.diff ?? [];
            const expanded = !!open[r.version];
            return (
              <li
                key={r.version}
                className={`vrow${isActive ? " active" : ""}${r.depth > 0 ? " branch" : ""}`}
                style={{ ["--depth" as string]: r.depth }}
                onClick={() => void store.listen(r.version)}
                data-testid={`version-${r.version}`}
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
                  </span>
                    <span className="vtime" title={meta?.created_at ?? ""}>
                      {timeAgo(r.createdAt)} · {clockTime(r.createdAt)}
                    </span>
                    {r.branched && r.parent && <span className="tag">de {r.parent}</span>}
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
                  {meta?.request && <p className="vreq">{meta.request}</p>}
                  {r.summary && <p className="vsum">{r.summary}</p>}
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
                  <div className="vfoot" onClick={(e) => e.stopPropagation()}>
                    <span className="ab-pick" role="group" aria-label={`Usar ${r.version} en A/B`}>
                      <button type="button" className="a" aria-pressed={a === r.version} onClick={() => store.setA(r.version)} aria-label={`${r.version} como A`}>
                        A
                      </button>
                      <button type="button" className="b" aria-pressed={b === r.version} onClick={() => store.setB(r.version)} aria-label={`${r.version} como B`} data-testid={`set-b-${r.version}`}>
                        B
                      </button>
                    </span>
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
                    {(counts[r.version] ?? 0) > 0 && (
                      <span className="tag" title="Comentarios">
                        <I.Comment size={11} /> {counts[r.version]}
                      </span>
                    )}
                    <span className="header-spacer" />
                    {diff.length > 0 && (
                      <button type="button" className="linkish" onClick={() => setOpen({ ...open, [r.version]: !expanded })} aria-expanded={expanded}>
                        {expanded ? "ocultar cambios" : `${diff.length} cambio${diff.length === 1 ? "" : "s"}`}
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
