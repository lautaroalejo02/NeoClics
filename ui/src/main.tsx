import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/space-grotesk/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MuestraClient, resolveBaseUrl } from "./api/client";
import { captureToken, forgetToken } from "./api/token";
import { AudioEngine } from "./audio/engine";
import { StoreProvider } from "./state/react";
import { Store } from "./state/store";
import { App } from "./ui/App";

// Access token (phone on the same Wi-Fi): taken from ?token= on first load,
// kept for the session, stripped from the visible URL.
const token = captureToken();
// The store is created first so the engine can hand decoded mixes to it
// (waveform peaks) without React in the loop.
let store: Store | null = null;
const client = new MuestraClient({
  baseUrl: resolveBaseUrl(),
  token,
  onAuthError: (status) => {
    forgetToken(); // a stale token must not be re-sent forever
    store?.authRefused(status);
  },
});
const engine = new AudioEngine({
  fetchFile: (p, signal) => client.fetchFile(p, signal),
  onMixDecoded: (v, ch) => store?.onMixDecoded(v, ch),
});
store = new Store({ client, engine });
engine.onEnded = () => undefined;

// Autoplay policy: the first user gesture anywhere unlocks audio.
const unlock = () => {
  engine.unlock();
  window.removeEventListener("pointerdown", unlock, true);
  window.removeEventListener("keydown", unlock, true);
};
window.addEventListener("pointerdown", unlock, true);
window.addEventListener("keydown", unlock, true);

// Diagnostics hook (used by the e2e test to read scheduling data).
(window as unknown as { __muestra: unknown }).__muestra = { store, engine, client };

document.documentElement.dataset.theme = store.state.theme;
void store.init();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider store={store}>
      <App />
    </StoreProvider>
  </StrictMode>,
);
