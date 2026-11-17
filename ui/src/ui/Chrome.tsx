// Header, jobs, banners, toasts, dialogs and empty states.

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Job } from "../api/types";
import { copyText } from "../hooks/hooks";
import { regenRequest } from "../text/es";
import { useApp, useStore } from "../state/react";
import * as I from "./icons";
import { SHORTCUTS } from "./keys";

const TOOL_LABEL: Record<string, string> = {
  "source.separate": "Separando stems",
  "source.analyze": "Analizando la fuente",
  "source.find_chops": "Buscando candidatos de chop",
  "source.import": "Importando audio",
  render: "Renderizando versión",
  preview: "Renderizando preview",
  "ears.analyze": "Calculando métricas",
  "library.index": "Indexando la librería",
};

export function Header({ onHelp, onSettings }: { onHelp: () => void; onSettings: () => void }) {
  const store = useStore();
  const title = useApp((s) => s.title);
  const ws = useApp((s) => s.ws);
  const theme = useApp((s) => s.theme);
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark">muestra</span>
        {title && <span className="brand-title">{title}</span>}
      </div>
      <span className="header-spacer" />
      {ws === "open" ? (
        <span className="pill" title="Conectado: las versiones nuevas aparecen solas" data-testid="ws-status">
          <span className="dot ok" />
          <span className="hide-narrow">en vivo</span>
        </span>
      ) : (
        <button type="button" className="pill" onClick={() => store.reconnect()} title="Sin conexión en vivo con la API. Reintento solo; tocá para reintentar ya." data-testid="ws-status">
          <span className={`dot ${ws === "connecting" ? "warn" : "err"}`} />
          {ws === "connecting" ? "conectando…" : "sin conexión · reintentar"}
        </button>
      )}
      <button type="button" className="icon-btn" onClick={() => store.setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Tema claro" : "Tema oscuro"} title="Cambiar tema">
        {theme === "dark" ? <I.Sun /> : <I.Moon />}
      </button>
      <button type="button" className="icon-btn hide-narrow" onClick={onHelp} aria-label="Atajos de teclado (?)" title="Atajos (?)">
        <I.Keyboard />
      </button>
      <button type="button" className="icon-btn" onClick={onSettings} aria-label="Preferencias" title="Preferencias">
        <I.Settings />
      </button>
    </header>
  );
}

export function Jobs() {
  const store = useStore();
  const jobs = useApp((s) => s.jobs);
  const list = Object.values(jobs);
  if (list.length === 0) return null;
  return (
    <div className="jobs" aria-live="polite" data-testid="jobs">
      {list.map((j) => (
        <JobRow key={j.id} job={j} onDismiss={() => store.dismissJob(j.id)} />
      ))}
    </div>
  );
}

