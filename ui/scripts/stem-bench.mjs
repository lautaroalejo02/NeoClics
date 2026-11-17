// Stem-mode loudness / clipping bench (WP14 finding 1).
//
//   node scripts/stem-bench.mjs <project-dir>... [--only v001] [--csv out.csv]
//
// For every render of the given projects: sums the pre-master track stems,
// level-matches the sum to mix.wav by RMS (what the app did before the
// limiter), and measures what a browser outputs through each candidate
// stem-bus chain, rendered OFFLINE in headless Chromium (OfflineAudioContext,
// the real DynamicsCompressorNode and the real AudioWorklet limiter):
//   none        straight to the output (the old behaviour)
//   comp-*      DynamicsCompressorNode (+ WaveShaper safety), several settings
//   lim-*       src/audio/limiter.ts as an AudioWorklet, several releases
// Reported per chain: output peak (dBFS), "distance to the mix" (residual
// after the best scalar gain, dB; lower = sounds more like the mastered mix),
// RMS vs the mix (dB) and the share of hard-clipped samples.
// Read-only on the projects.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import ts from "typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const dirs = args.filter((a) => !a.startsWith("--") && fs.existsSync(a) && fs.statSync(a).isDirectory());
const only = args.includes("--only") ? args[args.indexOf("--only") + 1] : null;
const csvOut = args.includes("--csv") ? args[args.indexOf("--csv") + 1] : null;

function readWav(file) {
  const b = fs.readFileSync(file);
  let p = 12;
  let fmt = null;
  let data = null;
  while (p + 8 <= b.length) {
    const id = b.toString("ascii", p, p + 4);
    const size = b.readUInt32LE(p + 4);
    if (id === "fmt ") fmt = { tag: b.readUInt16LE(p + 8), ch: b.readUInt16LE(p + 10), sr: b.readUInt32LE(p + 12), bits: b.readUInt16LE(p + 22) };
    if (id === "data") {
      data = b.subarray(p + 8, Math.min(b.length, p + 8 + size));
      break;
    }
    p += 8 + size + (size & 1);
  }
  const bytes = fmt.bits / 8;
  const frames = Math.floor(data.length / (bytes * fmt.ch));
  const chs = Array.from({ length: fmt.ch }, () => new Float32Array(frames));
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < fmt.ch; c++) {
      const o = (i * fmt.ch + c) * bytes;
      let v;
      if (fmt.tag === 3 || (fmt.tag === 0xfffe && fmt.bits === 32)) v = fmt.bits === 64 ? data.readDoubleLE(o) : data.readFloatLE(o);
      else if (fmt.bits === 16) v = data.readInt16LE(o) / 32768;
      else if (fmt.bits === 24) v = data.readIntLE(o, 3) / 8388608;
      else v = data.readInt32LE(o) / 2147483648;
      chs[c][i] = v;
    }
  }
  return { sr: fmt.sr, chs, frames };
}

const stereo = (w) => (w.chs.length === 1 ? [w.chs[0], w.chs[0]] : [w.chs[0], w.chs[1]]);

// ---- collect cases
const cases = [];
for (const dir of dirs) {
  const rdir = path.join(dir, "renders");
  for (const v of fs.readdirSync(rdir).sort()) {
    const metaP = path.join(rdir, v, "meta.json");
    if (!fs.existsSync(metaP) || (only && v !== only)) continue;
    const meta = JSON.parse(fs.readFileSync(metaP, "utf8"));
    if (!fs.existsSync(path.join(dir, meta.mix))) continue;
    cases.push({ id: `${path.basename(dir)}/${v}`, dir, meta });
  }
}
console.log(`${cases.length} renders`);

