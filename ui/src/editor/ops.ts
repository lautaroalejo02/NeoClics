// song.edit primitive ops applied to the editor's JSON song, with their
// inverses. Same path grammar and semantics as src/muestra/editor (paths.py,
// ops.py): the editor applies an edit instantly here and sends the very same
// ops to the backend, which validates and writes song.yaml.

import {
  BEATS_PER_BAR,
  clipBaseLength,
  clipStart0,
  laneBarSteps,
  laneTileChars,
  parseSteps,
  passBeats,
  stable,
  velChar,
  type ClipJson,
  type SongJson,
} from "./song";

export type Op =
  | { op: "set"; path: string; value: unknown; expect?: unknown }
  | { op: "unset"; path: string; expect?: unknown }
  | { op: "insert"; path: string; index?: number | null; value: unknown }
  | { op: "remove"; path: string; expect?: unknown }
  | { op: "move"; path: string; to: number }
  | { op: "document"; value: unknown };

export class PathError extends Error {}

type Step = { kind: "key"; value: string } | { kind: "index"; value: number };

export function parsePath(path: string): Step[] {
  if (path === "") return [];
  const steps: Step[] = [];
  for (const seg of path.split(".")) {
    const m = /^([^.[\]]*)((?:\[\d+\])*)$/.exec(seg);
    if (!m || (!m[1] && !m[2])) throw new PathError(`segmento «${seg}» mal formado`);
    if (m[1]) steps.push({ kind: "key", value: m[1] });
    for (const i of m[2].matchAll(/\[(\d+)\]/g)) steps.push({ kind: "index", value: Number(i[1]) });
  }
  return steps;
}

type Container = Record<string, unknown> | unknown[];

function isMap(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** [container, key] of the node at `path` (key = number for lists). */
export function resolve(doc: unknown, path: string, create = false): [Container, string | number] {
  const steps = parsePath(path);
  if (!steps.length) throw new PathError("ruta vacía");
  let node: unknown = doc;
  let parentKey: string | null = null;
  let i = 0;
  while (true) {
    const step = steps[i];
    const last = i === steps.length - 1;
    if (Array.isArray(node)) {
      if (step.kind === "index") {
        if (last) return [node, step.value];
        if (step.value < 0 || step.value >= node.length) throw new PathError(`no hay elemento ${step.value}`);
        node = node[step.value];
        i += 1;
        continue;
      }
      if (parentKey === "automation") {
        const keys: string[] = [];
        for (let j = i; j < steps.length && steps[j].kind === "key"; j++) keys.push(String(steps[j].value));
        let found: [number, number] | null = null;
        for (let n = keys.length; n > 0 && !found; n--) {
          const target = keys.slice(0, n).join(".");
          const k = node.findIndex((a) => isMap(a) && a.target === target);
          if (k >= 0) found = [k, n];
        }
        if (!found) throw new PathError("no existe esa automatización");
        i += found[1];
        if (i === steps.length) return [node, found[0]];
        node = node[found[0]];
        parentKey = null;
        continue;
      }
      const k = node.findIndex((it) => isMap(it) && it.id === step.value);
      if (k < 0) throw new PathError(`no hay ningún elemento con id «${step.value}»`);
      if (last) return [node, k];
      node = node[k];
      i += 1;
      continue;
    }
    if (!isMap(node)) throw new PathError("atraviesa un valor que no es lista ni mapeo");
    if (step.kind !== "key") throw new PathError("un índice sobre un mapeo");
    const key = String(step.value);
    if (last) return [node, key];
    if (node[key] == null) {
      if (!create) throw new PathError(`falta «${key}»`);
      node[key] = {};
    }
    node = node[key];
    parentKey = key;
    i += 1;
  }
}

export function getAt(doc: unknown, path: string): unknown {
  const [c, k] = resolve(doc, path);
  if (Array.isArray(c)) {
    if (typeof k !== "number" || k < 0 || k >= c.length) throw new PathError(`no hay elemento ${k}`);
    return c[k];
  }
  if (!(String(k) in c)) throw new PathError("no existe");
  return c[String(k)];
}

export function tryGet(doc: unknown, path: string): unknown {
  try {
    return getAt(doc, path);
  } catch {
    return undefined;
  }
}

const clone = <T>(v: T): T => (v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T));

