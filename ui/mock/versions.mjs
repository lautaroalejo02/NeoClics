// Derives a meaningful version history for the mock from the ONE real
// fixture render (fixtures/example-project/renders/v001, a 1-bar render).
//
//  * Every version is extended to MOCK_BARS bars (default 8) by tiling the
//    fixture audio, so the player, loops and comments have room.
//  * Derived versions change the stems with real DSP (filter sweeps, gain,
//    saturation) and rebuild mix.wav = master(sum of stems), so mixer
//    toggles and A/B both sound different.
//  * metrics.json loudness/spectral/masking values are MEASURED from the
//    generated audio (approximations: RMS-based "LUFS", no K-weighting), so
//    the loudness-match toggle has real differences to compensate.
//
// History: v001 -> v002 -> v003, and v004 branches from v002 (as after a
// `versions.restore`), so the versions panel shows a tree.

import fs from "node:fs";
import path from "node:path";
import { decodeWav, encodeWav, clone, gain, lowpass, highpass, saturate, sumInto } from "./wav.mjs";

const BARS = Math.max(1, Number(process.env.MOCK_BARS ?? 8));

const rd = (dir, rel) => decodeWav(fs.readFileSync(path.join(dir, rel)));
const wr = (dir, rel, wav, fmt) => {
  const p = path.join(dir, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, encodeWav(wav, fmt));
};
const readJson = (dir, rel) => JSON.parse(fs.readFileSync(path.join(dir, rel), "utf8"));
const writeJson = (dir, rel, obj) => fs.writeFileSync(path.join(dir, rel), JSON.stringify(obj, null, 2) + "\n");

function tile(wav, times) {
  if (times <= 1) return wav;
  const n = wav.frames;
  const data = wav.data.map((ch) => {
    const out = new Float32Array(n * times);
    for (let t = 0; t < times; t++) out.set(ch, t * n);
    return out;
  });
  return { ...wav, frames: n * times, data };
}

/** Time-varying one-pole lowpass: cutoff goes from f0 to f1 (exp) over [t0,t1] s. */
function sweepLowpass(wav, f0, f1, t0, t1) {
  const sr = wav.sampleRate;
  for (const ch of wav.data) {
    let y = 0;
    for (let i = 0; i < ch.length; i++) {
      const t = i / sr;
      const u = Math.min(1, Math.max(0, (t - t0) / (t1 - t0)));
      const f = f0 * (f1 / f0) ** u;
      const a = 1 - Math.exp((-2 * Math.PI * f) / sr);
      y += a * (ch[i] - y);
      ch[i] = y;
    }
  }
  return wav;
}

/** Master: drive into a soft clipper, normalise towards a target RMS. */
function master(sum, { targetDb, drive = 1.2 }) {
  const out = clone(sum);
  saturate(out, drive);
  const r = rmsDb(out);
  gain(out, targetDb - r);
  // ceiling ~ -0.8 dBFS
  const ceil = 10 ** (-0.8 / 20);
  for (const ch of out.data) for (let i = 0; i < ch.length; i++) ch[i] = Math.max(-ceil, Math.min(ceil, ch[i]));
  return out;
}

function rmsDb(w) {
  let acc = 0;
  let n = 0;
  for (const ch of w.data)
    for (let i = 0; i < ch.length; i++) {
      acc += ch[i] * ch[i];
      n++;
    }
  return 10 * Math.log10(Math.max(1e-12, acc / Math.max(1, n)));
}

function peakDb(w) {
  let p = 0;
  for (const ch of w.data) for (let i = 0; i < ch.length; i++) p = Math.max(p, Math.abs(ch[i]));
  return 20 * Math.log10(Math.max(1e-9, p));
}

function bandDb(w, lo, hi) {
  const c = clone(w);
  if (lo) highpass(c, lo);
  if (hi) lowpass(c, hi);
  return Math.round(rmsDb(c) * 10) / 10;
}

const r1 = (x) => Math.round(x * 10) / 10;

