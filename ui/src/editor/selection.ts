// What the person has selected in the timeline, its Spanish label, and the
// structured `selection` a "Pedile al agente" request carries (feedback
// contract, WP16).

import { BEATS_PER_BAR, clipBaseLength, clipStart0, noteName, passBeats, patternHits, trackById, trackNotes, type SongJson } from "./song";

export interface HitSel {
  lane: string;
  bar: number;
  step: number | null;
  index: number | null;
  beat0: number;
}

export interface NoteSel {
  index: number | null;
  phrase: string | null;
  phraseIndex: number | null;
  beat: number;
  pitch: number;
}

export type Selection =
  | { kind: "none" }
  | { kind: "hits"; track: string; items: HitSel[] }
  | { kind: "clips"; track: string; items: { clip: number; instance: number | null }[] }
  | { kind: "notes"; track: string; items: NoteSel[] }
  | { kind: "points"; track: string; target: string; items: number[] }
  | { kind: "track"; track: string }
  | { kind: "range"; track: string | null; lane: string | null; startBeat: number; endBeat: number };

export function emptySelection(): Selection {
  return { kind: "none" };
}

export function selCount(s: Selection): number {
  if (s.kind === "none") return 0;
  if (s.kind === "track" || s.kind === "range") return 1;
  return s.items.length;
}

const LANE_NAMES: Record<string, string> = {
  kick: "bombo",
  bd: "bombo",
  bombo: "bombo",
  snare: "caja",
  sd: "caja",
  clap: "clap",
  hat: "hi-hat",
  hh: "hi-hat",
  chat: "hi-hat cerrado",
  ch: "hi-hat cerrado",
  ohat: "hi-hat abierto",
  oh: "hi-hat abierto",
  ride: "ride",
  crash: "crash",
  tom: "tom",
  rim: "rim",
  perc: "percusión",
  shaker: "shaker",
};

export function laneName(lane: string): string {
  return LANE_NAMES[lane] ?? LANE_NAMES[lane.split(/[-_0-9]/)[0]] ?? lane;
}

const ORD = ["primer", "segundo", "tercer", "cuarto"];

export function beatLabel(beat0: number): string {
  const bar = Math.floor(beat0 / BEATS_PER_BAR + 1e-9) + 1;
  const inBar = beat0 - (bar - 1) * BEATS_PER_BAR;
  const b = Math.floor(inBar + 1e-9);
  const frac = Math.round((inBar - b) * 4);
  const sub = ["", " (semicorchea 2)", " (contratiempo)", " (semicorchea 4)"][frac] ?? "";
  return `compás ${bar}, ${ORD[b] ?? `${b + 1}º`} tiempo${sub}`;
}

function barsText(a: number, b: number): string {
  return a === b ? `compás ${a}` : `compases ${a} a ${b}`;
}

export function selectionLabel(song: SongJson, s: Selection): string {
  switch (s.kind) {
    case "none":
      return "nada";
    case "track":
      return `todo el track ${s.track}`;
    case "range": {
      const a = Math.floor(s.startBeat / BEATS_PER_BAR) + 1;
      const b = Math.max(a, Math.ceil(s.endBeat / BEATS_PER_BAR));
      return `${s.track ? `${s.lane ? laneName(s.lane) : s.track} · ` : ""}${barsText(a, b)}`;
    }
    case "hits": {
      const lanes = [...new Set(s.items.map((h) => laneName(h.lane)))];
      if (s.items.length === 1) return `${lanes[0]} · ${beatLabel(s.items[0].beat0)}`;
      const bars = s.items.map((h) => h.bar);
      return `${s.items.length} golpes de ${lanes.join(" y ")} · ${barsText(Math.min(...bars), Math.max(...bars))}`;
    }
    case "clips": {
      const t = trackById(song, s.track);
      const parts = s.items.map(({ clip, instance }) => {
        const c = t?.clips?.[clip];
        const what = c?.chop ? `chop ${c.chop}` : `sample ${String(c?.sample ?? "").split("/").pop()}`;
        if (!c) return what;
        const len = passBeats(c, clipBaseLength(song, c));
        const start = clipStart0(c) + (instance ? (instance - 1) * len : 0);
        const bar = Math.floor(start / BEATS_PER_BAR + 1e-9) + 1;
        return instance ? `${what}, repetición ${instance} (compás ${bar})` : `clip del ${what} (desde el compás ${bar})`;
      });
      return `${s.track} · ${parts.slice(0, 3).join("; ")}${parts.length > 3 ? ` y ${parts.length - 3} más` : ""}`;
    }
    case "notes": {
      if (s.items.length === 1) return `${s.track} · nota ${noteName(s.items[0].pitch)} en el ${beatLabel(s.items[0].beat - 1)}`;
      const bars = s.items.map((n) => Math.floor((n.beat - 1) / BEATS_PER_BAR) + 1);
      return `${s.track} · ${s.items.length} notas · ${barsText(Math.min(...bars), Math.max(...bars))}`;
    }
    case "points":
      return `${s.track} · automatización ${s.target.split(".").slice(2).join(".")} (${s.items.length} ${s.items.length === 1 ? "punto" : "puntos"})`;
  }
}

