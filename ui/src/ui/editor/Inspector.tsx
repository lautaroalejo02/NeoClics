import { useEffect, useMemo, useState, type ReactNode, type RefObject } from "react";
import { useApp, useStore } from "../../state/react";
import { changeNotes, setClip, setFxParam, setHitVelocity, setPass, setTrackField, type Edit } from "../../editor/actions";
import { round6, type Op } from "../../editor/ops";
import { useEditor, useEditorStore } from "../../editor/react";
import { beatLabel, laneName, selectionLabel, type Selection } from "../../editor/selection";
import { BEATS_PER_BAR, chopById, noteName, patternHits, trackById, trackKind, trackNotes, type FxJson, type SongJson } from "../../editor/song";
import * as I from "../icons";

/** Numeric field with units; commits on Enter/blur (one undo step). */
function Num({ label, value, unit, min, max, step = 0.1, onCommit, testid, disabled }: { label: string; value: number | null | undefined; unit?: string; min?: number; max?: number; step?: number; onCommit: (v: number | null) => void; testid?: string; disabled?: boolean }) {
  const [text, setText] = useState(value == null ? "" : String(round6(value)));
  useEffect(() => setText(value == null ? "" : String(round6(value))), [value]);
  const commit = () => {
    if (text.trim() === "") return onCommit(null);
    const v = Number(text.replace(",", "."));
    if (!Number.isFinite(v)) return setText(value == null ? "" : String(value));
    const c = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
    if (c !== value) onCommit(c);
    else setText(String(round6(c)));
  };
  return (
    <label className="insp-field">
      <span>{label}</span>
      <span className="insp-input">
        <input
          type="text"
          inputMode="decimal"
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "ArrowUp" || e.key === "ArrowDown") {
              e.preventDefault();
              const v = (Number(text) || 0) + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1);
              onCommit(Math.min(max ?? Infinity, Math.max(min ?? -Infinity, round6(v))));
            }
          }}
          data-testid={testid}
          aria-label={label}
        />
        {unit && <span className="unit">{unit}</span>}
      </span>
    </label>
  );
}

function Check({ label, value, onChange, testid }: { label: string; value: boolean; onChange: (v: boolean) => void; testid?: string }) {
  return (
    <label className="insp-check">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} data-testid={testid} />
      <span>{label}</span>
    </label>
  );
}

function Section({ title, children, extra }: { title: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <section className="insp-sec">
      <h3>
        {title}
        {extra}
      </h3>
      {children}
    </section>
  );
}

export function Inspector({ askRef, onAudition, narrow, onClose }: { askRef: RefObject<HTMLTextAreaElement | null>; onAudition: (sample: string, gain?: number) => void; narrow: boolean; onClose: () => void }) {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const sel = useEditor((s) => s.selection);
  const status = useEditor((s) => s.trackStatus);
  const lastEditMs = useEditor((s) => s.lastEditMs);
  const apply = (e: Edit | null) => editor.apply(e);
  let body: ReactNode;
  switch (sel.kind) {
    case "track":
      body = <TrackPanel song={song} id={sel.track} apply={apply} />;
      break;
    case "clips":
      body = <ClipPanel song={song} sel={sel} apply={apply} />;
      break;
    case "hits":
      body = <HitsPanel song={song} sel={sel} apply={apply} onAudition={onAudition} />;
      break;
    case "notes":
      body = <NotesPanel song={song} sel={sel} />;
      break;
    case "points":
      body = <PointsPanel song={song} sel={sel} />;
      break;
    case "range":
      body = <p className="muted">Zona seleccionada: {selectionLabel(song, sel)}. Podés pedirle algo al agente sobre esta parte.</p>;
      break;
    default:
      body = (
        <div className="insp-empty">
          <p>Seleccioná algo del arreglo para ver y cambiar sus parámetros.</p>
          <ul className="muted">
            <li>Clic en un casillero vacío de la batería: agrega un golpe.</li>
            <li>Clic en un golpe: lo saca (Ctrl+Z lo devuelve).</li>
            <li>⇧ + clic o arrastrar: seleccionar.</li>
            <li>El nombre del track: su mezcla y efectos.</li>
          </ul>
          <TrackLegend status={status} />
        </div>
      );
  }
  return (
    <aside className={`ed-insp card ${narrow ? "sheet" : ""}`} aria-label="Detalles de la selección">
      <div className="insp-head">
        <strong>{sel.kind === "none" ? "Detalles" : selectionLabel(song, sel)}</strong>
        <span className="header-spacer" />
        {narrow && (
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Cerrar detalles">
            <I.X />
          </button>
        )}
      </div>
      <div className="insp-body">{body}</div>
      <AskBox askRef={askRef} />
      {lastEditMs != null && <div className="insp-foot muted mono">última edición aplicada en {lastEditMs.toFixed(1)} ms</div>}
    </aside>
  );
}