function measure(mix, stems, version, base, extraAlarms = []) {
  const lufs = r1(rmsDb(mix) - 0.691);
  const tp = r1(peakDb(mix) + 0.3);
  const tracks = {};
  const energy = {};
  for (const [id, w] of Object.entries(stems)) {
    tracks[id] = { low_db: bandDb(w, 20, 250), mid_db: bandDb(w, 250, 4000), high_db: bandDb(w, 4000, 0) };
    energy[id] = bandDb(w, 40, 150);
  }
  const ranked = Object.entries(energy).sort((a, b) => b[1] - a[1]);
  const alarms = [...extraAlarms];
  if (ranked.length > 1 && ranked[0][1] - ranked[1][1] < 2.5) {
    alarms.push({
      code: "low_end_masking",
      severity: "warn",
      message: `${ranked[0][0]} y ${ranked[1][0]} se pisan en 40-150 Hz (diferencia de ${r1(ranked[0][1] - ranked[1][1])} dB)`,
      track: ranked[1][0],
      at_s: null,
    });
  }
  if (tp > -1) {
    alarms.push({ code: "true_peak", severity: "warn", message: `True peak de ${tp} dBTP: muy cerca de 0`, track: null, at_s: null });
  }
  return {
    ...base,
    version,
    loudness: { integrated_lufs: lufs, true_peak_dbtp: tp, clipping: tp > 0, clipped_samples: 0 },
    spectral: {
      bands_hz: base.spectral.bands_hz,
      mix: { low_db: bandDb(mix, 20, 250), mid_db: bandDb(mix, 250, 4000), high_db: bandDb(mix, 4000, 0) },
      tracks,
    },
    low_end_masking: { band_hz: [40, 150], energy_db: energy, dominant_track: ranked[0]?.[0] ?? null },
    alarms,
  };
}

function retarget(meta, version) {
  const swap = (p) => p.replace(/^renders\/v\d+\//, `renders/${version}/`);
  return {
    ...meta,
    version,
    mix: swap(meta.mix),
    tracks: meta.tracks.map((t) => ({ ...t, stem: swap(t.stem), lanes: (t.lanes ?? []).map((l) => ({ ...l, stem: swap(l.stem) })) })),
  };
}

/** Load a version's stems (track + lane) keyed by id / "track.lane". */
function loadStems(dir, meta) {
  const tracks = {};
  const lanes = {};
  for (const t of meta.tracks) {
    tracks[t.id] = rd(dir, t.stem);
    for (const l of t.lanes ?? []) lanes[`${t.id}.${l.id}`] = rd(dir, l.stem);
  }
  return { tracks, lanes };
}

function writeVersion(dir, meta, stems, mix, metrics) {
  const vdir = path.join(dir, "renders", meta.version);
  fs.mkdirSync(path.join(vdir, "stems"), { recursive: true });
  for (const t of meta.tracks) {
    wr(dir, t.stem, stems.tracks[t.id], "float32");
    for (const l of t.lanes ?? []) wr(dir, l.stem, stems.lanes[`${t.id}.${l.id}`], "float32");
  }
  wr(dir, meta.mix, mix, "pcm24");
  writeJson(dir, `renders/${meta.version}/meta.json`, meta);
  writeJson(dir, `renders/${meta.version}/metrics.json`, metrics);
}

/** A simple synth bass line in F#m (the fixture's bass stem is silent:
 *  the instrument track needs a plugin host that the fixture doesn't run). */
function synthBass(frames, sr, bpm) {
  const notes = [30, 30, 33, 30, 28, 30, 37, 28]; // F#1 F#1 A1 F#1 E1 F#1 C#2 E1 (8ths)
  const step = (60 / bpm) / 2;
  const out = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    const t = i / sr;
    const k = Math.floor(t / step);
    const tn = t - k * step;
    const f = 440 * 2 ** ((notes[k % notes.length] - 69) / 12);
    const env = Math.min(1, tn / 0.004) * Math.exp(-tn * 5.5);
    const ph = 2 * Math.PI * f * t;
    out[i] = 0.32 * env * (Math.sin(ph) + 0.35 * Math.sin(2 * ph) + 0.12 * Math.sin(3 * ph));
  }
  return out;
}

