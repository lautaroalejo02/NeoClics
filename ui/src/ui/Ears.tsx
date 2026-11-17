import { useState } from "react";
import type { Alarm, Metrics, VersionId } from "../api/types";
import { formatBarBeat, formatClock } from "../music/time";
import { useApp, useStore } from "../state/react";
import { timingOf } from "../state/store";
import { alarmCopy, fmtDb, fmtLufs, fmtMs, fmtNum, fmtPct, keyLabel, parseKey, sameTonic, severityLabel } from "../text/es";
import * as I from "./icons";

interface Tip {
  x: number;
  y: number;
  text: string;
}

export function EarsPanel() {
  const store = useStore();
  const active = useApp((s) => s.active);
  const a = useApp((s) => s.a);
  const b = useApp((s) => s.b);
  const metrics = useApp((s) => s.metrics);
  const metas = useApp((s) => s.metas);
  const blind = useApp((s) => s.blind);
  const [tip, setTip] = useState<Tip | null>(null);
  if (blind && !blind.picked) return <div className="curtain">Las métricas se ocultan en el modo ciego (te dirían cuál es cuál).</div>;
  const m = active ? metrics[active] : null;
  if (!active) return <p className="empty-small">Elegí una versión.</p>;
  if (!m) return <p className="empty-small">Esta versión no tiene métricas todavía (metrics.json). El agente las genera con cada render.</p>;
  const other = active === a ? b : a;
  const om = other ? metrics[other] ?? null : null;
  const t = timingOf(metas[active]);
  const series: { v: VersionId; m: Metrics; color: string; letter: string }[] = [
    { v: active, m, color: active === b ? "var(--b)" : "var(--a)", letter: active === b ? "B" : "A" },
  ];
  if (other && om) series.push({ v: other, m: om, color: other === b ? "var(--b)" : "var(--a)", letter: other === b ? "B" : "A" });
  series.sort((x, y) => x.letter.localeCompare(y.letter));
  const showTip = (e: React.MouseEvent | React.FocusEvent, text: string) => {
    const r = (e.currentTarget as Element).getBoundingClientRect();
    setTip({ x: r.left + r.width / 2, y: r.top - 8, text });
  };

  const sorted = [...m.alarms].sort((x, y) => sev(y.severity) - sev(x.severity));
  const lufs = m.loudness.integrated_lufs;
  const dl = om ? lufs - om.loudness.integrated_lufs : null;

  return (
    <div data-testid="ears" onMouseLeave={() => setTip(null)}>
      <h3 className="card-title" style={{ fontSize: 13, margin: "0 0 8px" }}>
        Alarmas de {active}
      </h3>
      {sorted.length === 0 ? (
        <p className="empty-small" style={{ paddingTop: 0 }}>
          <I.Check size={14} /> Sin alarmas: nada raro en lo que se puede medir. Igual, confiá en tu oído.
        </p>
      ) : (
        <ul className="alarms">
          {sorted.map((al, i) => (
            <li key={i}>
              <AlarmRow al={al} onJump={al.at_s != null ? () => store.seek(al.at_s as number) : null} where={al.at_s != null && t ? `${formatBarBeat(al.at_s, t)} · ${formatClock(al.at_s)}` : null} />
            </li>
          ))}
        </ul>
      )}
      <div className="ears-grid">
        <div className="metric">
          <h4>Volumen (loudness integrado)</h4>
          <div className="big mono">
            {fmtNum(lufs, 1)}
            <small>LUFS</small>
          </div>
          <p>
            Pico real {fmtNum(m.loudness.true_peak_dbtp, 1)} dBTP
            {m.loudness.clipping ? ` · ¡clipping! (${m.loudness.clipped_samples} muestras)` : " · sin clipping"}
          </p>
          {dl != null && other && (
            <p data-testid="lufs-delta">
              {Math.abs(dl) < 0.3
                ? `Igual de fuerte que ${other}.`
                : `${fmtDb(Math.abs(dl))} ${dl > 0 ? "más fuerte" : "más bajo"} que ${other}.`}
            </p>
          )}
          <LoudnessScale series={series} onTip={showTip} onLeave={() => setTip(null)} />
        </div>
        <div className="metric">
          <h4>Balance de frecuencias (mix)</h4>
          <Legend series={series} />
          <SpectralChart series={series} onTip={showTip} onLeave={() => setTip(null)} />
          <p>{spectralNote(m, om, other)}</p>
        </div>
        <div className="metric">
          <h4>
            Graves {m.low_end_masking.band_hz[0]}–{m.low_end_masking.band_hz[1]} Hz por pista
          </h4>
          <MaskingChart m={m} color={series.find((s) => s.v === active)!.color} onTip={showTip} onLeave={() => setTip(null)} />
          <p>{maskingNote(m)}</p>
        </div>
        <div className="metric">
          <h4>Tonalidad, grilla y cortes</h4>
          <p data-testid="tonal-note">
            <TonalNote m={m} />
          </p>
          <p>
            Desfase máximo de transientes: <strong className="mono">{fmtMs(m.alignment.max_abs_offset_ms)}</strong>
            {m.alignment.max_abs_offset_ms > 10 ? " (se nota, algo está corrido)" : " (dentro de lo normal)"}.
          </p>
          <p>
            Clicks en bordes de chops: <strong className="mono">{m.clicks.count}</strong>
          </p>
          {m.style_similarity && (
            <p>
              Parecido a tus referencias: <strong className="mono">{fmtPct(m.style_similarity.mean_score)}</strong>
            </p>
          )}
        </div>
      </div>
      <details style={{ marginTop: 12 }}>
        <summary className="hint" style={{ cursor: "pointer" }}>
          Ver los números en tabla
        </summary>
        <MetricsTable series={series} />
      </details>
      {tip && (
        <div className="chart-tip" style={{ left: tip.x, top: tip.y, transform: "translate(-50%, -100%)" }} role="tooltip">
          {tip.text}
        </div>
      )}
    </div>
  );
}

