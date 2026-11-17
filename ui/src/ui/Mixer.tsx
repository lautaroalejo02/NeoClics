import { isMixerDefault, laneCtl, trackCtl, trackGain } from "../audio/mixer";
import { useApp, useEngine, useStore } from "../state/react";
import { fmtDb } from "../text/es";
import * as I from "./icons";

const KIND: Record<string, string> = { clips: "clips", pattern: "patrón", instrument: "instrumento" };

export function MixerPanel() {
  const store = useStore();
  const active = useApp((s) => s.active);
  const metas = useApp((s) => s.metas);
  const mixer = useApp((s) => s.mixer);
  const eng = useEngine();
  const meta = active ? metas[active] : undefined;
  const isDefault = isMixerDefault(mixer);

  if (!meta) return <p className="empty-small">Elegí una versión para ver sus pistas.</p>;
  if (meta.tracks.length === 0) return <p className="empty-small">Este render no trae stems por pista.</p>;

  return (
    <div data-testid="mixer">
      <div className="fb-compose-row" style={{ marginBottom: 8 }}>
        <span className="hint">Mute/solo sobre los stems del render, sin re-renderizar. Vale para A y B.</span>
        <span className="mixer-status" role="status">
          {isDefault ? (
            <span className="tag ok">mix final con master</span>
          ) : eng.listening === "stems" ? (
            <span className="tag warn" data-testid="mixer-stems-status">
              stems sin master (nivel igualado)
            </span>
          ) : (
            <span className="tag">cargando stems…</span>
          )}
          <button type="button" className="btn ghost small" onClick={() => store.resetMixer()} disabled={isDefault}>
            Resetear
          </button>
        </span>
      </div>
      <div>
        {meta.tracks.map((t) => {
          const c = trackCtl(mixer, t.id);
          const audible = trackGain(mixer, t.id) > 0;
          return (
            <div key={t.id} className={`trk${c.mute ? " muted" : ""}${audible ? "" : " silent"}`} data-testid={`track-${t.id}`}>
              <div className="trk-name">
                <strong>{t.id}</strong>
                <span>{KIND[t.kind] ?? t.kind}</span>
              </div>
              <span className="ms">
                <button type="button" className="m" aria-pressed={c.mute} onClick={() => store.setTrack(t.id, { mute: !c.mute })} aria-label={`Mute ${t.id}`} data-testid={`mute-${t.id}`}>
                  M
                </button>
                <button type="button" className="s" aria-pressed={c.solo} onClick={() => store.setTrack(t.id, { solo: !c.solo })} aria-label={`Solo ${t.id}`} data-testid={`solo-${t.id}`}>
                  S
                </button>
              </span>
              <input
                type="range"
                min={-24}
                max={6}
                step={0.5}
                value={c.gainDb}
                onChange={(e) => store.setTrack(t.id, { gainDb: Number(e.target.value) })}
                onDoubleClick={() => store.setTrack(t.id, { gainDb: 0 })}
                aria-label={`Volumen de ${t.id} en dB`}
                aria-valuetext={`${c.gainDb} dB`}
              />
              <span className="db">{fmtDb(c.gainDb, { sign: true })}</span>
              {t.lanes.length > 0 && (
                <div className="lanes" role="group" aria-label={`Carriles de ${t.id}`}>
                  {t.lanes.map((l) => {
                    const lc = laneCtl(mixer, t.id, l.id);
                    return (
                      <span key={l.id} className="lane">
                        {l.id}
                        <span className="ms">
                          <button type="button" className="m" aria-pressed={lc.mute} onClick={() => store.setLane(t.id, l.id, { mute: !lc.mute })} aria-label={`Mute carril ${l.id} de ${t.id}`}>
                            M
                          </button>
                          <button type="button" className="s" aria-pressed={lc.solo} onClick={() => store.setLane(t.id, l.id, { solo: !lc.solo })} aria-label={`Solo carril ${l.id} de ${t.id}`}>
                            S
                          </button>
                        </span>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {!isDefault && (
        <p className="hint" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10 }}>
          <I.Info size={14} /> Los stems vienen antes del master: igualo su nivel al del mix para que no salte el volumen, pero sin el compresor ni el limitador final.
        </p>
      )}
    </div>
  );
}
