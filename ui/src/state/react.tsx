import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import type { AppState, Store } from "./store";
import type { EngineSnapshot } from "../audio/engine";

const StoreCtx = createContext<Store | null>(null);

export function StoreProvider({ store, children }: { store: Store; children: ReactNode }) {
  return <StoreCtx.Provider value={store}>{children}</StoreCtx.Provider>;
}

export function useStore(): Store {
  const s = useContext(StoreCtx);
  if (!s) throw new Error("StoreProvider missing");
  return s;
}

/** Subscribe to a slice. Selectors must return state references (or
 *  primitives), never freshly built objects. */
export function useApp<T>(selector: (s: AppState) => T): T {
  const store = useStore();
  return useSyncExternalStore(
    store.subscribe,
    () => selector(store.state),
    () => selector(store.state),
  );
}

export function useEngine(): EngineSnapshot {
  const store = useStore();
  return useSyncExternalStore(store.engine.subscribe, store.engine.getSnapshot, store.engine.getSnapshot);
}

/** Label a version for display, hiding identity in blind mode (X/Y). */
export function useSideLabel(): (v: string | null) => string {
  const blind = useApp((s) => s.blind);
  return (v) => {
    if (!v) return "—";
    if (blind && !blind.picked) return v === blind.x ? "X" : v === blind.y ? "Y" : "?";
    return v;
  };
}