function sev(s: Alarm["severity"]): number {
  return s === "error" ? 2 : s === "warn" ? 1 : 0;
}

function AlarmRow({ al, onJump, where }: { al: Alarm; onJump: (() => void) | null; where: string | null }) {
  const copy = alarmCopy(al.code);
  // the backend writes its message in Spanish, with the specifics: prefer it
  const msg = al.message?.trim() ? al.message : copy.explain;
  const content = (
    <>
      <span className="alarm-ic">{al.severity === "info" ? <I.Info /> : <I.Alert />}</span>
      <span>
        <span className="alarm-title">{copy.title}</span>
        {al.track && <span className="tag" style={{ marginLeft: 6 }}>{al.track}</span>}
        <br />
        <span className="alarm-msg">{msg}</span>
        {msg !== copy.explain && <span className="alarm-explain hint"><br />{copy.explain}</span>}
      </span>
      {where ? <span className="fb-at">{where}</span> : <span className="tag">{severityLabel(al.severity)}</span>}
    </>
  );
  return onJump ? (
    <button type="button" className={`alarm ${al.severity}`} data-code={al.code} onClick={onJump} aria-label={`${copy.title}: ${msg}. Ir a ${where}`}>
      {content}
    </button>
  ) : (
    <div className={`alarm ${al.severity}`} data-code={al.code}>{content}</div>
  );
}

/** "Suena en Eb mayor (confianza 100%), como la canción: en tono". Enharmonic
 *  spellings are the same note (D# = Eb) and one spelling is shown. */