function TrackLegend({ status }: { status: Record<string, { state: string }> }) {
  const n = Object.values(status);
  const exact = n.filter((x) => x.state === "exact").length;
  return (
    <p className="muted small-text">
      <span className="dot ok" /> exacto (con efectos) · <span className="dot info" /> vista previa · <span className="dot warn" /> preparando. {exact}/{n.length} tracks exactos.
    </p>
  );
}

// ------------------------------------------------------------------ track

const FX_PARAMS: Record<string, [string, string, string, number, number, number][]> = {
  // [param, label, unit, min, max, step]
  highpass: [["cutoff_hz", "Corte", "Hz", 10, 22000, 10]],
  lowpass: [["cutoff_hz", "Corte", "Hz", 10, 22000, 10]],
  sidechain: [["depth", "Profundidad", "", 0, 1, 0.05], ["attack_ms", "Ataque", "ms", 0, 500, 1], ["release_ms", "Release", "ms", 1, 5000, 10]],
  compressor: [["threshold_db", "Umbral", "dB", -90, 0, 1], ["ratio", "Ratio", ":1", 1, 40, 0.5], ["attack_ms", "Ataque", "ms", 0, 1000, 1], ["release_ms", "Release", "ms", 1, 5000, 10], ["makeup_gain_db", "Ganancia", "dB", -24, 24, 0.5]],
  limiter: [["ceiling_db", "Techo", "dB", -30, 0, 0.1]],
  gain: [["gain_db", "Ganancia", "dB", -60, 24, 0.5]],
  reverb: [["room_size", "Sala", "", 0, 1, 0.05], ["wet_level", "Húmedo", "", 0, 1, 0.05], ["dry_level", "Seco", "", 0, 1, 0.05], ["damping", "Amortiguación", "", 0, 1, 0.05]],
  delay: [["time_beats", "Tiempo", "beats", 0.01, 64, 0.25], ["feedback", "Feedback", "", 0, 0.99, 0.05], ["mix", "Mezcla", "", 0, 1, 0.05]],
  chorus: [["rate_hz", "Velocidad", "Hz", 0.01, 20, 0.1], ["depth", "Profundidad", "", 0, 1, 0.05], ["mix", "Mezcla", "", 0, 1, 0.05]],
  phaser: [["rate_hz", "Velocidad", "Hz", 0.01, 20, 0.1], ["depth", "Profundidad", "", 0, 1, 0.05], ["mix", "Mezcla", "", 0, 1, 0.05]],
  saturation: [["drive_db", "Drive", "dB", 0, 60, 0.5]],
};

const FX_NAME: Record<string, string> = {
  highpass: "Filtro pasa-altos",
  lowpass: "Filtro pasa-bajos",
  sidechain: "Sidechain",
  compressor: "Compresor",
  limiter: "Limiter",
  gain: "Ganancia",
  reverb: "Reverb",
  delay: "Delay",
  chorus: "Chorus",
  phaser: "Phaser",
  saturation: "Saturación",
  plugin: "Plugin",
};

