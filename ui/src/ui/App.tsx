import { useEffect, useState, type ReactNode } from "react";
import { useLayout } from "../hooks/hooks";
import { handleKey } from "./keys";
import { useApp, useStore } from "../state/react";
import { ChopsPanel } from "./Chops";
import { BootSkeleton, EmptyState, ErrorState, NoAudioCard, UnauthorizedState, Header, HelpDialog, Jobs, OfferBanner, SettingsDialog, Toasts } from "./Chrome";
import { EarsPanel } from "./Ears";
import { FeedbackPanel } from "./Feedback";
import { MixerPanel } from "./Mixer";
import { Player } from "./Player";
import { VersionsPanel } from "./Versions";
import { EditorView } from "./editor/EditorView";
import { useEditor, useEditorStore } from "../editor/react";

type TabId = "mixer" | "ears" | "chops" | "feedback" | "versions";

export function App() {
  const store = useStore();
  const boot = useApp((s) => s.boot);
  const bootError = useApp((s) => s.bootError);
  const theme = useApp((s) => s.theme);
  const chopCount = useApp((s) => s.chopSets.length);
  const playable = useApp((s) => s.versions.length);
  const layout = useLayout();
  const [help, setHelp] = useState(false);
  const [settings, setSettings] = useState(false);
  const [tab, setTab] = useState<TabId>("mixer");
  const editor = useEditorStore();
  const edStatus = useEditor((s) => s.status);
  // The arrangement editor is the main view when the backend has it (WP16).
  const [view, setView] = useState<"arreglo" | "escuchar">(() => {
    try {
      return (globalThis.localStorage?.getItem("muestra.view") as "arreglo" | "escuchar" | null) ?? "arreglo";
    } catch {
      return "arreglo";
    }
  });
  useEffect(() => {
    if (boot === "ready" || boot === "empty") void editor.init();
  }, [boot, editor]);
  const editorOn = view === "arreglo" && edStatus !== "unsupported" && edStatus !== "off" && (boot === "ready" || boot === "empty");
  const go = (v: "arreglo" | "escuchar") => {
    setView(v);
    try {
      globalThis.localStorage?.setItem("muestra.view", v);
    } catch {
      /* private mode */
    }
    // the transport follows the view: the editor plays the working song
    editor.engine.setSource(v === "arreglo" && editor.state.listen === "editor" ? "editor" : "decks");
  };
  useEffect(() => {
    editor.engine.setSource(editorOn && editor.state.listen === "editor" ? "editor" : "decks");
  }, [editorOn, editor]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b0c18" : "#f4f0e8");
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (help || settings) return;
      if (editorOn) return; // the editor has its own keyboard
      // Space on a focused button would click it AND toggle play: let the
      // transport win, consistently.
      if (handleKey(store, e, () => setHelp(true))) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [store, help, settings, editorOn]);

  const tabs: { id: TabId; label: string; count?: number }[] = [
    { id: "mixer", label: "Mezcla" },
    { id: "ears", label: "Oídos" },
    { id: "chops", label: "Chops", count: chopCount || undefined },
  ];
  if (layout !== "wide") tabs.splice(1, 0, { id: "feedback", label: "Feedback" });
  if (layout === "narrow") tabs.unshift({ id: "versions", label: "Versiones" });
  const current = tabs.some((t) => t.id === tab) ? tab : "mixer";

  let main: ReactNode;
  if (editorOn) main = <EditorView onListen={() => go("escuchar")} />;
  else if (boot === "loading") main = <BootSkeleton />;
  else if (boot === "error") main = <ErrorState message={bootError} />;
  else if (boot === "unauthorized") main = <UnauthorizedState />;
  else if (boot === "empty")
    main = (
      <>
        <Jobs />
        <EmptyState />
      </>
    );
  else if (playable === 0)
    // every version is known but none has audio (renders/ was deleted)
    main = (
      <>
        <Jobs />
        <NoAudioCard />
        {layout === "narrow" && <VersionsPanel />}
      </>
    );
  else
    main = (
      <>
        <Jobs />
        <OfferBanner />
        <Player />
        <section className="card" aria-label="Paneles">
          <div className="tabs" role="tablist">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" className="tab" aria-selected={current === t.id} aria-controls={`panel-${t.id}`} id={`tab-${t.id}`} onClick={() => setTab(t.id)} data-testid={`tab-${t.id}`}>
                {t.label}
                {t.count ? <span className="count">{t.count}</span> : null}
              </button>
            ))}
          </div>
          <div className="card-body" style={{ paddingTop: 14 }} role="tabpanel" id={`panel-${current}`} aria-labelledby={`tab-${current}`}>
            {current === "mixer" && <MixerPanel />}
            {current === "ears" && <EarsPanel />}
            {current === "chops" && <ChopsPanel />}
            {current === "feedback" && <FeedbackPanelInline />}
            {current === "versions" && <VersionsInline />}
          </div>
        </section>
      </>
    );

  const ready = boot === "ready";
  return (
    <>
      <a href="#main" className="sr-only">
        Saltar al reproductor
      </a>
      <Header onHelp={() => setHelp(true)} onSettings={() => setSettings(true)} />
      {edStatus !== "unsupported" && (boot === "ready" || boot === "empty") && (
        <nav className="viewbar" aria-label="Vista">
          <div className="seg" role="tablist">
            <button type="button" role="tab" aria-selected={view === "arreglo"} className={`seg-btn ${view === "arreglo" ? "on" : ""}`} onClick={() => go("arreglo")} data-testid="view-arreglo">
              Arreglo
            </button>
            <button type="button" role="tab" aria-selected={view !== "arreglo"} className={`seg-btn ${view !== "arreglo" ? "on" : ""}`} onClick={() => go("escuchar")} data-testid="view-escuchar">
              Escuchar versiones
            </button>
          </div>
        </nav>
      )}
      {editorOn ? (
        <main className="editor-page" id="main">
          {main}
        </main>
      ) : (
      <div className={`layout ${layout}`}>
        {ready && layout !== "narrow" && (
          <aside className="col side" aria-label="Historial">
            <VersionsPanel />
          </aside>
        )}
        <main className="col" id="main">
          {main}
        </main>
        {ready && layout === "wide" && (
          <aside className="col side" aria-label="Feedback">
            <FeedbackPanel />
          </aside>
        )}
      </div>
      )}
      <Toasts />
      {help && <HelpDialog onClose={() => setHelp(false)} />}
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
    </>
  );
}

/** Panels reused inside tabs on smaller layouts: drop their card chrome. */
function FeedbackPanelInline() {
  return (
    <div className="inline-panel">
      <FeedbackPanel />
    </div>
  );
}

function VersionsInline() {
  return (
    <div className="inline-panel">
      <VersionsPanel />
    </div>
  );
}
