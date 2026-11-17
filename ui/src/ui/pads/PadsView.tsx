// The Pads panel of the Arreglo view (WP17): a 3x8 pad grid on the computer
// keyboard (by physical key), mouse and multi-touch; banks; the recorder
// (loop overdub / count-in, metronome, quantise); takes; the latency
// indicator and calibration. Pads light from the key handler directly (no
// React on the audio path); this component only renders state.

import { useEffect, useMemo, useState } from "react";
import { useEditor, useEditorStore } from "../../editor/react";
import { trackKind, type SongJson } from "../../editor/song";
import { capLabel, ROWS } from "../../pads/keymap";
import { GRID_LABEL, type PadJson, type QuantizeGrid, type QuantizeJson } from "../../pads/model";
import { usePads, usePadsStore } from "../../pads/react";
import type { TakeSummary } from "../../pads/store";
import { useApp, useStore } from "../../state/react";
import * as I from "../icons";
import { SoundBrowser } from "../editor/Inspector";
import { laneSoundLabel } from "../../editor/labels";

const GRIDS: QuantizeGrid[] = ["off", "1/16", "1/8", "1/8t", "1/16t"];

export function PadsPanel() {
  const pads = usePadsStore();
  const status = usePads((s) => s.status);
  const error = usePads((s) => s.error);
  const on = usePads((s) => s.on);
  const [help, setHelp] = useState(false);
  const [calib, setCalib] = useState(false);
  useEffect(() => {
    void pads.init();
  }, [pads]);
  if (status === "unsupported") return <div className="card pads-panel muted">Este servidor no tiene pads (actualizá muestra).</div>;
  if (status === "error") return <div className="card pads-panel" role="alert">{error}</div>;
  return (
    <section className={`card pads-panel ${on ? "on" : "off"}`} aria-label="Pads" data-testid="pads-panel">
      <PadsBar onHelp={() => setHelp(!help)} onCalib={() => setCalib(true)} />
      {help && <PadsHelp />}
      <div className="pads-body">
        <PadGrid />
        <PadEditor />
      </div>
      <Takes />
      {calib && <Calibration onClose={() => setCalib(false)} />}
    </section>
  );
}

