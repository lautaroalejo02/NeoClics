// Pads <-> computer keyboard, by PHYSICAL key (KeyboardEvent.code): KeyZ is
// the bottom-left letter on QWERTY, AZERTY ("W") and QWERTZ ("Y") alike, so a
// layout always has the same shape under the fingers. Three rows of 8 fit any
// laptop (no numpad). Bottom row first: kick/snare/hats live under the left
// hand like on a drum machine.

export const ROWS: string[][] = [
  ["KeyQ", "KeyW", "KeyE", "KeyR", "KeyT", "KeyY", "KeyU", "KeyI"],
  ["KeyA", "KeyS", "KeyD", "KeyF", "KeyG", "KeyH", "KeyJ", "KeyK"],
  ["KeyZ", "KeyX", "KeyC", "KeyV", "KeyB", "KeyN", "KeyM", "Comma"],
];

/** Pad order: bottom row, middle row, top row (kit order, slices left to right). */
export const PAD_KEYS: string[] = [...ROWS[2], ...ROWS[1], ...ROWS[0]];

const QWERTY: Record<string, string> = { Comma: "," };
for (const row of ROWS) for (const c of row) if (c.startsWith("Key")) QWERTY[c] = c.slice(3);

/** Key-cap label for a code: the user's layout when the browser tells us
 *  (navigator.keyboard.getLayoutMap), else QWERTY. */
export function capLabel(code: string, layout?: Map<string, string> | null): string {
  const v = layout?.get(code);
  return (v ?? QWERTY[code] ?? code).toUpperCase();
}

/** Ask the browser for the real layout (Chromium only; null elsewhere). */
export async function readLayout(): Promise<Map<string, string> | null> {
  const kb = (navigator as Navigator & { keyboard?: { getLayoutMap?: () => Promise<Map<string, string>> } }).keyboard;
  try {
    return (await kb?.getLayoutMap?.()) ?? null;
  } catch {
    return null;
  }
}

export function isPadCode(code: string): boolean {
  return PAD_KEYS.includes(code);
}

export type PadsKeyAction =
  | { kind: "pad"; code: string; accent: boolean }
  | { kind: "bank"; index: number }
  | { kind: "transport" }
  | { kind: "record" }
  | { kind: "undoPass" }
  | { kind: "metronome" }
  | { kind: "exit" }
  | null;

/**
 * What a key does while the pads are ON. Only physical codes are used, so the
 * same keys work on every layout. Ctrl/Cmd/Alt combos are never captured
 * (Ctrl+Z still undoes). Auto-repeat is ignored by the caller for pads.
 */
export function classifyKey(e: { code: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; shiftKey?: boolean }): PadsKeyAction {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  if (isPadCode(e.code)) return { kind: "pad", code: e.code, accent: !!e.shiftKey };
  switch (e.code) {
    case "Digit1":
    case "Digit2":
    case "Digit3":
    case "Digit4":
      return { kind: "bank", index: Number(e.code.slice(5)) - 1 };
    case "Space":
      return { kind: "transport" };
    case "Enter":
    case "NumpadEnter":
      return { kind: "record" };
    case "Backspace":
      return { kind: "undoPass" };
    case "Digit0":
      return { kind: "metronome" };
    case "Escape":
      return { kind: "exit" };
  }
  return null;
}

/** Pad colours by role (also the default for a new pad). */
export const PAD_COLORS = ["#ff5d8f", "#ffb347", "#4fd1c5", "#7aa2ff", "#c792ea", "#a3e635", "#f472b6", "#facc15"];

/** A sensible kit layout: lane role -> pad slot (bottom row = drums, middle = extra). */
export function kitSlot(role: string, used: Set<number>): number {
  const r = role.toLowerCase();
  const pref: [RegExp, number[]][] = [
    [/kick|bombo|bd/, [0, 8]],
    [/snare|caja|sd/, [1, 9]],
    [/clap|palma/, [2, 10]],
    [/ohat|open/, [4, 12]],
    [/chat|closed|hh|hat/, [3, 11]],
    [/tom/, [5, 13, 14]],
    [/crash|ride|cym|plato/, [6, 15]],
    [/rim|perc|conga|bongo|cow|shak|tamb/, [7, 16, 17, 18, 19]],
  ];
  for (const [re, slots] of pref) if (re.test(r)) for (const s of slots) if (!used.has(s)) return s;
  for (let i = 0; i < PAD_KEYS.length; i++) if (!used.has(i)) return i;
  return -1;
}