function TonalNote({ m }: { m: Metrics }) {
  const est = m.tonal.estimated;
  const target = m.tonal.target_key;
  const pk = parseKey(target);
  if (!est) return <>Tonalidad objetivo {target}: sin estimación del audio.</>;
  const same = sameTonic(est.tonic, target);
  const inKey = m.tonal.in_key || (same && m.tonal.mode_match);
  // the song's own spelling wins (D# and Eb are the same note: show one)
  const tonicText = /^\s*[A-Ga-g][#b♯♭]?/.exec(target)?.[0].trim() ?? target;
  const targetMode = pk?.mode ?? (m.tonal.mode_match ? est.mode : null);
  return (
    <>
      Suena en {keyLabel(est.tonic, est.mode, target)} (confianza {fmtPct(est.confidence)}); la canción está en {keyLabel(tonicText, targetMode, target)}
      {": "}
      <strong style={{ color: inKey ? "var(--ok)" : "var(--warn)" }}>{inKey ? "en tono" : "no coincide"}</strong>.
    </>
  );
}

type Series = { v: VersionId; m: Metrics; color: string; letter: string }[];
type TipFn = (e: React.MouseEvent | React.FocusEvent, text: string) => void;

function Legend({ series }: { series: Series }) {
  if (series.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {series.map((s) => (
        <span key={s.v}>
          <span className="sw" style={{ background: s.color }} />
          {s.letter} · {s.v}
        </span>
      ))}
    </div>
  );
}

function LoudnessScale({ series, onTip, onLeave }: { series: Series; onTip: TipFn; onLeave: () => void }) {
  const lo = -24;
  const hi = -6;
  const W = 260;
  const x = (v: number) => 10 + ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (W - 20);
  return (
    <svg className="chart" viewBox={`0 0 ${W} 56`} role="img" aria-label={series.map((s) => `${s.letter} ${s.v}: ${fmtLufs(s.m.loudness.integrated_lufs)}`).join(", ")}>
      <line className="axis" x1={10} x2={W - 10} y1={26} y2={26} strokeWidth={2} />
      {[-24, -18, -14, -10, -6].map((v) => (
        <g key={v}>
          <line className="axis" x1={x(v)} x2={x(v)} y1={22} y2={30} />
          <text x={x(v)} y={54} textAnchor="middle">
            {v}
          </text>
        </g>
      ))}
      {series.map((s, i) => (
        <g key={s.v} tabIndex={0} onMouseEnter={(e) => onTip(e, `${s.v}: ${fmtLufs(s.m.loudness.integrated_lufs)}`)} onFocus={(e) => onTip(e, `${s.v}: ${fmtLufs(s.m.loudness.integrated_lufs)}`)} onMouseLeave={onLeave} onBlur={onLeave}>
          <circle cx={x(s.m.loudness.integrated_lufs)} cy={26} r={7} fill={s.color} stroke="var(--bg-2)" strokeWidth={2} />
          {/* A above the line, B below: never collide when close */}
          <text x={x(s.m.loudness.integrated_lufs)} y={i === 0 ? 12 : 46} textAnchor="middle" style={{ fontWeight: 600 }}>
            {s.letter}
          </text>
        </g>
      ))}
    </svg>
  );
}

const BANDS = [
  { k: "low_db", label: "graves" },
  { k: "mid_db", label: "medios" },
  { k: "high_db", label: "agudos" },
] as const;

function SpectralChart({ series, onTip, onLeave }: { series: Series; onTip: TipFn; onLeave: () => void }) {
  const W = 260;
  const H = 120;
  const floor = -60;
  const vals = series.flatMap((s) => BANDS.map((b) => s.m.spectral.mix[b.k]));
  const top = Math.min(0, Math.ceil((Math.max(...vals) + 3) / 6) * 6);
  const y = (v: number) => 8 + (1 - (Math.max(floor, v) - floor) / (top - floor)) * (H - 30);
  const groupW = (W - 30) / BANDS.length;
  const barW = Math.min(26, (groupW - 16) / series.length - 2);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Nivel por banda: ${series.map((s) => `${s.v} graves ${fmtDb(s.m.spectral.mix.low_db)}, medios ${fmtDb(s.m.spectral.mix.mid_db)}, agudos ${fmtDb(s.m.spectral.mix.high_db)}`).join("; ")}`}>
      {[top, Math.round((top + floor) / 2 / 6) * 6, floor].map((g) => (
        <g key={g}>
          <line className="gridl" x1={30} x2={W} y1={y(g)} y2={y(g)} />
          <text x={26} y={y(g) + 4} textAnchor="end">
            {Math.round(g)}
          </text>
        </g>
      ))}
      {BANDS.map((b, gi) => {
        const gx = 30 + gi * groupW + (groupW - series.length * (barW + 2)) / 2;
        return (
          <g key={b.k}>
            {series.map((s, si) => {
              const v = s.m.spectral.mix[b.k];
              const x0 = gx + si * (barW + 2);
              const y0 = y(v);
              const h = Math.max(2, y(floor) - y0);
              const label = `${s.v} · ${b.label}: ${fmtDb(v)}`;
              return (
                <path
                  key={s.v}
                  d={`M${x0},${y0 + h} V${y0 + 4} a4,4 0 0 1 4,-4 H${x0 + barW - 4} a4,4 0 0 1 4,4 V${y0 + h} Z`}
                  fill={s.color}
                  tabIndex={0}
                  onMouseEnter={(e) => onTip(e, label)}
                  onFocus={(e) => onTip(e, label)}
                  onMouseLeave={onLeave}
                  onBlur={onLeave}
                  aria-label={label}
                />
              );
            })}
            <text x={30 + gi * groupW + groupW / 2} y={H - 6} textAnchor="middle">
              {b.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function MaskingChart({ m, color, onTip, onLeave }: { m: Metrics; color: string; onTip: TipFn; onLeave: () => void }) {
  const entries = Object.entries(m.low_end_masking.energy_db).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <p className="hint">Sin datos por pista.</p>;
  const W = 260;
  const rowH = 24;
  const floor = Math.min(-48, Math.floor(Math.min(...entries.map(([, v]) => Math.max(v, -90))) / 6) * 6);
  const top = Math.max(...entries.map(([, v]) => v)) + 2;
  const labelW = 64;
  const x = (v: number) => labelW + ((Math.max(floor, v) - floor) / (top - floor)) * (W - labelW - 96);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${entries.length * rowH + 4}`} role="img" aria-label={`Energía en graves: ${entries.map(([k, v]) => `${k} ${fmtDb(v)}`).join(", ")}`}>
      {entries.map(([k, v], i) => {
        const yy = i * rowH + 4;
        const w = Math.max(2, x(v) - labelW);
        const dom = k === m.low_end_masking.dominant_track;
        const label = `${k}: ${v <= -90 ? "silencio" : fmtDb(v)}${dom ? " (domina)" : ""}`;
        return (
          <g key={k} tabIndex={0} onMouseEnter={(e) => onTip(e, label)} onFocus={(e) => onTip(e, label)} onMouseLeave={onLeave} onBlur={onLeave} aria-label={label}>
            <text x={labelW - 6} y={yy + 13} textAnchor="end">
              {k}
            </text>
            <path d={`M${labelW},${yy} H${labelW + w - 4} a4,4 0 0 1 4,4 V${yy + 12} a4,4 0 0 1 -4,4 H${labelW} Z`} fill={color} opacity={dom ? 1 : 0.55} />
            <text x={labelW + w + 6} y={yy + 13} style={{ fontWeight: dom ? 700 : 400 }}>
              {v <= -90 ? "—" : fmtNum(v, 0)}
              {dom ? " · domina" : ""}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function spectralNote(m: Metrics, om: Metrics | null, other: VersionId | null): string {
  const s = m.spectral.mix;
  const parts: string[] = [];
  if (s.high_db < s.mid_db - 12) parts.push("Los agudos están bastante abajo: puede sonar opaco.");
  else if (s.low_db > s.mid_db + 6) parts.push("Mucho grave respecto de los medios.");
  else parts.push("Balance razonable entre graves, medios y agudos.");
  if (om && other) {
    const dh = s.high_db - om.spectral.mix.high_db;
    const dlow = s.low_db - om.spectral.mix.low_db;
    if (Math.abs(dh) >= 1.5) parts.push(`${dh > 0 ? "Más" : "Menos"} brillo que ${other} (${fmtDb(dh, { sign: true })} en agudos).`);
    if (Math.abs(dlow) >= 1.5) parts.push(`${dlow > 0 ? "Más" : "Menos"} graves que ${other} (${fmtDb(dlow, { sign: true })}).`);
  }
  return parts.join(" ");
}

function maskingNote(m: Metrics): string {
  const e = Object.entries(m.low_end_masking.energy_db).sort((a, b) => b[1] - a[1]);
  if (e.length < 2) return "";
  const gap = e[0][1] - e[1][1];
  if (gap < 3) return `${e[0][0]} y ${e[1][0]} compiten en los graves (a ${fmtDb(gap)}): el caso "el bombo se come al sample".`;
  return `${e[0][0]} manda en los graves con ${fmtDb(gap)} de ventaja.`;
}

function MetricsTable({ series }: { series: Series }) {
  const rows: [string, (m: Metrics) => string][] = [
    ["Loudness (LUFS)", (m) => fmtNum(m.loudness.integrated_lufs, 1)],
    ["True peak (dBTP)", (m) => fmtNum(m.loudness.true_peak_dbtp, 1)],
    ["Graves (dB)", (m) => fmtNum(m.spectral.mix.low_db, 1)],
    ["Medios (dB)", (m) => fmtNum(m.spectral.mix.mid_db, 1)],
    ["Agudos (dB)", (m) => fmtNum(m.spectral.mix.high_db, 1)],
    ["Desfase máx. (ms)", (m) => fmtNum(m.alignment.max_abs_offset_ms, 1)],
    ["Clicks", (m) => String(m.clicks.count)],
    ["Alarmas", (m) => String(m.alarms.length)],
  ];
  return (
    <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse", marginTop: 8 }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left", padding: 4 }}>Métrica</th>
          {series.map((s) => (
            <th key={s.v} style={{ textAlign: "right", padding: 4 }} className="mono">
              {s.letter} {s.v}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, f]) => (
          <tr key={name} style={{ borderTop: "1px solid var(--line)" }}>
            <td style={{ padding: 4 }}>{name}</td>
            {series.map((s) => (
              <td key={s.v} style={{ textAlign: "right", padding: 4 }} className="mono">
                {f(s.m)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
