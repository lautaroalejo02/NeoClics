// Spanish copy + number formatting (WP14 finding 5).
import { describe, expect, it } from "vitest";
import {
  alarmCopy,
  describeChopScore,
  fmtBpm,
  fmtDb,
  fmtMs,
  fmtNum,
  fmtPct,
  friendlyError,
  keyLabel,
  parseKey,
  sameTonic,
  scoreVerdict,
  spellTonic,
} from "../src/text/es";

describe("number formatting", () => {
  it("never prints floating point noise", () => {
    expect(fmtBpm(249.99999999999793)).toBe("250");
    expect(fmtBpm(260.8)).toBe("260.8");
    expect(fmtBpm(120.04999)).toBe("120");
    expect(fmtNum(-0.0001, 1)).toBe("0");
    expect(fmtNum(Number.NaN)).toBe("—");
    expect(fmtNum(null)).toBe("—");
  });
  it("dB, %, ms with units", () => {
    expect(fmtDb(-13.2345)).toBe("-13.2 dB");
    expect(fmtDb(3, { sign: true })).toBe("+3 dB");
    expect(fmtDb(0, { sign: true })).toBe("0 dB");
    expect(fmtPct(0.8749)).toBe("87%");
    expect(fmtPct(0.0001)).toBe("<1%");
    expect(fmtPct(0)).toBe("0%");
    expect(fmtMs(3.14159)).toBe("3.1 ms");
    expect(fmtMs(37.6)).toBe("38 ms");
  });
});

describe("keys: enharmonic spellings are the same note", () => {
  it("D# = Eb, and one spelling is shown (the song's)", () => {
    expect(sameTonic("D#", "Eb")).toBe(true);
    expect(sameTonic("F#", "Gb")).toBe(true);
    expect(sameTonic("C", "D")).toBe(false);
    expect(spellTonic("D#", "Eb")).toBe("Eb");
    expect(spellTonic("Eb", "D#")).toBe("D#");
    expect(spellTonic("A#", "F")).toBe("A#"); // F is neutral → sharps
    expect(keyLabel("D#", "major", "Eb")).toBe("Eb mayor");
    expect(keyLabel("F#", "minor", "F#m")).toBe("F# menor");
  });
  it("parses modes", () => {
    expect(parseKey("F#m")).toMatchObject({ pc: 6, mode: "minor" });
    expect(parseKey("Bb minor")).toMatchObject({ pc: 10, mode: "minor", accidental: "b" });
    expect(parseKey("C major")).toMatchObject({ pc: 0, mode: "major" });
    expect(parseKey("Eb")).toMatchObject({ pc: 3, mode: null });
    expect(parseKey("")).toBeNull();
  });
});

// Every code ears can emit (src/muestra/ears/*.py, docs/ears.md).
const EARS_CODES = [
  "mix_silent", "clipping", "true_peak_hot", "loudness_off_target", "over_compressed", "no_loudness_target",
  "no_sidechain", "low_end_collision", "sidechain_missing", "sidechain_weak", "sidechain_no_recovery",
  "dc_offset", "low_end_phase", "track_silent", "track_inaudible",
  "click", "loop_seam_click",
  "lane_offset", "notes_offset", "swing_mismatch", "grid_drift", "clip_offset", "grid_offset",
  "track_transposed", "key_mismatch", "sample_detuned", "bass_sample_clash",
  ...["sub", "bass", "low_mid", "mid", "high_mid", "presence", "air"].flatMap((b) => [`spectral_${b}_excess`, `spectral_${b}_deficit`]),
];