/** Beat range [start, end) (0-based) the selection covers. */
export function selectionSpan(song: SongJson, s: Selection): [number, number] | null {
  switch (s.kind) {
    case "range":
      return [s.startBeat, s.endBeat];
    case "hits": {
      const b = s.items.map((h) => h.beat0);
      return b.length ? [Math.min(...b), Math.max(...b) + 0.25] : null;
    }
    case "notes": {
      const t = trackById(song, s.track);
      const all = t ? trackNotes(song, t) : [];
      const ends = s.items.map((n) => {
        const v = all.find((x) => x.beat === n.beat && x.pitch === n.pitch);
        return n.beat - 1 + (v?.length ?? 0.25);
      });
      const b = s.items.map((n) => n.beat - 1);
      return b.length ? [Math.min(...b), Math.max(...ends)] : null;
    }
    case "clips": {
      const t = trackById(song, s.track);
      let lo = Infinity;
      let hi = -Infinity;
      for (const { clip, instance } of s.items) {
        const c = t?.clips?.[clip];
        if (!c) continue;
        const len = passBeats(c, clipBaseLength(song, c));
        const start = clipStart0(c);
        const a = instance ? start + (instance - 1) * len : start;
        const e = instance ? a + len : start + len * (c.repeat ?? 1);
        lo = Math.min(lo, a);
        hi = Math.max(hi, e);
      }
      return Number.isFinite(lo) ? [lo, hi] : null;
    }
    case "points": {
      const a = song.automation?.find((x) => x.target === s.target);
      const bars = s.items.map((i) => a?.points[i]?.[0]).filter((x): x is number => x != null);
      return bars.length ? [(Math.min(...bars) - 1) * 4, (Math.max(...bars) - 1) * 4 + 0.25] : null;
    }
    default:
      return null;
  }
}

/** The feedback `selection` object for a request about `s` (null for none). */
export function selectionPayload(song: SongJson, s: Selection): Record<string, unknown> | null {
  if (s.kind === "none") return null;
  const label = selectionLabel(song, s);
  const span = selectionSpan(song, s);
  const out: Record<string, unknown> = { label };
  if ("track" in s && s.track) out.track = s.track;
  if (span) {
    out.start_beat = Math.round((span[0] + 1) * 1e6) / 1e6;
    out.end_beat = Math.round((span[1] + 1) * 1e6) / 1e6;
  }
  const paths: string[] = [];
  const excerpt: Record<string, unknown> = {};
  const t = "track" in s && s.track ? trackById(song, s.track) : undefined;
  if (s.kind === "hits") {
    const lanes = [...new Set(s.items.map((h) => h.lane))];
    if (lanes.length === 1) out.lane = lanes[0];
    out.hits = s.items.map((h) => ({ lane: h.lane, bar: h.bar, step: h.step, beat: Math.round((h.beat0 + 1) * 1e6) / 1e6, index: h.index }));
    for (const h of s.items) {
      if (h.index != null) paths.push(`tracks.${s.track}.pattern.hits[${h.index}]`);
      else paths.push(`tracks.${s.track}.pattern.lanes.${h.lane}.overrides.${h.bar}`);
    }
    const all = t ? patternHits(song, t) : [];
    excerpt.lanes = Object.fromEntries(lanes.map((l) => [l, t?.pattern?.lanes[l]?.steps ?? ""]));
    excerpt.hits = s.items.map((h) => all.find((x) => x.lane === h.lane && Math.abs(x.beat0 - h.beat0) < 1e-6) ?? h);
  } else if (s.kind === "clips") {
    out.clips = s.items.map((c) => ({ clip: c.clip, instance: c.instance }));
    for (const c of s.items) paths.push(c.instance ? `tracks.${s.track}.clips[${c.clip}].instances.${c.instance}` : `tracks.${s.track}.clips[${c.clip}]`);
    excerpt.clips = s.items.map((c) => t?.clips?.[c.clip]);
    const chops = [...new Set(s.items.map((c) => t?.clips?.[c.clip]?.chop).filter(Boolean))];
    if (chops.length === 1) out.chop = chops[0];
  } else if (s.kind === "notes") {
    out.notes = s.items.map((n) => ({ index: n.index ?? n.phraseIndex ?? 0, beat: n.beat, pitch: n.pitch, ...(n.phrase ? { phrase: n.phrase } : {}) }));
    for (const n of s.items) paths.push(n.phrase ? `phrases.${n.phrase}.notes[${n.phraseIndex}]` : `tracks.${s.track}.notes[${n.index}]`);
    excerpt.notes = s.items.map((n) => (n.phrase ? song.phrases?.find((p) => p.id === n.phrase)?.notes[n.phraseIndex ?? 0] : t?.notes?.[n.index ?? 0]));
  } else if (s.kind === "points") {
    out.automation = s.target;
    out.points = s.items;
    paths.push(...s.items.map((i) => `automation.${s.target}.points[${i}]`));
    excerpt.automation = song.automation?.find((a) => a.target === s.target);
  } else if (s.kind === "track") {
    paths.push(`tracks.${s.track}`);
  } else if (s.kind === "range") {
    if (s.lane) out.lane = s.lane;
  }
  if (paths.length) out.paths = [...new Set(paths)];
  if (Object.keys(excerpt).length) out.excerpt = JSON.parse(JSON.stringify(excerpt));
  return out;
}
