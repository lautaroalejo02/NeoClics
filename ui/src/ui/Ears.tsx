import { useState } from "react";
import type { Alarm, Metrics, VersionId } from "../api/types";
import { formatBarBeat, formatClock } from "../music/time";
import { useApp, useStore } from "../state/react";
import { timingOf } from "../state/store";
import * as I from "./icons";

const ALARM_TITLES: Record<string, string> = {
  low_end_masking: "Graves que se pisan",
  masking: "Graves que se pisan",
  click: "Click en un corte",
  clicks: "Clicks en los cortes",
  true_peak: "Pico muy alto",
  clipping: "Saturación digital (clipping)",
  alignment: "Fuera de grilla",
  off_grid: "Fuera de grilla",
  key: "Fuera de tonalidad",
  tonal: "Fuera de tonalidad",
  out_of_key: "Fuera de tonalidad",
  loudness: "Volumen general",
  too_quiet: "Muy bajo de volumen",
  too_loud: "Muy fuerte",
  spectral: "Balance de frecuencias",
};

function alarmTitle(code: string): string {
  return ALARM_TITLES[code] ?? code.replace(/_/g, " ");
}

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
            {lufs.toFixed(1)}
            <small>LUFS</small>
          </div>
          <p>
            Pico real {m.loudness.true_peak_dbtp.toFixed(1)} dBTP
            {m.loudness.clipping ? ` · ¡clipping! (${m.loudness.clipped_samples} muestras)` : " · sin clipping"}
          </p>
          {dl != null && other && (
            <p data-testid="lufs-delta">
              {Math.abs(dl) < 0.3
                ? `Igual de fuerte que ${other}.`
                : `${Math.abs(dl).toFixed(1)} dB ${dl > 0 ? "más fuerte" : "más bajo"} que ${other}.`}
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
          <p>
            {m.tonal.estimated
              ? `Suena en ${m.tonal.estimated.tonic}${m.tonal.estimated.mode === "minor" ? "m" : ""} (confianza ${Math.round(m.tonal.estimated.confidence * 100)}%), la canción está en ${m.tonal.target_key}: `
              : `Tonalidad objetivo ${m.tonal.target_key}: `}
            <strong style={{ color: m.tonal.in_key ? "var(--ok)" : "var(--warn)" }}>{m.tonal.in_key ? "en tono" : "no coincide"}</strong>.
          </p>
          <p>
            Desfase máximo de transientes: <strong className="mono">{m.alignment.max_abs_offset_ms.toFixed(1)} ms</strong>
            {m.alignment.max_abs_offset_ms > 10 ? " (se nota, algo está corrido)" : " (dentro de lo normal)"}.
          </p>
          <p>
            Clicks en bordes de chops: <strong className="mono">{m.clicks.count}</strong>
          </p>
          {m.style_similarity && (
            <p>
              Parecido a tus referencias: <strong className="mono">{Math.round(m.style_similarity.mean_score * 100)}%</strong>
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
  const content = (
    <>
      <span className="alarm-ic">{al.severity === "info" ? <I.Info /> : <I.Alert />}</span>
      <span>
        <span className="alarm-title">{alarmTitle(al.code)}</span>
        {al.track && <span className="tag" style={{ marginLeft: 6 }}>{al.track}</span>}
        <br />
        <span className="alarm-msg">{al.message}</span>
      </span>
      {where ? <span className="fb-at">{where}</span> : <span className="tag">{al.severity === "error" ? "grave" : al.severity === "warn" ? "ojo" : "aviso"}</span>}
    </>
  );
  return onJump ? (
    <button type="button" className={`alarm ${al.severity}`} onClick={onJump} aria-label={`${alarmTitle(al.code)}: ${al.message}. Ir a ${where}`}>
      {content}
    </button>
  ) : (
    <div className={`alarm ${al.severity}`}>{content}</div>
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
    <svg className="chart" viewBox={`0 0 ${W} 44`} role="img" aria-label={series.map((s) => `${s.letter} ${s.v}: ${s.m.loudness.integrated_lufs} LUFS`).join(", ")}>
      <line className="axis" x1={10} x2={W - 10} y1={22} y2={22} strokeWidth={2} />
      {[-24, -18, -14, -10, -6].map((v) => (
        <g key={v}>
          <line className="axis" x1={x(v)} x2={x(v)} y1={18} y2={26} />
          <text x={x(v)} y={40} textAnchor="middle">
            {v}
          </text>
        </g>
      ))}
      {series.map((s, i) => (
        <g key={s.v} tabIndex={0} onMouseEnter={(e) => onTip(e, `${s.v}: ${s.m.loudness.integrated_lufs.toFixed(1)} LUFS`)} onFocus={(e) => onTip(e, `${s.v}: ${s.m.loudness.integrated_lufs.toFixed(1)} LUFS`)} onMouseLeave={onLeave} onBlur={onLeave}>
          <circle cx={x(s.m.loudness.integrated_lufs)} cy={22} r={7} fill={s.color} stroke="var(--bg-2)" strokeWidth={2} />
          <text x={x(s.m.loudness.integrated_lufs)} y={i === 0 ? 10 : 10} textAnchor="middle" style={{ fontWeight: 600 }}>
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
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Nivel por banda: ${series.map((s) => `${s.v} graves ${s.m.spectral.mix.low_db} dB, medios ${s.m.spectral.mix.mid_db} dB, agudos ${s.m.spectral.mix.high_db} dB`).join("; ")}`}>
      {[top, (top + floor) / 2, floor].map((g) => (
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
              const label = `${s.v} · ${b.label}: ${v.toFixed(1)} dB`;
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
  const x = (v: number) => labelW + ((Math.max(floor, v) - floor) / (top - floor)) * (W - labelW - 50);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${entries.length * rowH + 4}`} role="img" aria-label={`Energía en graves: ${entries.map(([k, v]) => `${k} ${v} dB`).join(", ")}`}>
      {entries.map(([k, v], i) => {
        const yy = i * rowH + 4;
        const w = Math.max(2, x(v) - labelW);
        const dom = k === m.low_end_masking.dominant_track;
        const label = `${k}: ${v <= -90 ? "silencio" : `${v.toFixed(1)} dB`}${dom ? " (domina)" : ""}`;
        return (
          <g key={k} tabIndex={0} onMouseEnter={(e) => onTip(e, label)} onFocus={(e) => onTip(e, label)} onMouseLeave={onLeave} onBlur={onLeave} aria-label={label}>
            <text x={labelW - 6} y={yy + 13} textAnchor="end">
              {k}
            </text>
            <path d={`M${labelW},${yy} H${labelW + w - 4} a4,4 0 0 1 4,4 V${yy + 12} a4,4 0 0 1 -4,4 H${labelW} Z`} fill={color} opacity={dom ? 1 : 0.55} />
            <text x={labelW + w + 6} y={yy + 13} style={{ fontWeight: dom ? 700 : 400 }}>
              {v <= -90 ? "—" : v.toFixed(0)}
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
    if (Math.abs(dh) >= 1.5) parts.push(`${dh > 0 ? "Más" : "Menos"} brillo que ${other} (${dh > 0 ? "+" : ""}${dh.toFixed(1)} dB en agudos).`);
    if (Math.abs(dlow) >= 1.5) parts.push(`${dlow > 0 ? "Más" : "Menos"} graves que ${other} (${dlow > 0 ? "+" : ""}${dlow.toFixed(1)} dB).`);
  }
  return parts.join(" ");
}

function maskingNote(m: Metrics): string {
  const e = Object.entries(m.low_end_masking.energy_db).sort((a, b) => b[1] - a[1]);
  if (e.length < 2) return "";
  const gap = e[0][1] - e[1][1];
  if (gap < 3) return `${e[0][0]} y ${e[1][0]} compiten en los graves (a ${gap.toFixed(1)} dB): el caso "el bombo se come al sample".`;
  return `${e[0][0]} manda en los graves con ${gap.toFixed(1)} dB de ventaja.`;
}

function MetricsTable({ series }: { series: Series }) {
  const rows: [string, (m: Metrics) => string][] = [
    ["Loudness (LUFS)", (m) => m.loudness.integrated_lufs.toFixed(1)],
    ["True peak (dBTP)", (m) => m.loudness.true_peak_dbtp.toFixed(1)],
    ["Graves (dB)", (m) => m.spectral.mix.low_db.toFixed(1)],
    ["Medios (dB)", (m) => m.spectral.mix.mid_db.toFixed(1)],
    ["Agudos (dB)", (m) => m.spectral.mix.high_db.toFixed(1)],
    ["Desfase máx. (ms)", (m) => m.alignment.max_abs_offset_ms.toFixed(1)],
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