describe("alarm copy", () => {
  it.each(EARS_CODES)("%s has a Spanish title and a one-line explanation", (code) => {
    const c = alarmCopy(code);
    expect(c.title.length).toBeGreaterThan(3);
    expect(c.explain.length).toBeGreaterThan(20);
    expect(c.explain).not.toContain("\n");
    // never the raw code, never underscores / English fallback
    expect(c.title).not.toMatch(/_/);
    expect(c.title.toLowerCase()).not.toBe(code.replace(/_/g, " "));
    expect(c.title).not.toMatch(/\b(deficit|excess|target|off|loudness)\b/i);
  });
  it("spectral codes name the band in Spanish", () => {
    expect(alarmCopy("spectral_sub_deficit").title).toBe("Faltan subgraves");
    expect(alarmCopy("spectral_presence_excess").title).toBe("Demasiada presencia");
    expect(alarmCopy("spectral_air_deficit").title).toBe("Falta aire");
    expect(alarmCopy("spectral_sub_deficit").explain).toContain("20–60 Hz");
    expect(alarmCopy("loudness_off_target").title).toBe("Volumen lejos del objetivo");
  });
  it("unknown codes fall back gracefully, never to the raw English code", () => {
    const c = alarmCopy("some_future_check");
    expect(c.title).toBe("Aviso de los oídos del agente");
    expect(c.explain.length).toBeGreaterThan(20);
    expect(alarmCopy("spectral_weird_excess").title).toContain("Demasiado");
  });
});

describe("chop score in musician words", () => {
  const reasons = [
    "Continuidad del loop: 0.912 (espectro y nivel en el punto de vuelta).",
    "Pulso estable: 0.950; irregularidad residual 0.52% descontando la deriva gradual del tempo.",
    "Energía relativa: 0.870; ataques por segundo: 3.25.",
    "Contenido tonal: 0.350; coincidencia con voz (posible filtración): 0.180.",
    "Preview sin warp: render_chop aún no está implementado; extracto original con fundidos de 5 ms.",
  ];
  const { facets, rest } = describeChopScore(reasons);
  it("answers the questions a musician asks", () => {
    const by = Object.fromEntries(facets.map((f) => [f.key, f]));
    expect(by.seam.label).toBe("Qué tan bien cierra el loop");
    expect(by.seam.verdict).toBe("cierra muy bien");
    expect(by.pulse.label).toBe("Estabilidad del tempo");
    expect(by.motion.label).toBe("Cuánto movimiento tiene");
    expect(by.motion.extra).toContain("3.3 golpes por segundo");
    expect(by.bleed.verdict).toBe("se cuela un poco de voz");
  });
  it("no jargon survives and unknown lines are kept as notes", () => {
    const text = facets.map((f) => `${f.label} ${f.verdict} ${f.extra ?? ""}`).join(" ");
    expect(text).not.toMatch(/irregularidad residual|Continuidad|deriva|0\.\d{3}/);
    expect(rest).toHaveLength(1);
    expect(scoreVerdict(0.82)).toBe("muy buen candidato");
    expect(scoreVerdict(0.1)).toBe("flojo");
  });
});

describe("friendlyError", () => {
  it("maps backend issue codes to Spanish and keeps the English as detail", () => {
    const e = Object.assign(new Error("Clip ends at beat 128 but the song ends at beat 64"), {
      status: 422,
      issues: [{ code: "clip_overrun", message: "Clip ends at beat 128 but the song ends at beat 64", path: "x" }],
    });
    const f = friendlyError(e);
    expect(f.message).toMatch(/más largo/);
    expect(f.message).not.toContain("Clip ends");
    expect(f.detail).toContain("Clip ends at beat 128");
    expect(f.code).toBe("clip_overrun");
  });
  it("network failure", () => {
    const f = friendlyError(Object.assign(new Error("chops_choose: sin conexión con la API (TypeError)"), { status: null, issues: [] }));
    expect(f.code).toBe("network");
    expect(f.advice).toContain("muestra serve");
  });
  it("unknown English error: generic Spanish message, English as detail", () => {
    const f = friendlyError(Object.assign(new Error("boom happened"), { status: 500, issues: [{ code: "weird", message: "boom happened" }] }), "No se pudo");
    expect(f.message).toBe("No se pudo.");
    expect(f.detail).toBe("boom happened");
  });
  it("a Spanish backend message is shown as is", () => {
    const f = friendlyError(Object.assign(new Error("x"), { status: 422, issues: [{ code: "otro", message: "El candidato ya está en uso" }] }));
    expect(f.message).toBe("El candidato ya está en uso");
  });
});
