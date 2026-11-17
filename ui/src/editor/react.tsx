import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import type { EditorState, EditorStore } from "./store";

const Ctx = createContext<EditorStore | null>(null);

export function EditorProvider({ editor, children }: { editor: EditorStore; children: ReactNode }) {
  return <Ctx.Provider value={editor}>{children}</Ctx.Provider>;
}

export function useEditorStore(): EditorStore {
  const e = useContext(Ctx);
  if (!e) throw new Error("EditorProvider missing");
  return e;
}

/** Subscribe to a slice of the editor state (return references / primitives). */
export function useEditor<T>(selector: (s: EditorState) => T): T {
  const e = useEditorStore();
  return useSyncExternalStore(
    e.subscribe,
    () => selector(e.state),
    () => selector(e.state),
  );
}
