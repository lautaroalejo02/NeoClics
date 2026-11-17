// Short Spanish labels for lane sounds (WP17 slice lanes).

import type { LaneJson } from "./song";

/** "kick-01" or "pedazo del chop c1 (tiempo 3, 1 t)" (WP17 slice lanes). */
export function laneSoundLabel(lane: Pick<LaneJson, "sample" | "slice" | "pitch_semitones" | "reverse" | "choke">): string {
  const tune = (lane.pitch_semitones ? ` · ${lane.pitch_semitones > 0 ? "+" : ""}${lane.pitch_semitones} st` : "") + (lane.reverse ? " · al revés" : "") + (lane.choke ? ` · corte ${lane.choke}` : "");
  const sl = lane.slice;
  if (!sl) return String(lane.sample ?? "").split("/").pop() + tune;
  const start = (sl.start_beats ?? 0) + (sl.chop ? 1 : 0);
  const where = sl.chop ? `del chop ${sl.chop} (tiempo ${+start.toFixed(3)}` : `de ${sl.source} (compás ${sl.start_downbeat}${sl.start_beats ? ` + ${+sl.start_beats.toFixed(3)}` : ""}`;
  return `pedazo ${where}, ${+sl.length_beats.toFixed(3)} t)${sl.speed === "source" ? " · velocidad del disco" : ""}${tune}`;
}
