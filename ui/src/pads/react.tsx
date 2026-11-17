import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import type { PadsState, PadsStore } from "./store";

const Ctx = createContext<PadsStore | null>(null);

export function PadsProvider({ pads, children }: { pads: PadsStore; children: ReactNode }) {
  return <Ctx.Provider value={pads}>{children}</Ctx.Provider>;
}

export function usePadsMaybe(): PadsStore | null {
  return useContext(Ctx);
}

export function usePadsStore(): PadsStore {
  const p = useContext(Ctx);
  if (!p) throw new Error("PadsProvider missing");
  return p;
}

const OFF = { status: "unsupported" } as PadsState;
const noop = () => () => undefined;

export function usePads<T>(selector: (s: PadsState) => T): T {
  const p = useContext(Ctx);
  return useSyncExternalStore(p ? p.subscribe : noop, () => selector(p ? p.state : OFF), () => selector(p ? p.state : OFF));
}