function PadsBar({ onHelp, onCalib }: { onHelp: () => void; onCalib: () => void }) {
  const pads = usePadsStore();
  const on = usePads((s) => s.on);
  const banks = usePads((s) => s.banks);
  const rec = usePads((s) => s.rec);
  const metronome = usePads((s) => s.metronome);
  const countIn = usePads((s) => s.countIn);
  const q = usePads((s) => s.quantize);
  const lat = usePads((s) => s.latency);
  const loop = useEditor((s) => s.loop);
  useEffect(() => {
    const id = setInterval(() => pads.refreshLatency(), 2000);
    return () => clearInterval(id);
  }, [pads]);
  const recLabel = rec.phase === "countin" ? "Cuenta…" : rec.phase === "recording" ? `Grabando${loop ? ` · pasada ${rec.cycle}` : ""} · ${rec.events}` : "Grabar";
  return (
    <div className="pads-bar" role="toolbar" aria-label="Pads">
      <button type="button" className={`btn small pads-power ${on ? "primary" : ""}`} aria-pressed={on} onClick={() => pads.setOn(!on)} data-testid="pads-power" title={on ? "El teclado toca los pads (Esc lo apaga)" : "Activar el teclado para los pads"}>
        <span className={`dot ${on ? "ok" : ""}`} /> Teclado {on ? "ON" : "OFF"}
      </button>
      <span className="seg" role="group" aria-label="Bancos">
        {banks.banks.map((b, i) => (
          <button key={b.id} type="button" className="btn small" aria-pressed={b.id === banks.active_bank} onClick={() => pads.setBank(i)} title={`${b.name} (tecla ${i + 1})`} data-testid={`pads-bank-${b.id}`}>
            {b.id.toUpperCase()}
          </button>
        ))}
      </span>
      <button type="button" className={`btn small pads-rec ${rec.phase !== "idle" ? "recording" : ""}`} onClick={() => void pads.toggleRecord()} data-testid="pads-rec" title={loop ? "Grabar sobre el loop: cada vuelta suma (Enter)" : "Grabar (Enter). Con el transporte parado, cuenta antes de arrancar"}>
        <span className="rec-dot" aria-hidden="true" /> {recLabel}
      </button>
      <button type="button" className="btn small" onClick={() => void pads.undoPass()} title="Deshacer la última pasada (⌫)" disabled={rec.phase === "idle" && !pads.state.selectedTake}>
        ↶ Pasada
      </button>
      <label className="pads-opt" title="Metrónomo (0)">
        <input type="checkbox" checked={metronome} onChange={(e) => pads.setMetronome(e.target.checked)} /> Metrónomo
      </label>
      <label className="pads-opt" title="Cuenta antes de grabar con el transporte parado">
        Cuenta
        <select value={countIn} onChange={(e) => pads.setCountIn(Number(e.target.value) as 0 | 1 | 2)} data-testid="pads-countin">
          <option value={0}>no</option>
          <option value={1}>1 compás</option>
          <option value={2}>2 compases</option>
        </select>
      </label>
      <QuantizeControls value={q} onChange={(v) => pads.setQuantize(v)} label="Cuantizar tomas nuevas" />
      <span className="ed-spacer" />
      <button type="button" className={`pill lat-${lat.level}`} onClick={onCalib} title={latencyTitle(lat)} data-testid="pads-latency">
        <span className={`dot ${lat.level === "ok" ? "ok" : lat.level === "warn" ? "warn" : "err"}`} /> {Math.round(lat.outputMs + lat.baseMs)} ms{lat.calibratedMs != null ? " · calibrado" : ""}
      </button>
      <button type="button" className="icon-btn" onClick={onHelp} aria-label="Ayuda de los pads" title="Cómo se tocan">
        ?
      </button>
      {lat.level === "bad" && (
        <p className="pads-warn" role="status">
          {lat.text}
        </p>
      )}
    </div>
  );
}

function latencyTitle(l: ReturnType<typeof usePadsStore>["state"]["latency"]): string {
  return [
    l.text,
    `Salida (outputLatency): ${l.outputMs} ms · base: ${l.baseMs} ms · demora de la base por el limitador: ${l.busMs} ms`,
    l.schedP50 != null ? `Tecla → sonido programado: mediana ${l.schedP50} ms, p95 ${l.schedP95} ms, máx ${l.schedMax} ms` : "Tocá unos pads para medir tecla → sonido.",
    l.calibratedMs != null ? `Calibración de esta salida: ${l.calibratedMs} ms` : "Sin calibrar: clic para calibrar.",
  ].join("\n");
}

export function QuantizeControls({ value, onChange, label }: { value: QuantizeJson; onChange: (q: QuantizeJson) => void; label: string }) {
  return (
    <span className="pads-q" role="group" aria-label={label}>
      <select value={value.grid} onChange={(e) => onChange({ ...value, grid: e.target.value as QuantizeGrid })} aria-label="Grilla de cuantización" data-testid="pads-q-grid">
        {GRIDS.map((g) => (
          <option key={g} value={g}>
            {g === "off" ? "Libre" : `Cuant. ${GRID_LABEL[g]}`}
          </option>
        ))}
      </select>
      {value.grid !== "off" && (
        <>
          <label title="Fuerza: cuánto se acerca cada golpe a la grilla">
            <input type="range" min={0} max={100} step={5} value={Math.round(value.strength * 100)} onChange={(e) => onChange({ ...value, strength: Number(e.target.value) / 100 })} aria-label="Fuerza de la cuantización" />
            <span className="mono">{Math.round(value.strength * 100)}%</span>
          </label>
          {(value.grid === "1/16" || value.grid === "1/8") && (
            <label title="Swing: atrasa los contratiempos (100% = tresillo)">
              sw
              <input type="range" min={0} max={100} step={5} value={Math.round(value.swing * 100)} onChange={(e) => onChange({ ...value, swing: Number(e.target.value) / 100 })} aria-label="Swing" />
              <span className="mono">{Math.round(value.swing * 100)}%</span>
            </label>
          )}
        </>
      )}
    </span>
  );
}