const NEW_FX: Record<string, Record<string, unknown>> = {
  highpass: { type: "highpass", cutoff_hz: 150 },
  lowpass: { type: "lowpass", cutoff_hz: 8000 },
  saturation: { type: "saturation", drive_db: 6 },
  reverb: { type: "reverb", room_size: 0.4, wet_level: 0.2 },
  delay: { type: "delay", time_beats: 0.75, feedback: 0.3, mix: 0.25 },
  chorus: { type: "chorus" },
  phaser: { type: "phaser" },
  compressor: { type: "compressor", threshold_db: -18, ratio: 3 },
  gain: { type: "gain", gain_db: 0 },
};

function freeFxId(fx: FxJson[], type: string): string {
  const base = { highpass: "hp", lowpass: "lp", saturation: "sat", reverb: "verb", delay: "dly", chorus: "chorus", phaser: "phaser", compressor: "comp", gain: "gain" }[type] ?? type;
  let id = base;
  let n = 2;
  while (fx.some((f) => f.id === id)) id = `${base}${n++}`;
  return id;
}

function TrackPanel({ song, id, apply }: { song: SongJson; id: string; apply: (e: Edit | null) => void }) {
  const t = trackById(song, id);
  const [adding, setAdding] = useState("lowpass");
  if (!t) return <p className="muted">Ese track ya no existe.</p>;
  const fx = t.fx ?? [];
  return (
    <>
      <Section title="Mezcla">
        <Num label="Volumen" unit="dB" value={t.gain_db ?? 0} min={-60} max={24} step={0.5} onCommit={(v) => apply(setTrackField(song, id, "gain_db", v ?? 0, `volumen de ${id}`))} testid="insp-track-gain" />
        <Num label="Paneo" unit="I −1 · D +1" value={t.pan ?? 0} min={-1} max={1} step={0.05} onCommit={(v) => apply(setTrackField(song, id, "pan", v ?? 0, `paneo de ${id}`))} />
        <Check label="Mute (se guarda)" value={!!t.mute} onChange={(v) => apply(setTrackField(song, id, "mute", v, v ? `mutear ${id}` : `activar ${id}`))} />
      </Section>
      <Section title={`Efectos (${fx.length})`}>
        {fx.length === 0 && <p className="muted small-text">Sin efectos.</p>}
        {fx.map((f, i) => (
          <div className="insp-fx" key={`${f.id ?? i}-${f.type}`}>
            <div className="insp-fx-head">
              <strong>{FX_NAME[f.type] ?? f.type}</strong>
              {f.id && <span className="chip mono">{f.id}</span>}
              <span className="header-spacer" />
              <button
                type="button"
                className="btn small ghost"
                onClick={() => apply({ ops: [{ op: "remove", path: f.id ? `tracks.${id}.fx.${f.id}` : `tracks.${id}.fx[${i}]`, expect: f }], label: `sacar ${FX_NAME[f.type] ?? f.type}`, track: id })}
                title="Sacar este efecto"
              >
                Sacar
              </button>
            </div>
            {(FX_PARAMS[f.type] ?? []).map(([p, label, unit, min, max, step]) => (
              <Num key={p} label={label} unit={unit} value={f[p] as number | undefined} min={min} max={max} step={step} onCommit={(v) => v != null && apply(setFxParam(song, id, i, p, v))} />
            ))}
            {f.type === "sidechain" && <p className="muted small-text">Reacciona a {String(f.key_track)}{f.key_lane ? ` · ${String(f.key_lane)}` : ""}.</p>}
            {f.type === "plugin" && <p className="muted small-text">{String(f.plugin)} (parámetros nativos: pedíselos al agente)</p>}
          </div>
        ))}
        <div className="insp-row">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Efecto para agregar">
            {Object.keys(NEW_FX).map((k) => (
              <option key={k} value={k}>
                {FX_NAME[k]}
              </option>
            ))}
          </select>
          <button type="button" className="btn small" onClick={() => apply({ ops: [{ op: "insert", path: `tracks.${id}.fx`, value: { id: freeFxId(fx, adding), ...NEW_FX[adding] } }], label: `agregar ${FX_NAME[adding]}`, track: id })}>
            Agregar efecto
          </button>
        </div>
        <p className="muted small-text">En la vista previa suenan al instante filtros, ganancia y sidechain; el resto llega en segundos con la versión del track con efectos.</p>
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ clips

function ClipPanel({ song, sel, apply }: { song: SongJson; sel: Extract<Selection, { kind: "clips" }>; apply: (e: Edit | null) => void }) {
  const editor = useEditorStore();
  const t = trackById(song, sel.track);
  const first = sel.items[0];
  const clip = t?.clips?.[first?.clip ?? -1];
  if (!t || !clip) return <p className="muted">Ese clip ya no existe.</p>;
  const ci = first.clip;
  const chop = chopById(song, clip.chop);
  const inst = first.instance && (clip.repeat ?? 1) > 1 ? first.instance : null;
  const o = inst ? (clip.instances?.[String(inst)] ?? {}) : null;
  const setC = (field: Parameters<typeof setClip>[3], v: unknown, label: string) => apply(setClip(song, sel.track, ci, field, v, label));
  const setChop = (path: string, v: unknown, label: string) => {
    if (!chop) return;
    const full = `chops.${chop.id}.${path}`;
    const cur = path.split(".").reduce<unknown>((acc, k) => (acc as Record<string, unknown> | undefined)?.[k], chop);
    apply({ ops: [{ op: "set", path: full, value: v, expect: cur ?? null } as Op], label, track: sel.track });
  };
  return (
    <>
      {inst && o && (
        <Section title={`Solo la repetición ${inst}`}>
          <Check label="Silenciada" value={!!o.mute} onChange={(v) => apply(setPass(song, sel.track, ci, inst, { mute: v }, v ? `silenciar la repetición ${inst}` : `hacer sonar la repetición ${inst}`))} testid="insp-pass-mute" />
          <Check label="Al revés" value={!!o.reverse} onChange={(v) => apply(setPass(song, sel.track, ci, inst, { reverse: v }, `dar vuelta la repetición ${inst}`))} testid="insp-pass-reverse" />
          <Num label="Volumen" unit="dB" value={o.gain_db ?? 0} min={-60} max={24} step={0.5} onCommit={(v) => apply(setPass(song, sel.track, ci, inst, { gain_db: v ?? 0 }, `volumen de la repetición ${inst}`))} />
          <Num label="Tono" unit="semitonos" value={o.pitch_semitones ?? 0} min={-24} max={24} step={1} onCommit={(v) => apply(setPass(song, sel.track, ci, inst, { pitch_semitones: v ?? 0 }, `tono de la repetición ${inst}`))} />
          <p className="muted small-text">El tono de una sola repetición se renderiza en segundos; mute, volumen y al revés son instantáneos.</p>
        </Section>
      )}
      <Section title="Clip">
        <Num label="Compás" value={clip.at_bar} min={1} max={song.meta.length_bars} step={1} onCommit={(v) => v != null && setC("at_bar", Math.round(v), "mover el clip")} testid="insp-clip-bar" />
        <Num label="Corrimiento" unit="beats" value={clip.offset_beats ?? 0} step={0.25} onCommit={(v) => setC("offset_beats", v || null, "correr el clip")} />
        <Num label="Repeticiones" value={clip.repeat ?? 1} min={1} max={4096} step={1} onCommit={(v) => v != null && setC("repeat", Math.round(v), "cambiar las repeticiones")} />
        <Num label="Largo de cada una" unit="beats" value={clip.length_beats ?? (chop ? (chop.length_beats - (clip.trim_start_beats ?? 0)) * (clip.stretch ?? 1) : null)} min={0.0625} step={0.25} onCommit={(v) => setC("length_beats", v, "cambiar el largo")} testid="insp-clip-length" />
        <Num label="Arranca dentro del material" unit="beats" value={clip.trim_start_beats ?? 0} step={0.25} onCommit={(v) => setC("trim_start_beats", v || null, "recortar el inicio")} />
        <Num label="Estirar" unit="× (sin cambiar el tono)" value={clip.stretch ?? 1} min={0.25} max={4} step={0.25} onCommit={(v) => setC("stretch", v === 1 ? null : v, "estirar el clip")} />
        <Num label="Volumen" unit="dB" value={clip.gain_db ?? 0} min={-60} max={24} step={0.5} onCommit={(v) => setC("gain_db", v || null, "volumen del clip")} />
        <Check label="Al revés" value={!!clip.reverse} onChange={(v) => setC("reverse", v || null, "dar vuelta el clip")} />
        <Num label="Fade in" unit="ms" value={clip.fade_in_ms ?? null} min={0} max={10000} step={10} onCommit={(v) => setC("fade_in_ms", v, "fade in")} />
        <Num label="Fade out" unit="ms" value={clip.fade_out_ms ?? null} min={0} max={10000} step={10} onCommit={(v) => setC("fade_out_ms", v, "fade out")} />
      </Section>
      {chop && (
        <Section title={`Chop ${chop.id} (todos sus clips)`} extra={<button type="button" className="btn small ghost" onClick={() => editor.openSource(chop.source)}>Ver en la fuente</button>}>
          <p className="muted small-text">
            Fuente {chop.source} · stem {chop.stem ?? "other"} · desde el compás {chop.start_downbeat} del disco
          </p>
          <Num label="Inicio (compás del disco)" value={chop.start_downbeat} min={1} step={1} onCommit={(v) => v != null && setChop("start_downbeat", Math.round(v), "mover el inicio del chop")} />
          <Num label="Corrimiento del inicio" unit="beats" value={chop.start_offset_beats ?? 0} step={0.25} onCommit={(v) => setChop("start_offset_beats", v ?? 0, "correr el inicio del chop")} />
          <Num label="Largo" unit="beats" value={chop.length_beats} min={0.25} max={128} step={1} onCommit={(v) => v != null && setChop("length_beats", v, "cambiar el largo del chop")} />
          <label className="insp-field">
            <span>Warp</span>
            <select value={chop.warp?.mode ?? "resample"} onChange={(e) => setChop("warp.mode", e.target.value, "cambiar el warp del chop")} aria-label="Modo de warp">
              <option value="resample">Vinilo (el tono sigue al tempo)</option>
              <option value="stretch">Estirar (mantiene el tono)</option>
            </select>
          </label>
          <Num label="Tono" unit="semitonos" value={chop.warp?.pitch_semitones ?? 0} min={-24} max={24} step={1} onCommit={(v) => setChop("warp.pitch_semitones", v ?? 0, "cambiar el tono del chop")} testid="insp-chop-pitch" />
          <Num label="Crossfade entre repeticiones" unit="ms" value={chop.fade_ms ?? 5} min={0} max={1000} step={1} onCommit={(v) => setChop("fade_ms", v ?? 5, "cambiar el crossfade")} />
        </Section>
      )}
    </>
  );
}

// ------------------------------------------------------------------ hits

function HitsPanel({ song, sel, apply, onAudition }: { song: SongJson; sel: Extract<Selection, { kind: "hits" }>; apply: (e: Edit | null) => void; onAudition: (s: string, g?: number) => void }) {
  const t = trackById(song, sel.track);
  const [browse, setBrowse] = useState(false);
  const hits = useMemo(() => (t ? patternHits(song, t) : []), [song, t]);
  if (!t?.pattern) return <p className="muted">Ese track ya no es de batería.</p>;
  const first = hits.find((h) => h.lane === sel.items[0].lane && Math.abs(h.beat0 - sel.items[0].beat0) < 1e-6);
  const lanes = [...new Set(sel.items.map((h) => h.lane))];
  const lane = lanes.length === 1 ? t.pattern.lanes[lanes[0]] : null;
  const vel = first?.velocity ?? 1;
  return (
    <>
      <Section title={sel.items.length === 1 ? `Golpe de ${laneName(sel.items[0].lane)}` : `${sel.items.length} golpes`}>
        {sel.items.length === 1 && first && (
          <p className="muted small-text">
            {beatLabel(first.beat0)} · {first.origin === "free" ? "golpe suelto (fuera de la grilla)" : first.origin === "override" ? "cambiado solo en este compás" : "del patrón (se repite)"}
          </p>
        )}
        <label className="insp-field">
          <span>Fuerza</span>
          <input type="range" min={0.05} max={1} step={0.01} value={vel} onChange={(e) => apply(setHitVelocity(song, sel.track, sel.items, Number(e.target.value)))} aria-label="Fuerza del golpe" data-testid="insp-hit-velocity" />
          <span className="mono">{Math.round(vel * 100)}%</span>
        </label>
        <p className="muted small-text">En la grilla la fuerza se guarda como x (100%), o (60%) o 1–9 (n/9).</p>
      </Section>
      {lane && (
        <Section title={`Carril ${laneName(lanes[0])} (todo el patrón)`}>
          <p className="mono small-text ellip" title={lane.sample}>
            {lane.sample.split("/").pop()}
          </p>
          <div className="insp-row">
            <button type="button" className="btn small" onClick={() => onAudition(lane.sample)}>
              <I.Play size={14} /> Escuchar
            </button>
            <button type="button" className="btn small" onClick={() => setBrowse(!browse)} data-testid="insp-change-sound" aria-expanded={browse}>
              Cambiar sonido
            </button>
          </div>
          {browse && <SoundBrowser lane={lanes[0]} current={lane.sample} onAudition={onAudition} onPick={(ref) => {
            apply({ ops: [{ op: "set", path: `tracks.${sel.track}.pattern.lanes.${lanes[0]}.sample`, value: ref, expect: lane.sample }], label: `cambiar el sonido de ${laneName(lanes[0])}`, track: sel.track });
            setBrowse(false);
          }} />}
          <Num label="Volumen del carril" unit="dB" value={lane.gain_db ?? 0} min={-60} max={24} step={0.5} onCommit={(v) => apply({ ops: [{ op: "set", path: `tracks.${sel.track}.pattern.lanes.${lanes[0]}.gain_db`, value: v ?? 0, expect: lane.gain_db ?? null }], label: `volumen de ${laneName(lanes[0])}`, track: sel.track })} />
          <Num label="Paneo del carril" value={lane.pan ?? 0} min={-1} max={1} step={0.05} onCommit={(v) => apply({ ops: [{ op: "set", path: `tracks.${sel.track}.pattern.lanes.${lanes[0]}.pan`, value: v ?? 0, expect: lane.pan ?? null }], label: `paneo de ${laneName(lanes[0])}`, track: sel.track })} />
        </Section>
      )}
    </>
  );
}

interface LibHit {
  uri: string;
  path?: string;
  type?: string;
  kit?: string;
  tags?: string[];
}

const LANE_TYPE: Record<string, string> = { kick: "kick", bd: "kick", snare: "snare", sd: "snare", clap: "clap", chat: "chat", ch: "chat", hat: "hat", hh: "chat", ohat: "ohat", oh: "ohat", ride: "cym", crash: "cym", tom: "tom", rim: "rim", perc: "perc" };

function SoundBrowser({ lane, current, onAudition, onPick }: { lane: string; current: string; onAudition: (s: string) => void; onPick: (ref: string) => void }) {
  const store = useStore();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<LibHit[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const type = LANE_TYPE[lane] ?? LANE_TYPE[lane.split(/[-_0-9]/)[0]];
  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const res = await store.client.tool<{ results?: LibHit[]; hits?: LibHit[] }>("library_search", { query: q || laneName(lane), ...(type ? { type } : {}), limit: 30, similar_to: q ? undefined : current });
        if (alive) setResults(res.results ?? res.hits ?? []);
      } catch (e) {
        try {
          const res = await store.client.tool<{ results?: LibHit[]; hits?: LibHit[] }>("library_search", { query: q || laneName(lane), limit: 30 });
          if (alive) setResults(res.results ?? res.hits ?? []);
        } catch {
          if (alive) setErr(e instanceof Error ? e.message : String(e));
        }
      }
    };
    const id = setTimeout(run, 250);
    return () => {
      alive = false;
      clearTimeout(id);
    };
  }, [q, lane, type, current, store]);
  return (
    <div className="sound-browser" data-testid="sound-browser">
      <input type="search" placeholder="Buscar en la librería (ej. punchy, 909, seco)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar sonidos" />
      {err && <p className="muted small-text">No pude buscar en la librería: {err}</p>}
      {results === null && !err && <p className="muted small-text">Buscando…</p>}
      {results && results.length === 0 && <p className="muted small-text">Nada parecido. Probá otra palabra.</p>}
      <ul>
        {(results ?? []).map((r) => (
          <li key={r.uri} className={r.uri === current ? "current" : ""}>
            <button type="button" className="icon-btn" onClick={() => onAudition(r.uri)} aria-label={`Escuchar ${r.uri}`} title="Escuchar">
              <I.Play size={13} />
            </button>
            <span className="ellip" title={r.uri}>
              {(r.path ?? r.uri).split("/").pop()}
              {r.kit && <span className="muted"> · {r.kit}</span>}
            </span>
            <button type="button" className="btn small" onClick={() => onPick(r.uri)} disabled={r.uri === current} data-testid="sound-use">
              Usar
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ notes / points

function NotesPanel({ song, sel }: { song: SongJson; sel: Extract<Selection, { kind: "notes" }> }) {
  const editor = useEditorStore();
  const t = trackById(song, sel.track);
  const notes = t ? trackNotes(song, t) : [];
  const n = notes.find((x) => x.beat === sel.items[0]?.beat && x.pitch === sel.items[0]?.pitch);
  if (!t || !n) return <p className="muted">Esa nota ya no existe.</p>;
  const update = (d: Parameters<typeof changeNotes>[3]) => {
    const e = changeNotes(song, sel.track, sel.items, d);
    if (e && editor.apply(e)) editor.select({ ...sel, items: sel.items.map((x) => ({ ...x, beat: x.beat + (d.beats ?? 0), pitch: x.pitch + (d.pitch ?? 0) })) });
  };
  return (
    <Section title={sel.items.length === 1 ? `Nota ${noteName(n.pitch)}` : `${sel.items.length} notas`}>
      {n.phrase && <p className="warn-text small-text">Es de la frase «{n.phrase}»: el cambio vale para todas sus repeticiones.</p>}
      <Num label="Altura" unit={noteName(n.pitch)} value={n.pitch} min={0} max={127} step={1} onCommit={(v) => v != null && update({ pitch: Math.round(v) - n.pitch })} testid="insp-note-pitch" />
      <Num label="Empieza" unit={`beat (compás ${Math.floor((n.beat - 1) / BEATS_PER_BAR) + 1})`} value={n.beat} min={1} step={0.25} onCommit={(v) => v != null && update({ beats: v - n.beat })} />
      <Num label="Largo" unit="beats" value={n.length} min={0.0625} step={0.25} onCommit={(v) => v != null && update({ length: v - n.length })} />
      <Num label="Fuerza" unit="1–127" value={n.velocity} min={1} max={127} step={1} onCommit={(v) => v != null && update({ velocity: v })} />
      <p className="muted small-text">Cambiar notas re-renderiza el instrumento (segundos); mientras, suena la versión anterior.</p>
    </Section>
  );
}

function PointsPanel({ song, sel }: { song: SongJson; sel: Extract<Selection, { kind: "points" }> }) {
  const editor = useEditorStore();
  const a = song.automation?.find((x) => x.target === sel.target);
  const i = sel.items[0];
  const p = a?.points[i];
  if (!a || !p) return <p className="muted">Ese punto ya no existe.</p>;
  const unit = sel.target.endsWith("_hz") ? "Hz" : sel.target.endsWith("_db") ? "dB" : "";
  const set = (bar: number, value: number) => editor.apply({ ops: [{ op: "set", path: `automation.${sel.target}.points[${i}]`, value: [round6(bar), round6(value)], expect: p }], label: "ajustar un punto de automatización" });
  return (
    <Section title={`Automatización · ${sel.target.split(".").slice(2).join(".")}`}>
      <Num label="Compás" value={p[0]} min={1} max={song.meta.length_bars + 1} step={0.25} onCommit={(v) => v != null && set(v, p[1])} />
      <Num label="Valor" unit={unit} value={p[1]} step={unit === "Hz" ? 50 : 0.1} onCommit={(v) => v != null && set(p[0], v)} />
      <label className="insp-field">
        <span>Curva</span>
        <select value={a.curve ?? "lin"} onChange={(e) => editor.apply({ ops: [{ op: "set", path: `automation.${sel.target}.curve`, value: e.target.value, expect: a.curve ?? "lin" }], label: "cambiar la curva" })}>
          <option value="lin">Lineal</option>
          <option value="exp">Exponencial</option>
          <option value="step">Escalones</option>
        </select>
      </label>
    </Section>
  );
}

// ------------------------------------------------------------------ ask

function AskBox({ askRef }: { askRef: RefObject<HTMLTextAreaElement | null> }) {
  const editor = useEditorStore();
  const song = useEditor((s) => s.song)!;
  const sel = useEditor((s) => s.selection);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const pending = useApp((s) => s.feedback).filter((e) => e.type === "comment" && e.selection).length;
  const disabled = sel.kind === "none";
  const send = async () => {
    if (!text.trim() || disabled) return;
    setSending(true);
    const ok = await editor.ask(text);
    setSending(false);
    if (ok) setText("");
  };
  return (
    <div className="ask-box">
      <label htmlFor="ed-ask">
        <strong>Pedile al agente</strong>
        <span className="muted"> {disabled ? "· seleccioná algo primero" : `· sobre: ${selectionLabel(song, sel)}`}</span>
      </label>
      <textarea
        id="ed-ask"
        ref={askRef}
        rows={2}
        placeholder={disabled ? "Seleccioná un golpe, un clip, notas o una zona…" : "ej. sacá esto · que esto suene más filtrado · repetí este corte pero al revés"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void send();
          }
          if (e.key === "Escape") (e.target as HTMLTextAreaElement).blur();
        }}
        disabled={disabled}
        data-testid="ed-ask"
      />
      <div className="insp-row">
        <span className="muted small-text">{pending ? `${pending} pedido${pending === 1 ? "" : "s"} con selección en el feedback` : "Queda en el feedback, apuntando exactamente a esto."}</span>
        <span className="header-spacer" />
        <button type="button" className="btn small primary" onClick={() => void send()} disabled={disabled || !text.trim() || sending} data-testid="ed-ask-send">
          {sending ? "Enviando…" : "Enviar"}
        </button>
      </div>
    </div>
  );
}

export { trackKind };
