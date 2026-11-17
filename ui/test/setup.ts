import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// jsdom lacks these browser APIs; the UI degrades gracefully without them.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const g = globalThis as Record<string, unknown>;
g.ResizeObserver ??= RO;
g.IntersectionObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
};
if (typeof window !== "undefined") {
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  // no canvas in jsdom: drawing code checks for a null context
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
  window.scrollTo ??= () => {};
  Element.prototype.scrollIntoView ??= function () {};
}