/** Extend the fixture v001 in place to BARS bars (tiled audio). */
function extendV001(dir) {
  const meta = readJson(dir, "renders/v001/meta.json");
  const bars = meta.range_bars[1] - meta.range_bars[0];
  const times = Math.max(1, Math.round(BARS / bars));
  if (times === 1) return meta;
  const stems = loadStems(dir, meta);
  for (const k of Object.keys(stems.tracks)) stems.tracks[k] = tile(stems.tracks[k], times);
  for (const k of Object.keys(stems.lanes)) stems.lanes[k] = tile(stems.lanes[k], times);
  const frames = Object.values(stems.tracks)[0].frames;
  if (stems.tracks.bass && rmsDb(stems.tracks.bass) < -90) {
    const b = synthBass(frames, stems.tracks.bass.sampleRate, meta.bpm);
    stems.tracks.bass.data = stems.tracks.bass.data.map(() => Float32Array.from(b));
  }
  const sum = clone(Object.values(stems.tracks)[0]);
  for (const ch of sum.data) ch.fill(0);
  for (const w of Object.values(stems.tracks)) sumInto(sum, w);
  const mix = master(sum, { targetDb: -16.5, drive: 1.1 });
  const newMeta = {
    ...meta,
    range_bars: [meta.range_bars[0], meta.range_bars[0] + bars * times],
    duration_s: (meta.duration_s ?? mix.frames / mix.sampleRate) * times,
  };
  const base = readJson(dir, "renders/v001/metrics.json");
  const metrics = measure(mix, stems.tracks, "v001", base, [
    { code: "click", severity: "info", message: "Posible click en el borde del chop c1 (compás 5)", track: "sample", at_s: r1(newMeta.duration_s / 2) },
  ]);
  writeVersion(dir, newMeta, stems, mix, metrics);
  return newMeta;
}

const RECIPES = {
  v002: {
    parent: "v001",
    minutes: 9,
    request: "Más filtrado en la intro, el bajo más gordo y el sample medio tono más arriba",
    summary:
      "Filtro low-pass automatizado en el sample (compases 1-4, de 600 Hz a abierto), bajo +3.5 dB con un poco de saturación y el chop c1 un semitono más arriba.",
    diff: [
      { path: "automation[0].points", from: [[1, 800], [5, 18000]], to: [[1, 600], [5, 18000]] },
      { path: "tracks.bass.gain_db", from: 0, to: 3.5 },
      { path: "tracks.bass.fx[0]", from: null, to: { type: "saturation", drive_db: 4 } },
      { path: "chops.c1.warp.pitch_semitones", from: 1, to: 2 },
    ],
    apply(st, spb) {
      sweepLowpass(st.tracks.sample, 600, 16000, 0, 4 * spb * 4);
      saturate(gain(st.tracks.bass, 3.5), 1.6);
    },
    targetDb: -11.8,
    alarms: [
      { code: "low_end_masking", severity: "warn", message: "El bajo domina 40-150 Hz y tapa el cuerpo del sample", track: "bass", at_s: 2.0 },
      { code: "alignment", severity: "info", message: "Transientes del sample hasta 4 ms corridos de la grilla", track: "sample", at_s: 8.0 },
    ],
  },
  v003: {
    parent: "v002",
    minutes: 17,
    request: "Recuperá brillo y que la batería pegue más",
    summary: "Abrí el filtro del sample del todo, +1.5 dB a la batería y más empuje en el master (saturación suave).",
    diff: [
      { path: "automation[0]", from: { target: "tracks.sample.fx.lp.cutoff_hz" }, to: null },
      { path: "tracks.drums.gain_db", from: 0, to: 1.5 },
      { path: "master.fx[2]", from: null, to: { type: "saturation", drive_db: 6 } },
      { path: "master.target_lufs", from: -10, to: -9 },
    ],
    apply(st) {
      gain(st.tracks.drums, 1.5);
      for (const k of Object.keys(st.lanes)) if (k.startsWith("drums.")) gain(st.lanes[k], 1.5);
    },
    targetDb: -9.6,
    drive: 1.9,
    alarms: [{ code: "click", severity: "info", message: "Posible click en el borde del chop c1", track: "sample", at_s: 6.0 }],
  },
  v004: {
    parent: "v002",
    minutes: 26,
    request: "Volvé a la v002 pero con el sample más atrás y sin el semitono extra",
    summary: "Partí de la v002: sample −3 dB y un high-pass a 220 Hz para dejarle lugar al bombo; el chop vuelve a +1 semitono.",
    diff: [
      { path: "chops.c1.warp.pitch_semitones", from: 2, to: 1 },
      { path: "tracks.sample.gain_db", from: -2, to: -5 },
      { path: "tracks.sample.fx.hp.cutoff_hz", from: 150, to: 220 },
    ],
    apply(st) {
      highpass(gain(st.tracks.sample, -3), 220);
    },
    targetDb: -12.6,
  },
};