function listPathOf(elementPath: string): string {
  if (elementPath.endsWith("]")) return elementPath.slice(0, elementPath.lastIndexOf("["));
  return elementPath.slice(0, elementPath.lastIndexOf("."));
}

export function sameValue(a: unknown, b: unknown): boolean {
  const norm = (x: unknown): unknown => {
    if (typeof x === "number") return Number.isInteger(x) ? x : Math.round(x * 1e9) / 1e9;
    if (Array.isArray(x)) return x.map(norm);
    if (isMap(x)) return Object.fromEntries(Object.entries(x).filter(([, v]) => v !== undefined).map(([k, v]) => [k, norm(v)]));
    return x ?? null;
  };
  return stable(norm(a)) === stable(norm(b));
}

export class ConflictError extends Error {}

function checkExpect(op: { expect?: unknown }, current: unknown, path: string): void {
  if (!("expect" in op)) return;
  if (!sameValue(current ?? null, op.expect ?? null)) throw new ConflictError(`«${path}» cambió`);
}

/** Apply one primitive op in place; return [the applied op (with expect), its inverse]. */
export function applyPrimitive(doc: SongJson, op: Op): [Op, Op[]] {
  switch (op.op) {
    case "set": {
      const [c, k] = resolve(doc, op.path, true);
      let exists: boolean;
      let old: unknown;
      if (Array.isArray(c)) {
        if (typeof k !== "number" || k < 0 || k >= c.length) throw new PathError(`no hay elemento ${k}`);
        exists = true;
        old = c[k];
      } else {
        exists = String(k) in c && c[String(k)] !== undefined;
        old = c[String(k)];
      }
      checkExpect(op, exists ? old : null, op.path);
      const oldV = exists ? clone(old) : null;
      if (Array.isArray(c)) c[k as number] = clone(op.value);
      else c[String(k)] = clone(op.value);
      const applied: Op = { op: "set", path: op.path, value: clone(op.value), expect: oldV };
      return [applied, exists ? [{ op: "set", path: op.path, value: oldV, expect: clone(op.value) }] : [{ op: "unset", path: op.path, expect: clone(op.value) }]];
    }
    case "unset": {
      const [c, k] = resolve(doc, op.path);
      if (Array.isArray(c)) throw new PathError("es un elemento de lista: usá remove");
      const exists = String(k) in c && c[String(k)] !== undefined;
      checkExpect(op, exists ? c[String(k)] : null, op.path);
      if (!exists) return [{ op: "unset", path: op.path, expect: null }, []];
      const old = clone(c[String(k)]);
      delete c[String(k)];
      return [{ op: "unset", path: op.path, expect: old }, [{ op: "set", path: op.path, value: old, expect: null }]];
    }
    case "insert": {
      const [c, k] = resolve(doc, op.path, true);
      let seq: unknown;
      if (Array.isArray(c)) seq = c[k as number];
      else {
        if (c[String(k)] == null) c[String(k)] = [];
        seq = c[String(k)];
      }
      if (!Array.isArray(seq)) throw new PathError(`«${op.path}» no es una lista`);
      const index = op.index == null ? seq.length : Math.min(op.index, seq.length);
      seq.splice(index, 0, clone(op.value));
      return [{ op: "insert", path: op.path, index, value: clone(op.value) }, [{ op: "remove", path: `${op.path}[${index}]`, expect: clone(op.value) }]];
    }
    case "remove": {
      const [c, k] = resolve(doc, op.path);
      if (Array.isArray(c)) {
        const i = k as number;
        if (i < 0 || i >= c.length) throw new PathError(`no hay elemento ${i}`);
        checkExpect(op, c[i], op.path);
        const old = clone(c[i]);
        c.splice(i, 1);
        return [{ op: "remove", path: op.path, expect: old }, [{ op: "insert", path: listPathOf(op.path), index: i, value: old }]];
      }
      if (!(String(k) in c)) throw new PathError("no existe");
      checkExpect(op, c[String(k)], op.path);
      const old = clone(c[String(k)]);
      delete c[String(k)];
      return [{ op: "remove", path: op.path, expect: old }, [{ op: "set", path: op.path, value: old }]];
    }
    case "move": {
      const [c, k] = resolve(doc, op.path);
      if (!Array.isArray(c)) throw new PathError("no es un elemento de lista");
      const to = Math.min(op.to, c.length - 1);
      const [item] = c.splice(k as number, 1);
      c.splice(to, 0, item);
      return [op, [{ op: "move", path: `${listPathOf(op.path)}[${to}]`, to: k as number }]];
    }
    case "document": {
      const old = clone(doc);
      for (const key of Object.keys(doc)) delete (doc as unknown as Record<string, unknown>)[key];
      Object.assign(doc, clone(op.value));
      return [op, [{ op: "document", value: old }]];
    }
  }
}