function PadsHelp() {
  return (
    <div className="pads-help small-text">
      <p>
        <b>Tocar:</b> las tres filas de letras (Z…, A…, Q…) son los pads, por posición: funciona igual en teclados QWERTY, AZERTY o QWERTZ. <kbd>⇧</kbd> mientras tocás = acento (más fuerte). También con el mouse o con varios dedos en la pantalla.
      </p>
      <p>
        <kbd>1</kbd>–<kbd>4</kbd> banco · <kbd>Espacio</kbd> play/pausa · <kbd>Enter</kbd> grabar/parar · <kbd>⌫</kbd> deshacer la última pasada · <kbd>0</kbd> metrónomo · <kbd>Esc</kbd> apaga el teclado de los pads (vuelven los atajos del editor).
      </p>
      <p>
        <b>Grabar:</b> con un loop marcado, cada vuelta suma lo que tocás (como un looper). Parado, cuenta 1 o 2 compases y arranca. Lo que tocás se guarda tal cual en la toma; la cuantización se puede cambiar después sin perder lo tocado.
      </p>
      <p className="muted">
        Ojo: muchos teclados de notebook no registran 3 o más teclas apretadas a la vez en ciertas combinaciones («ghosting»). Si un golpe no suena, probá otras teclas para ese pad o tocá en la pantalla. Auriculares Bluetooth agregan mucha demora: para tocar a tiempo usá cable o los parlantes.
      </p>
    </div>
  );
}