function deriveVersion(dir, version, recipe, rootCreated) {
  const parentMeta = readJson(dir, `renders/${recipe.parent}/meta.json`);
  const stems = loadStems(dir, parentMeta);
  const spb = 60 / parentMeta.bpm;
  recipe.apply(stems, spb);
  const first = Object.values(stems.tracks)[0];
  const sum = clone(first);
  for (const ch of sum.data) ch.fill(0);
  for (const w of Object.values(stems.tracks)) sumInto(sum, w);
  const mix = master(sum, { targetDb: recipe.targetDb, drive: recipe.drive });
  const meta = {
    ...retarget(parentMeta, version),
    parent: recipe.parent,
    git_commit: (version.repeat(10) + "0".repeat(40)).replace(/[^0-9a-f]/g, "0").slice(0, 40),
    created_at: new Date(rootCreated + recipe.minutes * 60_000).toISOString(),
    request: recipe.request,
    agent: "claude-code",
    summary: recipe.summary,
    diff: recipe.diff,
    render_time_s: 4.1 + Math.random() * 2,
  };
  const base = readJson(dir, `renders/${recipe.parent}/metrics.json`);
  writeVersion(dir, meta, stems, mix, measure(mix, stems.tracks, version, base, recipe.alarms ?? []));
  fs.copyFileSync(path.join(dir, "renders", recipe.parent, "song.yaml"), path.join(dir, "renders", version, "song.yaml"));
  return meta;
}

/**
 * Seed the copied project: extend v001, derive v002..v004. Timestamps are
 * anchored `hoursAgo` before now so "hace X min" reads naturally.
 */
export function seedVersions(dir, { hoursAgo = 1 } = {}) {
  extendV001(dir);
  const root = Date.now() - hoursAgo * 3600_000;
  const m1 = readJson(dir, "renders/v001/meta.json");
  m1.created_at = new Date(root).toISOString();
  writeJson(dir, "renders/v001/meta.json", m1);
  for (const v of ["v002", "v003", "v004"]) deriveVersion(dir, v, RECIPES[v], root);
  return { root, durationS: m1.duration_s };
}

/** Simulated agent render (the `render` job): a new version off the latest. */
export function renderNext(dir) {
  const ids = fs
    .readdirSync(path.join(dir, "renders"))
    .filter((d) => /^v\d{3,}$/.test(d))
    .sort();
  const parent = ids[ids.length - 1];
  const n = Number(parent.slice(1)) + 1;
  const version = `v${String(n).padStart(3, "0")}`;
  const k = n % 3;
  const recipe = {
    parent,
    minutes: 0,
    request: ["Que respire más con el bombo", "Más brillo arriba", "El bajo un toque más adelante"][k],
    summary: [
      "Sidechain del sample más profundo (0.6 → 0.75) y release más corto.",
      "Shelf de agudos +2 dB en el sample y hats +1 dB.",
      "Bajo +1.5 dB y un poco más de saturación.",
    ][k],
    diff: [
      [{ path: "tracks.sample.fx.sc.depth", from: 0.6, to: 0.75 }],
      [{ path: "tracks.drums.pattern.lanes.ohat.gain_db", from: 0, to: 1 }],
      [{ path: "tracks.bass.gain_db", from: 3.5, to: 5 }],
    ][k],
    apply(st) {
      if (k === 0) gain(st.tracks.sample, -1);
      if (k === 1) gain(st.tracks.drums, 0.8);
      if (k === 2) saturate(gain(st.tracks.bass, 1.5), 1.3);
    },
    targetDb: -11 + k * 0.4,
  };
  const meta = deriveVersion(dir, version, recipe, Date.now());
  return meta;
}