/** Apply a batch on a COPY; returns the new song, applied ops (with expects) and the inverse batch. */
export function applyOps(song: SongJson, ops: Op[]): { song: SongJson; applied: Op[]; inverse: Op[] } {
  const doc = clone(song);
  const applied: Op[] = [];
  let inverse: Op[] = [];
  for (const op of ops) {
    const [a, inv] = applyPrimitive(doc, op);
    applied.push(a);
    inverse = [...inv, ...inverse];
  }
  return { song: doc, applied, inverse };
}

// ------------------------------------------------------------------ edit builders
// (the same compilation as song.edit's semantic ops, done client-side so the
// change is instant; the server receives the resulting primitives)

/** Set / remove one GRID hit (song bar + 0-based step): writes the lane's
 *  per-bar override, dropping it when the bar is back to the pattern. */
export function hitOps(song: SongJson, trackId: string, lane: string, bar: number, step: number, velocity: number | null): Op[] {
  const track = song.tracks?.find((t) => t.id === trackId);
  const p = track?.pattern;
  const l = p?.lanes[lane];
  if (!track || !p || !l) return [];
  const at = track.at_bar ?? 1;
  const tile = laneTileChars(p, l, bar, at);
  const overrides = l.overrides ?? {};
  const current = overrides[String(bar)];
  const chars = (current ?? tile).replace(/ /g, "").split("");
  chars[step] = velChar(velocity);
  const next = chars.join("");
  const base = `tracks.${trackId}.pattern.lanes.${lane}.overrides`;
  if (sameValue(parseSteps(next), parseSteps(tile))) {
    if (current === undefined) return [];
    const others = Object.keys(overrides).filter((k) => k !== String(bar));
    return others.length ? [{ op: "unset", path: `${base}.${bar}`, expect: current }] : [{ op: "unset", path: base, expect: overrides }];
  }
  if (current !== undefined && sameValue(parseSteps(next), laneBarSteps(p, l, bar, at))) return [];
  return [{ op: "set", path: `${base}.${bar}`, value: next, ...(current !== undefined ? { expect: current } : {}) }];
}

/** Place a clip at a 0-based song beat (at_bar + offset_beats). */
export function placeOps(path: string, clip: ClipJson, beat0: number): Op[] {
  const bar = Math.floor(beat0 / BEATS_PER_BAR + 1e-9) + 1;
  const off = Math.round((beat0 - (bar - 1) * BEATS_PER_BAR) * 1e6) / 1e6;
  const ops: Op[] = [];
  if (clip.at_bar !== bar) ops.push({ op: "set", path: `${path}.at_bar`, value: bar, expect: clip.at_bar });
  const curOff = clip.offset_beats ?? 0;
  if (Math.abs(curOff - off) > 1e-9) {
    if (Math.abs(off) < 1e-9) ops.push({ op: "unset", path: `${path}.offset_beats`, expect: clip.offset_beats ?? null });
    else ops.push({ op: "set", path: `${path}.offset_beats`, value: off, expect: clip.offset_beats ?? null });
  }
  return ops;
}

