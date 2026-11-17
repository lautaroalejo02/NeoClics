import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/space-grotesk/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles.css";
import "./ui/editor/editor.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MuestraClient, resolveBaseUrl } from "./api/client";
import { captureToken, forgetToken } from "./api/token";
import { AudioEngine } from "./audio/engine";
import { StoreProvider } from "./state/react";
import { Store } from "./state/store";
import { App } from "./ui/App";
import { EditorStore } from "./editor/store";
import { EditorProvider } from "./editor/react";

// Access token (phone on the same Wi-Fi): taken from ?token= on first load,
// kept for the session, stripped from the visible URL.
const token = captureToken();
// The store is created first so the engine can hand decoded mixes to it
// (waveform peaks) without React in the loop.
let store: Store | null = null;
const client = new MuestraClient({
  baseUrl: resolveBaseUrl(),
  token,
  onAuthError: () => {
    forgetToken(); // a stale token must not be re-sent forever
    store?.authRefused();
  },
});
const engine = new AudioEngine({
  fetchFile: (p, signal) => client.fetchFile(p, signal),
  onMixDecoded: (v, ch) => store?.onMixDecoded(v, ch),
});
store = new Store({ client, engine });
const editor = new EditorStore(store, engine);
store.onEvent = (e) => {
  switch (e.type) {
    case "song.changed":
      void editor.reload(false);
      break;
    case "draft.created":
      if (typeof e.data.id === "string") setTimeout(() => editor.onDraft(e.data.id as string), 50);
      break;
    case "version.created":
      editor.onVersion();
      break;
    case "job.updated":
      editor.onJob(e.data as never);
      break;
  }
};
engine.onEnded = () => undefined;

// Autoplay policy: a user gesture anywhere unlocks audio. iOS Safari only
// honours AudioContext.resume() inside touchend/click (not pointerdown), so
// listen to all of them and keep listening until the context really runs;
// if it gets interrupted later (phone call, lock screen) arm them again.
const GESTURES = ["pointerdown", "touchend", "click", "keydown"] as const;
let armed = false;
const unlock = () => engine.unlock();
const arm = (on: boolean) => {
  if (on === armed) return;
  armed = on;
  for (const g of GESTURES) (on ? window.addEventListener : window.removeEventListener).call(window, g, unlock, true);
};
engine.subscribe(() => {
  const a = engine.getSnapshot().audio;
  if (a === "running") arm(false);
  else if (a === "suspended") arm(true);
});
arm(true);

// Diagnostics hook (used by the e2e test to read scheduling data).
(window as unknown as { __muestra: unknown }).__muestra = { store, engine, client, editor };

document.documentElement.dataset.theme = store.state.theme;
void store.init();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider store={store}>
      <EditorProvider editor={editor}>
        <App />
      </EditorProvider>
    </StoreProvider>
  </StrictMode>,
);
