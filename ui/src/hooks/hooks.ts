import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";

export type LayoutMode = "wide" | "medium" | "narrow";

function subscribeResize(cb: () => void) {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
}

function layoutNow(): LayoutMode {
  const w = typeof window === "undefined" ? 1400 : window.innerWidth;
  if (w >= 1360) return "wide";
  if (w >= 900) return "medium";
  return "narrow";
}

/** wide: versions | player | feedback; medium: versions | player; narrow: one column. */
export function useLayout(): LayoutMode {
  return useSyncExternalStore(subscribeResize, layoutNow, () => "wide");
}

/** requestAnimationFrame loop; the callback reads live values (engine
 *  position) and writes the DOM directly — no React state per frame. */
export function useRaf(cb: (t: number) => void, active = true): void {
  const ref = useRef(cb);
  useEffect(() => {
    ref.current = cb;
  });
  useEffect(() => {
    if (!active) return;
    let id = 0;
    const loop = (t: number) => {
      ref.current(t);
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [active]);
}

/** Element size (CSS px) via ResizeObserver. */
export function useSize<T extends HTMLElement>(): [RefObject<T | null>, { w: number; h: number }] {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const r = e.contentRect;
      setSize((s) => (Math.abs(s.w - r.width) < 0.5 && Math.abs(s.h - r.height) < 0.5 ? s : { w: r.width, h: r.height }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

/** Re-render every `ms` (relative timestamps like "hace 3 min"). */
export function useTick(ms: number): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((x) => x + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
  return n;
}

export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (el as HTMLInputElement).type;
    return !["checkbox", "radio", "range", "button", "submit"].includes(type);
  }
  return el.isContentEditable;
}

/** Copy text to the clipboard. navigator.clipboard only exists in secure
 *  contexts (not on http://192.168.x.y from a phone), so fall back to the
 *  old textarea + execCommand trick. Resolves whether it worked. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}