function PadGrid() {
  const pads = usePadsStore();
  const banks = usePads((s) => s.banks);
  const selected = usePads((s) => s.selected);
  const missing = usePads((s) => s.missing);
  const layout = usePads((s) => s.layout);
  const bank = banks.banks.find((b) => b.id === banks.active_bank) ?? banks.banks[0];
  const byKey = useMemo(() => new Map(bank.pads.map((p) => [p.key, p])), [bank]);
  return (
    <div className="pad-grid" role="group" aria-label={`Pads del ${bank.name}`} data-testid="pad-grid">
      {ROWS.map((row, ri) => (
        <div className="pad-row" key={ri}>
          {row.map((code) => {
            const p = byKey.get(code);
            return (
              <button
                key={code}
                type="button"
                ref={(el) => pads.registerPad(code, el)}
                className={`pad ${p ? "" : "empty"} ${selected === code ? "sel" : ""} ${p && missing.includes(code) ? "loading" : ""}`}
                style={p?.color ? ({ "--pad": p.color } as React.CSSProperties) : undefined}
                data-testid={`pad-${code}`}
                aria-label={`${capLabel(code, layout)}: ${p ? p.name : "vacío"}`}
                onPointerDown={(e) => {
                  e.preventDefault();
                  (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
                  pads.select(code);
                  if (p) pads.press(code, e.shiftKey, e.timeStamp);
                }}
                onPointerUp={(e) => p && pads.release(code, e.timeStamp)}
                onPointerCancel={(e) => p && pads.release(code, e.timeStamp)}
                onContextMenu={(e) => e.preventDefault()}
              >
                <kbd className="cap">{capLabel(code, layout)}</kbd>
                <span className="pname">{p ? p.name : "+"}</span>
                {p && (
                  <span className="pmeta">
                    {p.mode === "gate" ? "mantener" : ""}
                    {p.choke ? ` · corte ${p.choke}` : ""}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function PadEditor() {
  const pads = usePadsStore();
  const editor = useEditorStore();
  const store = useStore();
  const selected = usePads((s) => s.selected);
  usePads((s) => s.banks);
  const song = useEditor((s) => s.song);
  const [mode, setMode] = useState<"none" | "library" | "kit" | "slice" | "track" | "scale">("none");
  const p = selected ? pads.padAt(selected) : undefined;
  const audition = (uri: string) => void editor.soundBuffer({ sample: uri }).then((b) => b && editor.engine.playOneShot(b));
  if (!song) return null;
  return (
    <div className="pad-editor" data-testid="pad-editor">
      <div className="insp-row wrap">
        <span className="small-text muted">Cargar en el banco:</span>
        <button type="button" className="btn small" aria-pressed={mode === "kit"} onClick={() => setMode(mode === "kit" ? "none" : "kit")} data-testid="pads-kit">
          Un kit entero
        </button>
        <button type="button" className="btn small" aria-pressed={mode === "slice"} onClick={() => setMode(mode === "slice" ? "none" : "slice")} data-testid="pads-slice">
          Cortar un chop
        </button>
        <button type="button" className="btn small" aria-pressed={mode === "track"} onClick={() => setMode(mode === "track" ? "none" : "track")} data-testid="pads-from-track">
          Desde un track
        </button>
        <button type="button" className="btn small" aria-pressed={mode === "scale"} onClick={() => setMode(mode === "scale" ? "none" : "scale")}>
          Notas de un instrumento
        </button>
      </div>
      {mode === "kit" && <KitPicker onDone={() => setMode("none")} />}
      {mode === "slice" && <ChopSlicer song={song} onDone={() => setMode("none")} />}
      {mode === "track" && (
        <TrackPicker song={song} kind="pattern" label="Armar pads desde" onPick={(id) => {
          const n = pads.fromTrack(song, id);
          store.toast(`Cargué ${n} sonidos de «${id}» en los pads.`, "ok", undefined, 2500);
          setMode("none");
        }} />
      )}
      {mode === "scale" && (
        <TrackPicker song={song} kind="instrument" label={`Escala de ${song.meta.key} en la fila del medio para`} onPick={(id) => {
          const n = pads.scalePads(song, id);
          store.toast(n ? `Cargué ${n} notas de ${id} (escala de ${song.meta.key}) en la fila A–K.` : "Ese track no es un instrumento.", n ? "ok" : "warn", undefined, 3000);
          setMode("none");
        }} />
      )}
      {selected && !p && mode === "none" && (
        <div>
          <p className="small-text">Pad {selected.replace("Key", "")} vacío: elegí un sonido de la librería.</p>
          <SoundBrowser lane="kick" current="" onAudition={(s) => audition(s)} onPick={(ref) => pads.assignSample(selected, ref)} />
        </div>
      )}
      {p && mode === "none" && <PadFields pad={p} />}
      {!selected && mode === "none" && <p className="muted small-text">Tocá un pad para editarlo. Un pad vacío se llena desde la librería; o cargá un kit, cortá un chop o copiá un track entero.</p>}
    </div>
  );
}

function PadFields({ pad }: { pad: PadJson }) {
  const pads = usePadsStore();
  const editor = useEditorStore();
  const [browse, setBrowse] = useState(false);
  const set = (patch: Partial<PadJson>) => pads.setPad(pad.key, patch);
  const audition = (uri: string) => void editor.soundBuffer({ sample: uri }).then((b) => b && editor.engine.playOneShot(b));
  const sound = pad.note ? `notas ${pad.note.pitches.join(", ")} de ${pad.note.track}` : laneSoundLabel({ sample: pad.sample, slice: pad.slice });
  return (
    <div className="pad-fields">
      <div className="insp-row">
        <input className="pad-name" value={pad.name} onChange={(e) => set({ name: e.target.value.slice(0, 40) || pad.name })} aria-label="Nombre del pad" />
        <input type="color" value={pad.color ?? "#7aa2ff"} onChange={(e) => set({ color: e.target.value })} aria-label="Color del pad" />
        <button type="button" className="btn small ghost" onClick={() => pads.clearPad(pad.key)}>
          Vaciar
        </button>
      </div>
      <p className="mono small-text ellip" title={pad.sample ?? ""}>
        {sound}
      </p>
      {!pad.note && (
        <button type="button" className="btn small" onClick={() => setBrowse(!browse)} aria-expanded={browse}>
          Cambiar sonido
        </button>
      )}
      {browse && <SoundBrowser lane={pad.lane ?? pad.name} current={pad.sample ?? ""} onAudition={(s) => audition(s)} onPick={(ref) => { pads.assignSample(pad.key, ref, pad.name); setBrowse(false); }} />}
      <div className="pad-knobs">
        <Range label="Volumen" unit="dB" min={-24} max={12} step={0.5} value={pad.gain_db ?? 0} onChange={(v) => set({ gain_db: v })} />
        <Range label="Paneo" min={-1} max={1} step={0.05} value={pad.pan ?? 0} onChange={(v) => set({ pan: v })} />
        <Range label="Afinación" unit="st" min={-24} max={24} step={1} value={pad.pitch_semitones ?? 0} onChange={(v) => set({ pitch_semitones: v })} />
        <Range label="Fuerza normal" unit="%" min={5} max={100} step={5} value={Math.round((pad.velocity ?? 0.8) * 100)} onChange={(v) => set({ velocity: v / 100 })} />
        <Range label="Acento (⇧)" unit="%" min={5} max={100} step={5} value={Math.round((pad.accent ?? 1) * 100)} onChange={(v) => set({ accent: v / 100 })} />
      </div>
      <div className="insp-row wrap">
        <label className="pads-opt">
          <input type="checkbox" checked={!!pad.reverse} onChange={(e) => set({ reverse: e.target.checked })} disabled={!!pad.note} /> Al revés
        </label>
        <label className="pads-opt" title="Mantener: suena mientras apretás (se graba cuánto)">
          <input type="checkbox" checked={pad.mode === "gate"} onChange={(e) => set({ mode: e.target.checked ? "gate" : "oneshot" })} /> Mantener para sonar
        </label>
        <label className="pads-opt" title="Pads del mismo grupo se cortan entre sí (hi-hat abierto/cerrado, slices)">
          Grupo de corte
          <select value={pad.choke ?? 0} onChange={(e) => set({ choke: Number(e.target.value) || null })}>
            <option value={0}>ninguno</option>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}

function Range({ label, unit, min, max, step, value, onChange }: { label: string; unit?: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }) {
  const [drag, setDrag] = useState<number | null>(null);
  const v = drag ?? value;
  const commit = () => {
    if (drag != null && drag !== value) onChange(drag);
    setDrag(null);
  };
  return (
    <label className="insp-field">
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={v} onChange={(e) => setDrag(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} aria-label={label} />
      <span className="mono">
        {+v.toFixed(2)}
        {unit ? ` ${unit}` : ""}
      </span>
    </label>
  );
}

function KitPicker({ onDone }: { onDone: () => void }) {
  const store = useStore();
  const pads = usePadsStore();
  const [kits, setKits] = useState<{ name: string; description: string; available: boolean }[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void store.client
      .tool<{ kits: { name: string; description: string; available: boolean }[] }>("library_kits", {})
      .then((r) => setKits(r.kits ?? []))
      .catch(() => setKits([]));
  }, [store]);
  if (!kits) return <p className="muted small-text">Buscando kits…</p>;
  if (!kits.length) return <p className="muted small-text">No encontré kits de inicio en la librería.</p>;
  return (
    <ul className="pads-list">
      {kits.map((k) => (
        <li key={k.name}>
          <span className="ellip" title={k.description}>
            <b>{k.name}</b> <span className="muted small-text">{k.description.slice(0, 90)}</span>
          </span>
          <button type="button" className="btn small" disabled={busy || !k.available} data-testid={`pads-kit-${k.name}`} onClick={async () => {
            setBusy(true);
            try {
              const n = await pads.dropKit(k.name);
              store.toast(`Kit ${k.name}: ${n} sonidos en el banco (bombo, caja, hats abajo).`, "ok", undefined, 2500);
              onDone();
            } catch (e) {
              store.toast(`No pude cargar el kit: ${String(e)}`, "warn");
            } finally {
              setBusy(false);
            }
          }}>
            Cargar
          </button>
        </li>
      ))}
    </ul>
  );
}

export function SliceOptions({ onSlice, busy }: { onSlice: (by: "beat" | "half" | "transients", speed: "project" | "source") => void; busy: boolean }) {
  const [by, setBy] = useState<"beat" | "half" | "transients">("beat");
  const [speed, setSpeed] = useState<"project" | "source">("project");
  return (
    <span className="insp-row wrap">
      <select value={by} onChange={(e) => setBy(e.target.value as typeof by)} aria-label="Cómo cortar" data-testid="slice-by">
        <option value="beat">un pad por tiempo</option>
        <option value="half">un pad por medio tiempo</option>
        <option value="transients">en cada golpe (transitorios)</option>
      </select>
      <select value={speed} onChange={(e) => setSpeed(e.target.value as typeof speed)} aria-label="Velocidad">
        <option value="project">al tempo del proyecto (como el chop)</option>
        <option value="source">a la velocidad del disco</option>
      </select>
      <button type="button" className="btn small primary" disabled={busy} onClick={() => onSlice(by, speed)} data-testid="slice-go">
        Cortar en pads
      </button>
    </span>
  );
}

function ChopSlicer({ song, onDone }: { song: SongJson; onDone: () => void }) {
  const pads = usePadsStore();
  const store = useStore();
  const chops = song.chops ?? [];
  const [chop, setChop] = useState(chops[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const selected = usePads((s) => s.selected);
  if (!chops.length) return <p className="muted small-text">La canción no tiene chops. Abrí una fuente (en el editor) y marcá una región: «Cortar en pads».</p>;
  return (
    <div className="insp-row wrap">
      <select value={chop} onChange={(e) => setChop(e.target.value)} aria-label="Chop" data-testid="slice-chop">
        {chops.map((c) => (
          <option key={c.id} value={c.id}>
            {c.id} ({c.length_beats} tiempos)
          </option>
        ))}
      </select>
      <SliceOptions busy={busy} onSlice={async (by, speed) => {
        setBusy(true);
        try {
          const n = await pads.sliceToPads({ chop }, by, speed, selected ?? undefined, chop);
          store.toast(`Corté ${chop} en ${n} pads (cada uno corta al anterior).`, "ok", undefined, 2500);
          onDone();
        } catch (e) {
          store.toast(`No pude cortar: ${String(e)}`, "warn");
        } finally {
          setBusy(false);
        }
      }} />
    </div>
  );
}

function TrackPicker({ song, kind, label, onPick }: { song: SongJson; kind: "pattern" | "instrument"; label: string; onPick: (id: string) => void }) {
  const tracks = (song.tracks ?? []).filter((t) => trackKind(t) === kind && (kind !== "instrument" || !t.midi));
  if (!tracks.length) return <p className="muted small-text">{kind === "pattern" ? "No hay tracks de batería." : "No hay instrumentos con notas (los que leen un MIDI no sirven)."}</p>;
  return (
    <div className="insp-row wrap">
      <span className="small-text">{label}:</span>
      {tracks.map((t) => (
        <button key={t.id} type="button" className="btn small" onClick={() => onPick(t.id)} data-testid={`pads-track-${t.id}`}>
          {t.id}
        </button>
      ))}
    </div>
  );
}

function Takes() {
  const pads = usePadsStore();
  const takes = usePads((s) => s.takes);
  const selected = usePads((s) => s.selectedTake);
  const auditioning = usePads((s) => s.auditioning);
  const notice = usePads((s) => s.notice);
  if (!takes.length)
    return (
      <div className="pads-takes muted small-text">
        {notice ?? "Todavía no grabaste nada. Marcá un loop (L) y apretá Enter: lo que toques queda guardado como una toma."}
      </div>
    );
  return (
    <div className="pads-takes" data-testid="pads-takes">
      <h3 className="small-text">Tomas</h3>
      <ul className="pads-list">
        {[...takes].reverse().map((t) => (
          <li key={t.id} className={selected === t.id ? "sel" : ""} data-testid={`take-${t.id}`}>
            <button type="button" className="icon-btn" onClick={() => (auditioning === t.id ? pads.stopAudition() : void pads.audition(t.id))} aria-label={auditioning === t.id ? "Dejar de escuchar" : `Escuchar ${t.name} sobre la base`} title="Escuchar sobre la base" data-testid={`take-play-${t.id}`}>
              {auditioning === t.id ? <I.Pause size={14} /> : <I.Play size={14} />}
            </button>
            <button type="button" className="take-name" onClick={() => pads.selectTake(selected === t.id ? null : t.id)}>
              <b>{t.name}</b>
              <span className="muted small-text">
                {" "}
                {t.n_events} golpes · {t.loop_beats ? `loop ${t.loop_beats / 4} compases` : `${Math.ceil(t.length_beats / 4)} compases`}
                {t.quantize.grid !== "off" ? ` · ${GRID_LABEL[t.quantize.grid]}` : ""}
                {t.status === "recording" ? " · grabando" : ""}
                {t.commits ? ` · usada ${t.commits}×` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {selected && <TakeDetail key={selected} take={takes.find((t) => t.id === selected)} />}
    </div>
  );
}

function TakeDetail({ take }: { take: TakeSummary | undefined }) {
  const pads = usePadsStore();
  const editor = useEditorStore();
  const song = useEditor((s) => s.song);
  const [name, setName] = useState(take?.name ?? "");
  const [target, setTarget] = useState<"new_track" | "track">("new_track");
  const drumTracks = (song?.tracks ?? []).filter((t) => trackKind(t) === "pattern");
  const [track, setTrack] = useState(drumTracks[0]?.id ?? "");
  const startBar = take ? Math.floor(take.origin_beat / 4) + 1 : 1;
  const [atBar, setAtBar] = useState(startBar);
  const [how, setHow] = useState<"repeat" | "until">("repeat");
  const [repeat, setRepeat] = useState(1);
  const [until, setUntil] = useState((song?.meta.length_bars ?? 8) + 1);
  const [ask, setAsk] = useState("");
  if (!take || !song) return null;
  return (
    <div className="take-detail" data-testid="take-detail">
      <div className="insp-row wrap">
        <input value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name !== take.name && void pads.renameTake(take.id, name)} aria-label="Nombre de la toma" className="pad-name" />
        <QuantizeControls value={take.quantize} onChange={(q) => void pads.setTakeQuantize(take.id, q)} label="Cuantización de esta toma" />
        <button type="button" className="btn small ghost" onClick={() => void pads.deleteTake(take.id)}>
          Borrar
        </button>
      </div>
      <div className="insp-row wrap take-use">
        <b className="small-text">Usar en la canción:</b>
        <select value={target} onChange={(e) => setTarget(e.target.value as typeof target)} aria-label="Destino" data-testid="take-target">
          <option value="new_track">en una pista nueva de pads</option>
          {drumTracks.length > 0 && <option value="track">sumada a un track de batería</option>}
        </select>
        {target === "track" && (
          <select value={track} onChange={(e) => setTrack(e.target.value)} aria-label="Track de batería">
            {drumTracks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id}
              </option>
            ))}
          </select>
        )}
        <label className="pads-opt">
          desde el compás <input type="number" min={1} max={song.meta.length_bars} value={atBar} onChange={(e) => setAtBar(Math.max(1, Math.min(song.meta.length_bars, Number(e.target.value) || 1)))} data-testid="take-at-bar" />
        </label>
        <select value={how} onChange={(e) => setHow(e.target.value as typeof how)} aria-label="Cuántas veces">
          <option value="repeat">repetida</option>
          <option value="until">en loop hasta el compás</option>
        </select>
        {how === "repeat" ? (
          <label className="pads-opt">
            <input type="number" min={1} max={256} value={repeat} onChange={(e) => setRepeat(Math.max(1, Number(e.target.value) || 1))} data-testid="take-repeat" /> {repeat === 1 ? "vez" : "veces"}
          </label>
        ) : (
          <input type="number" min={atBar + 1} max={song.meta.length_bars + 1} value={until} onChange={(e) => setUntil(Number(e.target.value) || atBar + 1)} aria-label="Hasta el compás" />
        )}
        <button type="button" className="btn small primary" data-testid="take-commit" onClick={() => void pads.commit(take.id, { target, track: target === "track" ? track : undefined, at_bar: atBar, ...(how === "repeat" ? { repeat } : { until_bar: until }) })}>
          Poner en la canción
        </button>
      </div>
      <div className="insp-row wrap">
        <input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Pedile al agente algo sobre esta toma (ej. limpiame el ritmo)" aria-label="Pedido al agente sobre la toma" className="take-ask" />
        <button type="button" className="btn small" disabled={!ask.trim()} onClick={async () => {
          if (await editor.askAbout(ask, { label: `toma ${take.name}`, take: take.id })) setAsk("");
        }}>
          Pedile al agente
        </button>
      </div>
    </div>
  );
}

function Calibration({ onClose }: { onClose: () => void }) {
  const pads = usePadsStore();
  const calib = usePads((s) => s.calib);
  const lat = usePads((s) => s.latency);
  const dark = useApp((s) => s.theme);
  void dark;
  return (
    <div className="overlay pads-overlay-ok" onClick={onClose}>
      <div className="dialog pads-calib" role="dialog" aria-modal="true" aria-label="Calibrar la demora" onClick={(e) => e.stopPropagation()}>
        <h2>Demora del audio</h2>
        <p className="small-text">{lat.text}</p>
        <ul className="small-text mono">
          <li>Salida del navegador (outputLatency): {lat.outputMs} ms · base: {lat.baseMs} ms</li>
          <li>Tecla → sonido programado: {lat.schedP50 != null ? `${lat.schedP50} ms (p95 ${lat.schedP95} ms)` : "tocá unos pads para medirlo"}</li>
          <li>Calibración de esta salida: {lat.calibratedMs != null ? `${lat.calibratedMs} ms` : "ninguna"}</li>
        </ul>
        <p className="small-text">
          Calibrar: vas a escuchar 16 clics. Apretá <kbd>Espacio</kbd> (o el botón) justo con cada clic, como si tocaras. Mido cuánto llega tarde lo que escuchás y lo descuento al grabar, para que cada golpe quede donde lo tocaste. Se guarda para esta salida de audio (si cambiás de auriculares, calibrá de nuevo).
        </p>
        {calib.phase === "running" ? (
          <button type="button" className="btn primary calib-tap" onPointerDown={(e) => pads.calibTap(e.timeStamp)} data-testid="calib-tap">
            ¡Ahora! ({calib.taps}/{calib.total})
          </button>
        ) : (
          <div className="insp-row">
            <button type="button" className="btn primary" onClick={() => { pads.setOn(true); pads.startCalibration(); }} data-testid="calib-start">
              Empezar
            </button>
            {lat.calibratedMs != null && (
              <button type="button" className="btn small ghost" onClick={() => pads.resetCalibration()}>
                Olvidar la calibración
              </button>
            )}
            <button type="button" className="btn small" onClick={onClose}>
              Cerrar
            </button>
          </div>
        )}
        {calib.phase === "done" && (
          <p className="small-text" role="status">
            Listo: {calib.resultMs} ms (variación ±{calib.spreadMs} ms en {calib.taps} toques). {(calib.spreadMs ?? 0) > 30 ? "Variaste bastante: si podés, repetila." : ""}
          </p>
        )}
        {calib.phase === "failed" && <p className="small-text" role="status">No alcanzaron los toques cerca de los clics. Probá de nuevo.</p>}
      </div>
    </div>
  );
}