function JobRow({ job, onDismiss }: { job: Job; onDismiss: () => void }) {
  const label = TOOL_LABEL[job.tool] ?? job.tool;
  const p = job.progress;
  const running = job.status === "running" || job.status === "queued";
  return (
    <div className={`job ${job.status}`} role="status">
      {running ? <span className="spinner" aria-hidden="true" /> : job.status === "done" ? <I.Check /> : <I.Alert />}
      <div>
        <strong>{label}</strong>
        {job.message && <span className="muted"> · {job.message}</span>}
        {job.status === "queued" && <span className="muted"> · en cola</span>}
      </div>
      <span className="mono muted" style={{ fontSize: 12 }}>
        {job.status === "failed" ? (
          <button type="button" className="icon-btn" onClick={onDismiss} aria-label="Cerrar">
            <I.X />
          </button>
        ) : p != null ? (
          `${Math.round(p * 100)}%`
        ) : (
          ""
        )}
      </span>
      {running && (
        <div className={`bar${p == null ? " indet" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={p != null ? Math.round(p * 100) : undefined} aria-label={label}>
          <i style={{ width: `${Math.round((p ?? 0) * 100)}%` }} />
        </div>
      )}
      {job.status === "failed" && <div className="hint" style={{ gridColumn: "2 / 4", color: "var(--err)" }}>{job.result?.errors?.[0]?.message ?? "Falló. Mirá la terminal del agente."}</div>}
    </div>
  );
}

export function OfferBanner() {
  const store = useStore();
  const offer = useApp((s) => s.offer);
  const metas = useApp((s) => s.metas);
  const active = useApp((s) => s.active);
  if (!offer) return null;
  const m = metas[offer];
  return (
    <div className="banner" role="status" data-testid="offer">
      <span className="tag a">nueva</span>
      <div className="grow">
        <strong className="mono">{offer}</strong> lista{m?.request ? <> · <em>«{m.request}»</em></> : null}
        {m?.summary && <div className="hint">{m.summary}</div>}
      </div>
      <button type="button" className="btn primary small" onClick={() => void store.compareNew(offer, true)} data-testid="offer-compare">
        Comparar con {active ?? "la actual"}
      </button>
      <button type="button" className="btn ghost small" onClick={() => store.dismissOffer()}>
        Después
      </button>
    </div>
  );
}

export function Toasts() {
  const store = useStore();
  const toasts = useApp((s) => s.toasts);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
          <span className="dot" />
          <span style={{ flex: 1 }}>{t.text}</span>
          {t.action && (
            <button
              type="button"
              className="btn small"
              onClick={() => {
                t.action?.run();
                store.dismissToast(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" className="icon-btn" style={{ width: 26, height: 26 }} onClick={() => store.dismissToast(t.id)} aria-label="Cerrar aviso">
            <I.X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Cerrar">
            <I.X />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function HelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Atajos de teclado" onClose={onClose}>
      <dl className="keys">
        {SHORTCUTS.map(([keys, what]) => (
          <div key={what} style={{ display: "contents" }}>
            <dt>
              {keys.map((k) => (
                <kbd key={k}>{k}</kbd>
              ))}
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const policy = useApp((s) => s.newVersionPolicy);
  const theme = useApp((s) => s.theme);
  const lm = useApp((s) => s.loudnessMatch);
  return (
    <Dialog title="Preferencias" onClose={onClose}>
      <div className="settings">
        <div className="settings row">
          <strong>Cuando el agente renderiza una versión nueva</strong>
          <div className="seg" role="group" aria-label="Versión nueva">
            <button type="button" aria-pressed={policy === "offer"} onClick={() => store.setNewVersionPolicy("offer")}>
              Ofrecerla como B
            </button>
            <button type="button" aria-pressed={policy === "play"} onClick={() => store.setNewVersionPolicy("play")}>
              Reproducirla sola
            </button>
          </div>
          <span className="hint">“Reproducirla sola” la pone como A contra la que estabas escuchando (si el audio ya está activado).</span>
        </div>
        <div className="settings row">
          <strong>Tema</strong>
          <div className="seg" role="group" aria-label="Tema">
            <button type="button" aria-pressed={theme === "dark"} onClick={() => store.setTheme("dark")}>
              Oscuro
            </button>
            <button type="button" aria-pressed={theme === "light"} onClick={() => store.setTheme("light")}>
              Claro
            </button>
          </div>
        </div>
        <label className="toggle">
          <input type="checkbox" checked={lm} onChange={(e) => store.setLoudnessMatch(e.target.checked)} />
          Igualar volumen al comparar A/B (recomendado: lo más fuerte siempre parece mejor)
        </label>
      </div>
    </Dialog>
  );
}

const SAY = [
  "Importá nos-siguen-pegando-abajo.wav",
  "Armame un loop future funk de 16 compases con el riff del inicio",
  "Más filtrado en la intro, el bajo más gordo y el sample medio tono más arriba",
];

/** Versions exist (the project knows them) but none has audio: renders/ is
 *  derived data and was deleted. Nothing to play, so say how to get it back. */
export function NoAudioCard() {
  const store = useStore();
  const noAudio = useApp((s) => s.noAudio);
  const newest = noAudio[noAudio.length - 1];
  if (!newest) return null;
  const text = regenRequest(newest.version);
  return (
    <section className="card empty" data-testid="no-audio">
      <h2>Las versiones no tienen audio</h2>
      <p>
        Conozco {noAudio.length} versi{noAudio.length === 1 ? "ón" : "ones"} de este proyecto, pero no hay audio para escuchar (la carpeta <code>renders/</code> se borró o
        todavía no se renderizó). Se regenera sin perder nada: la canción está guardada.
      </p>
      <p>
        <strong>Pedile al agente que la regenere</strong> (render de {newest.version}):
      </p>
      <div className="say">
        <button
          type="button"
          data-testid="copy-regen-newest"
          onClick={async () => {
            const ok = await copyText(text);
            store.toast(ok ? "Copiado: pegáselo al agente" : "No pude copiar. Decile: " + text, ok ? "ok" : "warn", undefined, ok ? 3000 : 9000);
          }}
        >
          <span style={{ flex: 1 }}>{text}</span>
          <span className="hint">copiar</span>
        </button>
      </div>
      <p className="hint">Cuando termine de renderizar, la versión aparece sola acá, sin recargar.</p>
    </section>
  );
}

export function EmptyState() {
  const store = useStore();
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (t: string) => {
    void copyText(t).then((ok) => setCopied(ok ? t : null));
  };
  return (
    <section className="card empty" data-testid="empty">
      <h2>Todavía no hay ninguna versión</h2>
      <p>
        Acá vas a escuchar, comparar y comentar lo que arma el agente. Hablale en la terminal (Claude Code, Codex…) y cuando renderice la primera versión aparece
        sola, sin recargar.
      </p>
      <p>
        <strong>Probá decirle:</strong>
      </p>
      <div className="say">
        {SAY.map((t) => (
          <button key={t} type="button" onClick={() => copy(t)} title="Copiar">
            <span style={{ flex: 1 }}>{t}</span>
            <span className="hint">{copied === t ? "copiado" : "copiar"}</span>
          </button>
        ))}
      </div>
      <button type="button" className="btn ghost small" onClick={() => void store.init()}>
        <I.Retry /> Volver a buscar versiones
      </button>
    </section>
  );
}

export function ErrorState({ message }: { message: string | null }) {
  const store = useStore();
  return (
    <section className="card empty" role="alert" data-testid="boot-error">
      <h2>No encuentro la API de muestra</h2>
      <p>La interfaz habla con el servidor local del proyecto. Levantalo en una terminal y volvé a probar:</p>
      <div className="code">muestra serve --project ruta/a/tu-proyecto</div>
      {message && <p className="hint">Detalle: {message}</p>}
      <button type="button" className="btn primary" onClick={() => void store.init()}>
        <I.Retry /> Reintentar
      </button>
    </section>
  );
}

export function BootSkeleton() {
  return (
    <div aria-busy="true" aria-label="Cargando" style={{ display: "grid", gap: 16 }}>
      <div className="skel" style={{ height: 320 }} />
      <div className="skel" style={{ height: 220 }} />
    </div>
  );
}
