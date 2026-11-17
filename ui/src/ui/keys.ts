// Keyboard shortcuts (global, ignored while typing).

import { isTypingTarget } from "../hooks/hooks";
import type { Store } from "../state/store";

export const SHORTCUTS: [string[], string][] = [
  [["Espacio"], "Play / pausa"],
  [["S"], "Cambiar entre A y B (en el mismo punto)"],
  [["A"], "Escuchar A"],
  [["B"], "Escuchar B"],
  [["X", "Y"], "Modo ciego: escuchar X o Y"],
  [["L"], "Loop on/off (arrastrá sobre la onda para elegir compases)"],
  [["←", "→"], "Un tiempo atrás / adelante"],
  [["⇧ ←", "⇧ →"], "Un compás atrás / adelante"],
  [["M"], "Comentar en el punto que suena (Enter envía)"],
  [["1", "…", "9"], "Saltar a la versión 1…9"],
  [["↑", "↓"], "Volumen"],
  [["?"], "Esta ayuda"],
];


/** Global shortcuts. Ignored while typing (inputs, textareas). */
export function handleKey(store: Store, e: KeyboardEvent, openHelp: () => void): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return false;
  if (isTypingTarget(e.target)) return false;
  // sliders keep their own arrow keys
  if (e.key.startsWith("Arrow") && (e.target as HTMLInputElement | null)?.type === "range") return false;
  const s = store.state;
  const k = e.key;
  // Let real buttons handle Space/Enter themselves only for non-transport keys.
  switch (k) {
    case " ":
    case "Spacebar":
      store.togglePlay();
      return true;
    case "s":
    case "S":
      void (s.blind ? store.flipBlind() : store.flip());
      return true;
    case "a":
    case "A":
      if (!s.blind) void store.hearSide("a");
      return true;
    case "b":
    case "B":
      if (!s.blind) void store.hearSide("b");
      return true;
    case "x":
    case "X":
      if (s.blind) void store.hearBlind("x");
      return !!s.blind;
    case "y":
    case "Y":
      if (s.blind) void store.hearBlind("y");
      return !!s.blind;
    case "l":
    case "L":
      store.toggleLoop();
      return true;
    case "m":
    case "M":
      store.openDraft();
      return true;
    case "ArrowLeft":
      store.seekBy(-1, e.shiftKey ? "bar" : "beat");
      return true;
    case "ArrowRight":
      store.seekBy(1, e.shiftKey ? "bar" : "beat");
      return true;
    case "ArrowUp":
      store.setVolume(s.volume + 0.05);
      return true;
    case "ArrowDown":
      store.setVolume(s.volume - 0.05);
      return true;
    case "?":
      openHelp();
      return true;
    case "Escape":
      if (s.draft) {
        store.closeDraft();
        return true;
      }
      return false;
    default:
      if (/^[1-9]$/.test(k) && !s.blind) {
        const v = store.versionAt(Number(k));
        if (v) void store.listen(v);
        return true;
      }
      return false;
  }
}

