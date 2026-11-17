// Derives extra render versions (v002, v003, and later simulated renders)
// from the fixture v001 so the UI has a meaningful version history and
// A/B actually sounds different. Pure DSP on the fixture wavs — no deps.

import fs from "node:fs";
import path from "node:path";
import {
  decodeWav, encodeWav, clone, gain, lowpass, highpass, saturate, sumInto, peaks,
} from "./wav.mjs";

function readMeta(projectDir, version) {
  return JSON.parse(fs.readFileSync(path.join(projectDir, "renders", version, "meta.json"), "utf8"));
}

function readWav(projectDir, rel) {
  return decodeWav(fs.readFileSync(path.join(projectDir, rel)));
}

function writeWav(projectDir, rel, wav, format) {
  const p = path.join(projectDir, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, encodeWav(wav, format));
}

/**
 * A "recipe" describes how to alter audio + what meta/metrics to write.
 * stemFx: {trackId | "trackId.lane": (wav)=>wav}
 */
const RECIPES = {
  v002: {
    request: "más filtrado en la intro, el bajo más gordo",
    summary:
      "Cerré el low-pass del sample a 1400 Hz y le di +3.5 dB al bajo. El bombo quedó igual.",
    diff: [
      { path: "tracks.sample.fx.lp.cutoff_hz", from: 18000, to: 1400 },
      { path: "tracks.bass.gain_db", from: 0, to: 3.5 },
    ],
    mixFx: (w) => gain(lowpass(clone(w), 1400), -0.5),
    stemFx: {
      sample: (w) => lowpass(w, 1200),
      bass: (w) => gain(w, 3.5),
    },
    metrics: (m) => ({
      ...m,
      loudness: { ...m.loudness, integrated_lufs: -13.1, true_peak_dbtp: -1.8 },
      spectral: {
        ...m.spectral,
        mix: { low_db: -16.9, mid_db: -21.8, high_db: -38.5 },
        tracks: {
          ...m.spectral.tracks,
          sample: { low_db: -26.0, mid_db: -23.1, high_db: -44.0 },
          bass: { low_db: -16.5, mid_db: -31.5, high_db: -50.0 },
        },
      },
      low_end_masking: {
        ...m.low_end_masking,
        energy_db: { drums: -19.0, bass: -16.5, sample: -30.0 },
        dominant_track: "bass",
      },
      alarms: [
        {
          code: "low_end_masking",
          severity: "warn",
          message: "El bajo y el bombo se pisan fuerte en 40-150 Hz",
          track: "bass",
          at_s: 0.5,
        },
      ],
    }),
  },
  v003: {
    request: "recuperá brillo y que pegue más",
    summary:
      "Abrí el filtro de vuelta, saturación leve en el master (+0.8 dB percibido) y batería +1 dB.",
    diff: [
      { path: "tracks.sample.fx.lp.cutoff_hz", from: 1400, to: 16000 },
      { path: "tracks.drums.gain_db", from: 0, to: 1 },
      { path: "master.fx[1]", from: null, to: { type: "saturation", drive_db: 6 } },
      { path: "master.gain_db", from: 0, to: 0.8 },
    ],
    mixFx: (w) => gain(saturate(clone(w), 1.4), 0.8),
    stemFx: {
      drums: (w) => gain(w, 1),
      sample: (w) => gain(w, 0.5),
    },
    metrics: (m) => ({
      ...m,
      loudness: { ...m.loudness, integrated_lufs: -10.9, true_peak_dbtp: -0.9 },
      spectral: {
        ...m.spectral,
        mix: { low_db: -17.8, mid_db: -20.4, high_db: -29.0 },
      },
      clicks: {
        count: 1,
        events: [{ track: "sample", at_s: 1.0, severity: 0.3 }],
      },
      alarms: [
        {
          code: "click",
          severity: "info",
          message: "Posible click en el borde de un chop",
          track: "sample",
          at_s: 1.0,
        },
        {
          code: "true_peak",
          severity: "warn",
          message: "True peak muy cerca de 0 dBTP (−0.9)",
          track: null,
          at_s: null,
        },
      ],
    }),
  },
  // Recipe used for simulated `render` tool calls (v004+), cycled by index.
  next: (n) => ({
    request: "iteración pedida por chat",
    summary: `Ajuste fino ${n}: balance de batería y brillo general.`,
    diff: [
      { path: "tracks.drums.gain_db", from: 1, to: 0.5 + (n % 3) * 0.5 },
      { path: "master.gain_db", from: 0.8, to: 0.4 },
    ],
    mixFx: (w) => gain(saturate(clone(w), 1.2), 0.4),
    stemFx: { drums: (w) => gain(w, 0.5 + (n % 3) * 0.5) },
    metrics: (m) => ({
      ...m,
      loudness: { ...m.loudness, integrated_lufs: -11.6, true_peak_dbtp: -1.1 },
      alarms: [],
    }),
  }),
};