// ---- tiny server: raw float32 blobs + the limiter module (as ESM)
const blobs = new Map();
const limiterJs = ts.transpileModule(fs.readFileSync(path.join(HERE, "../src/audio/limiter.ts"), "utf8"), {
  compilerOptions: { target: "ES2022", module: "ES2022" },
}).outputText;
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split("?")[0]);
  res.setHeader("Access-Control-Allow-Origin", "*");
  if (u === "/limiter.js") {
    res.setHeader("content-type", "text/javascript");
    res.end(limiterJs);
  } else if (u === "/") {
    res.setHeader("content-type", "text/html");
    res.end("<!doctype html><title>bench</title>");
  } else if (blobs.has(u)) {
    res.setHeader("content-type", "application/octet-stream");
    res.end(blobs.get(u));
  } else {
    res.statusCode = 404;
    res.end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(`${base}/`);
page.on("console", (m) => console.log("[page]", m.text()));

const CHAINS = [
  { name: "none" },
  { name: "comp-b", comp: { threshold: -6, knee: 2, ratio: 20, attack: 0.001, release: 0.1 }, shaper: true },
  { name: "lim-60", worklet: { releaseMs: 60 } },
  { name: "glue2.5", comp: { threshold: -14, knee: 12, ratio: 2.5, attack: 0.01, release: 0.15 }, shaper: true },
  { name: "glue3", comp: { threshold: -18, knee: 12, ratio: 3, attack: 0.01, release: 0.15 }, shaper: true },
  { name: "glue2.5+lim", comp: { threshold: -14, knee: 12, ratio: 2.5, attack: 0.01, release: 0.15 }, worklet: { releaseMs: 60 } },
  { name: "glue3+lim", comp: { threshold: -18, knee: 12, ratio: 3, attack: 0.01, release: 0.15 }, worklet: { releaseMs: 60 } },
  { name: "glue4+lim", comp: { threshold: -20, knee: 10, ratio: 4, attack: 0.005, release: 0.1 }, worklet: { releaseMs: 60 } },
];

const rows = [];
for (const c of cases) {
  const mix = readWav(path.join(c.dir, c.meta.mix));
  const stems = c.meta.tracks.map((t) => readWav(path.join(c.dir, t.stem)));
  if (stems.length === 0) continue;
  const len = Math.min(mix.frames, ...stems.map((s) => s.frames));
  const sum = [new Float32Array(len), new Float32Array(len)];
  for (const s of stems) {
    const st = stereo(s);
    for (let ch = 0; ch < 2; ch++) for (let i = 0; i < len; i++) sum[ch][i] += st[ch][i];
  }
  const mx = stereo(mix).map((a) => a.subarray(0, len));
  const rmsOf = (a) => {
    let s = 0;
    for (const ch of a) for (let i = 0; i < len; i++) s += ch[i] * ch[i];
    return Math.sqrt(s / (len * a.length));
  };
  // the app's level match (RMS, clamped +-18 dB)
  const g = Math.min(10 ** (18 / 20), Math.max(10 ** (-18 / 20), rmsOf(mx) / rmsOf(sum)));
  const key = c.id.replace(/\W/g, "_");
  const toBuf = (a) => {
    const f = new Float32Array(len * 2);
    f.set(a[0], 0);
    f.set(a[1], len);
    return Buffer.from(f.buffer);
  };
  blobs.set(`/${key}.sum`, toBuf(sum));
  blobs.set(`/${key}.mix`, toBuf(mx));
  const res = await page.evaluate(runInPage, { base, key, len, sr: mix.sr, g, chains: CHAINS });
  blobs.delete(`/${key}.sum`);
  blobs.delete(`/${key}.mix`);
  rows.push({ id: c.id, gDb: 20 * Math.log10(g), res });
  const f = (n) => `${res[n].peak.toFixed(1).padStart(5)}/${res[n].dist.toFixed(1).padStart(5)}/${res[n].rmsVsMix.toFixed(1).padStart(5)}`;
  console.log(
    `${c.id.padEnd(22)} match ${(20 * Math.log10(g)).toFixed(1).padStart(5)} dB | mix peak ${res.unprocessed.mixPeak.toFixed(1)} | ` +
      CHAINS.map((ch) => `${ch.name} ${f(ch.name)}`).join(" | "),
  );
}
console.log("\ncolumns: peak dBFS / distance-to-mix dB / RMS vs mix dB");
if (rows.length) {
  const avg = (name, k) => rows.reduce((s, r) => s + r.res[name][k], 0) / rows.length;
  const worst = (name, k) => Math.max(...rows.map((r) => r.res[name][k]));
  console.log("\nSUMMARY over", rows.length, "renders (mean peak / worst peak / mean distance / mean |RMS vs mix| / worst clip%)");
  for (const ch of CHAINS) {
    const mabs = rows.reduce((s, r) => s + Math.abs(r.res[ch.name].rmsVsMix), 0) / rows.length;
    console.log(
      `${ch.name.padEnd(8)} ${avg(ch.name, "peak").toFixed(2).padStart(6)} ${worst(ch.name, "peak").toFixed(2).padStart(6)} ${avg(ch.name, "dist").toFixed(2).padStart(6)} ${mabs.toFixed(2).padStart(5)} ${worst(ch.name, "clipPct").toFixed(3)}`,
    );
  }
}
if (csvOut) {
  const head = ["render", "match_db", "mix_peak", ...CHAINS.flatMap((c) => [`${c.name}_peak`, `${c.name}_dist`, `${c.name}_rms`, `${c.name}_clip_pct`])];
  const lines = [head.join(",")];
  for (const r of rows)
    lines.push(
      [r.id, r.gDb.toFixed(2), r.res.unprocessed.mixPeak.toFixed(2), ...CHAINS.flatMap((c) => [r.res[c.name].peak.toFixed(2), r.res[c.name].dist.toFixed(2), r.res[c.name].rmsVsMix.toFixed(2), r.res[c.name].clipPct.toFixed(3)])].join(","),
    );
  fs.writeFileSync(csvOut, lines.join("\n"));
}
await browser.close();
server.close();

// Runs inside Chromium.
async function runInPage({ base, key, len, sr, g, chains }) {
  const load = async (u) => {
    const b = await (await fetch(`${base}/${u}`)).arrayBuffer();
    const f = new Float32Array(b);
    return [f.subarray(0, len), f.subarray(len, 2 * len)];
  };
  const sum = await load(`${key}.sum`);
  const mix = await load(`${key}.mix`);
  const db = (x) => 20 * Math.log10(Math.max(x, 1e-9));
  const rms = (a) => {
    let s = 0;
    for (const ch of a) for (let i = 0; i < ch.length; i++) s += ch[i] * ch[i];
    return Math.sqrt(s / (a.length * a[0].length));
  };
  const peak = (a) => Math.max(...a.map((ch) => ch.reduce((m, v) => Math.max(m, Math.abs(v)), 0)));
  const out = {};
  for (const chain of chains) {
    const ctx = new OfflineAudioContext(2, len, sr);
    const buf = ctx.createBuffer(2, len, sr);
    buf.copyToChannel(sum[0], 0);
    buf.copyToChannel(sum[1], 1);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const gain = ctx.createGain();
    gain.gain.value = g;
    src.connect(gain);
    let tail = gain;
    let delaySamples = 0;
    if (chain.comp) {
      const comp = ctx.createDynamicsCompressor();
      for (const k of Object.keys(chain.comp)) comp[k].value = chain.comp[k];
      tail.connect(comp);
      tail = comp;
      delaySamples += Math.round(0.006 * sr);
    }
    if (chain.worklet) {
      await ctx.audioWorklet.addModule(`${base}/limiter.js`);
      const node = new AudioWorkletNode(ctx, "muestra-limiter", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [2],
        channelCount: 2,
        channelCountMode: "explicit",
        processorOptions: chain.worklet,
      });
      tail.connect(node);
      tail = node;
      delaySamples += Math.round((5 * sr) / 1000);
    }
    if (chain.shaper) {
      const shaper = ctx.createWaveShaper();
      const N = 2049;
      const curve = new Float32Array(N);
      const knee = 0.891;
      for (let i = 0; i < N; i++) {
        const x = (i / (N - 1)) * 2 - 1;
        const a = Math.abs(x);
        const y = a <= knee ? a : knee + (0.99 - knee) * Math.tanh((a - knee) / (0.99 - knee));
        curve[i] = Math.sign(x) * y;
      }
      shaper.curve = curve;
      shaper.oversample = "2x";
      tail.connect(shaper);
      tail = shaper;
    }
    tail.connect(ctx.destination);
    src.start(0);
    const rendered = await ctx.startRendering();
    const y = [rendered.getChannelData(0), rendered.getChannelData(1)];
    // align by cross-correlation within +-200 samples of the nominal latency
    let best = delaySamples;
    if (delaySamples) {
      const start = Math.min(Math.max(0, len - 30000), Math.floor(len / 3));
      let bc = -Infinity;
      for (let lag = Math.max(0, delaySamples - 200); lag <= delaySamples + 200; lag++) {
        let c = 0;
        for (let i = start; i < start + 20000 && i + lag < len; i += 3) c += sum[0][i] * y[0][i + lag];
        if (c > bc) {
          bc = c;
          best = lag;
        }
      }
    }
    const n = len - best;
    const ya = y.map((ch) => ch.subarray(best, best + n));
    let num = 0;
    let den = 0;
    let mm = 0;
    for (let c = 0; c < 2; c++)
      for (let i = 0; i < n; i++) {
        num += ya[c][i] * mix[c][i];
        den += ya[c][i] * ya[c][i];
        mm += mix[c][i] * mix[c][i];
      }
    const a = num / Math.max(den, 1e-12);
    const resid = mm - 2 * a * num + a * a * den;
    let clipped = 0;
    for (let c = 0; c < 2; c++) for (let i = 0; i < n; i++) if (Math.abs(ya[c][i]) >= 0.9999) clipped++;
    out[chain.name] = {
      peak: db(peak(ya)),
      rmsVsMix: db(rms(ya)) - db(rms(mix.map((x) => x.subarray(0, n)))),
      dist: db(Math.sqrt(Math.max(resid, 0) / mm)),
      clipPct: (100 * clipped) / (2 * n),
      lag: best,
    };
  }
  out.unprocessed = { peak: db(peak(sum) * g), mixPeak: db(peak(mix)) };
  return out;
}
