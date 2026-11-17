import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import type { EditorState, EditorStore } from "./store";

const Ctx = createContext<EditorStore | null>(null);

export function EditorProvider({ editor, children }: { editor: EditorStore; children: ReactNode }) {
  return <Ctx.Provider value={editor}>{children}</Ctx.Provider>;
}

/** The editor store, or null when the app runs without one (component tests). */
export function useEditorMaybe(): EditorStore | null {
  return useContext(Ctx);
}

export function useEditorStore(): EditorStore {
  const e = useContext(Ctx);
  if (!e) throw new Error("EditorProvider missing");
  return e;
}

const OFF = { status: "unsupported" } as EditorState;
const noop = () => () => undefined;

/** Subscribe to a slice of the editor state (return references / primitives). */
export function useEditor<T>(selector: (s: EditorState) => T): T {
  const e = useContext(Ctx);
  return useSyncExternalStore(
    e ? e.subscribe : noop,
    () => selector(e ? e.state : OFF),
    () => selector(e ? e.state : OFF),
  );
}