/**
 * Render a derived version inside projectDir.
 * @returns {object} the meta object written
 */
export function renderVersion(projectDir, version, parent, recipe) {
  const srcMeta = readMeta(projectDir, parent);
  const dir = path.join(projectDir, "renders", version);
  fs.mkdirSync(path.join(dir, "stems"), { recursive: true });

  const stemFx = recipe.stemFx ?? {};
  // Stems: copy parent stems, applying per-track fx.
  for (const track of srcMeta.tracks) {
    const rel = track.stem.replace(`renders/${parent}/`, `renders/${version}/`);
    const wav = readWav(projectDir, track.stem);
    if (stemFx[track.id]) stemFx[track.id](wav);
    writeWav(projectDir, rel, wav, "float32");
    for (const lane of track.lanes ?? []) {
      const lrel = lane.stem.replace(`renders/${parent}/`, `renders/${version}/`);
      const lwav = readWav(projectDir, lane.stem);
      const laneFx = stemFx[`${track.id}.${lane.id}`];
      if (laneFx) laneFx(lwav);
      writeWav(projectDir, lrel, lwav, "float32");
    }
  }

  // Mix: either explicit mixFx on the parent mix, or the sum of the new stems.
  const mixRel = `renders/${version}/mix.wav`;
  if (recipe.mixFx) {
    writeWav(projectDir, mixRel, recipe.mixFx(readWav(projectDir, srcMeta.mix)), "pcm24");
  } else {
    const first = readWav(projectDir, `renders/${version}/stems/${srcMeta.tracks[0].id}.wav`);
    const mix = clone(first);
    for (const ch of mix.data) ch.fill(0);
    for (const t of srcMeta.tracks) {
      sumInto(mix, readWav(projectDir, `renders/${version}/stems/${t.id}.wav`));
    }
    writeWav(projectDir, mixRel, mix, "pcm24");
  }

  // created_at: at least a few minutes after the parent so the history
  // timeline stays ordered (fixture timestamps are fictional).
  const parentTs = Date.parse(srcMeta.created_at ?? "") || 0;
  const created = new Date(Math.max(Date.now(), parentTs + 8 * 60_000));
  const meta = {
    ...srcMeta,
    version,
    parent,
    created_at: created.toISOString(),
    request: recipe.request,
    agent: "mock-agent",
    summary: recipe.summary,
    diff: recipe.diff ?? [],
    mix: mixRel,
    tracks: srcMeta.tracks.map((t) => ({
      ...t,
      stem: t.stem.replace(`renders/${parent}/`, `renders/${version}/`),
      lanes: (t.lanes ?? []).map((l) => ({
        ...l,
        stem: l.stem.replace(`renders/${parent}/`, `renders/${version}/`),
      })),
    })),
  };
  fs.writeFileSync(path.join(dir, "meta.json"), JSON.stringify(meta, null, 2));
  fs.copyFileSync(
    path.join(projectDir, "renders", parent, "song.yaml"),
    path.join(dir, "song.yaml"),
  );

  const parentMetrics = JSON.parse(
    fs.readFileSync(path.join(projectDir, "renders", parent, "metrics.json"), "utf8"),
  );
  const metrics = { ...(recipe.metrics ? recipe.metrics(parentMetrics) : parentMetrics), version };
  fs.writeFileSync(path.join(dir, "metrics.json"), JSON.stringify(metrics, null, 2));

  // peaks.json — NOT in the contract; the mock serves it to exercise the
  // optional endpoint (UI falls back to local decode without it).
  const mixWav = readWav(projectDir, mixRel);
  fs.writeFileSync(
    path.join(dir, "peaks.json"),
    JSON.stringify({ version, peaks: peaks(mixWav, 900) }),
  );
  return meta;
}

/** Create v002, v003 (+peaks for v001) in a freshly copied fixture project. */
export function seedVersions(projectDir) {
  const v1meta = readMeta(projectDir, "v001");
  const mix = readWav(projectDir, v1meta.mix);
  fs.writeFileSync(
    path.join(projectDir, "renders/v001/peaks.json"),
    JSON.stringify({ version: "v001", peaks: peaks(mix, 900) }),
  );
  const v2 = renderVersion(projectDir, "v002", "v001", RECIPES.v002);
  renderVersion(projectDir, "v003", "v002", RECIPES.v003);
  return { v1meta, v2 };
}

/** Recipe + render for a simulated new version vNNN (used by the render job). */
export function renderNext(projectDir) {
  const renders = fs
    .readdirSync(path.join(projectDir, "renders"))
    .filter((d) => /^v\d+$/.test(d))
    .sort();
  const parent = renders[renders.length - 1];
  const n = renders.length + 1;
  const version = `v${String(n).padStart(3, "0")}`;
  return renderVersion(projectDir, version, parent, RECIPES.next(n));
}