/** Placement fields of a clip dict for a 0-based beat (no ops). */
export function placed(clip: ClipJson, beat0: number): ClipJson {
  const bar = Math.floor(beat0 / BEATS_PER_BAR + 1e-9) + 1;
  const off = Math.round((beat0 - (bar - 1) * BEATS_PER_BAR) * 1e6) / 1e6;
  const out: ClipJson = { ...clip, at_bar: bar };
  if (Math.abs(off) > 1e-9) out.offset_beats = off;
  else delete out.offset_beats;
  return out;
}

/** Drop defaults so a clip written by the editor reads like hand-written YAML. */
export function tidyClip(c: ClipJson): ClipJson {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(c)) {
    if (v == null) continue;
    if ((k === "repeat" || k === "stretch") && v === 1) continue;
    if ((k === "offset_beats" || k === "trim_start_beats" || k === "gain_db") && v === 0) continue;
    if (k === "reverse" && v === false) continue;
    if (k === "instances" && Object.keys(v as object).length === 0) continue;
    out[k] = v;
  }
  return out as unknown as ClipJson;
}

/** Split a clip at a 0-based song beat: same audio, now two (or more) clips. */
export function splitClipOps(song: SongJson, trackId: string, ci: number, beat0: number): Op[] | null {
  const track = song.tracks?.find((t) => t.id === trackId);
  const clip = track?.clips?.[ci];
  if (!clip) return null;
  const len = passBeats(clip, clipBaseLength(song, clip));
  const start = clipStart0(clip);
  const repeat = clip.repeat ?? 1;
  const cut = beat0 - start;
  if (!(cut > 1e-6 && cut < len * repeat - 1e-6)) return null;
  const k = Math.floor(cut / len + 1e-9);
  const inside = cut - k * len;
  const inst = clip.instances ?? {};
  const subset = (from: number, to: number, shift: number) =>
    Object.fromEntries(Object.entries(inst).filter(([n]) => Number(n) >= from && Number(n) <= to).map(([n, v]) => [String(Number(n) - shift), v]));
  const pieces: [number, ClipJson][] = [];
  if (Math.abs(inside) < 1e-6) {
    pieces.push([start, { ...clip, repeat: k, instances: subset(1, k, 0) }]);
    pieces.push([start + k * len, { ...clip, repeat: repeat - k, instances: subset(k + 1, repeat, k) }]);
  } else {
    if (k) pieces.push([start, { ...clip, repeat: k, instances: subset(1, k, 0) }]);
    const own = inst[String(k + 1)];
    const stretch = clip.stretch ?? 1;
    pieces.push([start + k * len, { ...clip, repeat: 1, length_beats: round6(inside), instances: own ? { "1": own } : {} }]);
    pieces.push([
      start + k * len + inside,
      { ...clip, repeat: 1, length_beats: round6(len - inside), trim_start_beats: round6((clip.trim_start_beats ?? 0) + inside / stretch), instances: own ? { "1": own } : {} },
    ]);
    if (repeat - k - 1 > 0) pieces.push([start + (k + 1) * len, { ...clip, repeat: repeat - k - 1, instances: subset(k + 2, repeat, k + 1) }]);
  }
  const path = `tracks.${trackId}.clips`;
  return pieces.map(([pos, c], i) =>
    i === 0
      ? ({ op: "set", path: `${path}[${ci}]`, value: tidyClip(placed(c, pos)), expect: clip } as Op)
      : ({ op: "insert", path, index: ci + i, value: tidyClip(placed(c, pos)) } as Op),
  );
}

export function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}
