// Measurements of the arrangement editor on a real server (WP16 report):
//   node scripts/editor-measure.mjs <url> perf       frame times, edit latency, memory growth
//   node scripts/editor-measure.mjs <url> fidelity   browser mix vs the version's real stems
import { chromium } from "@playwright/test";

const [url, mode = "perf"] = process.argv.slice(2);
const b = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required", "--enable-precise-memory-info", "--js-flags=--expose-gc"] });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const p = await ctx.newPage();
p.on("pageerror", (e) => console.log("pageerror", e.message));
await p.addInitScript(() => localStorage.setItem("muestra.view", "arreglo"));
await p.goto(url);
await p.waitForSelector('[data-testid="timeline"]', { timeout: 60000 });
const M = "window.__muestra";
await p.waitForFunction(`${M}.editor.state.status === "ready"`, null, { timeout: 60000 });
const t0 = Date.now();
await p.waitForFunction(`Object.values(${M}.editor.state.trackStatus).every((s) => s.state === "exact")`, null, { timeout: 240000, polling: 500 });
console.log(`all tracks exact (wet) after ${((Date.now() - t0) / 1000).toFixed(1)} s`);

const pct = (xs, q) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : NaN;
};

if (mode === "perf") {
  const info = await p.evaluate(() => {
    const m = window.__muestra;
    const s = m.editor.state.song;
    return { tracks: s.tracks.length, bars: s.meta.length_bars, events: m.engine.editorArrangement.events.length };
  });
  console.log("song", info);
  // play + scroll/zoom continuously for 4 s, measuring real frame intervals and canvas draw times
  await p.evaluate(() => window.__muestra.editor.togglePlay());
  const box = await p.getByTestId("timeline").boundingBox();
  await p.mouse.move(box.x + 300, box.y + 200);
  await p.evaluate(() => {
    window.__frames = [];
    let last = performance.now();
    const loop = (t) => {
      window.__frames.push(t - last);
      last = t;
      if (window.__frames.length < 100000) requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    window.__muestra.perf.drawTimes.length = 0;
  });
  for (let i = 0; i < 120; i++) {
    await p.mouse.wheel(i % 40 < 20 ? 60 : -60, 0);
    if (i % 30 === 0) await p.keyboard.press(i % 60 === 0 ? "+" : "-");
    await p.waitForTimeout(16);
  }
  const fr = await p.evaluate(() => ({ frames: window.__frames.slice(5), draws: [...window.__muestra.perf.drawTimes] }));
  console.log(`scroll+zoom while playing: frame interval p50 ${pct(fr.frames, 0.5).toFixed(1)} ms, p95 ${pct(fr.frames, 0.95).toFixed(1)} ms, max ${Math.max(...fr.frames).toFixed(1)} ms; canvas draw p50 ${pct(fr.draws, 0.5).toFixed(2)} ms, p95 ${pct(fr.draws, 0.95).toFixed(2)} ms, max ${Math.max(...fr.draws).toFixed(2)} ms (${fr.draws.length} draws)`);

  // 300 edits (add/remove hits, move notes) while playing: latency, memory
  const mem = () => p.evaluate(() => { globalThis.gc?.(); return { heap: performance.memory?.usedJSHeapSize ?? 0, pieces: window.__muestra.editor.memoryBytes(), scheduled: window.__muestra.engine.editorPlayer?.scheduledCount ?? 0 }; });
  const m0 = await mem();
  const lat = await p.evaluate(async () => {
    const ed = window.__muestra.editor;
    const out = [];
    for (let i = 0; i < 300; i++) {
      const s = ed.state.song;
      const bar = 1 + (i % 32);
      const step = (i * 3) % 16;
      const lanes = s.tracks.find((t) => t.id === "drums").pattern.lanes;
      const ov = lanes.chat.overrides?.[String(bar)];
      const tile = "x.x.x.x.x.x.x.x.";
      const chars = (ov ?? tile).split("");
      chars[step] = chars[step] === "." ? "x" : ".";
      const next = chars.join("");
      const ops = next === tile ? [{ op: "unset", path: `tracks.drums.pattern.lanes.chat.overrides.${bar}`, expect: ov ?? null }] : [{ op: "set", path: `tracks.drums.pattern.lanes.chat.overrides.${bar}`, value: next, expect: ov ?? null }];
      const t = performance.now();
      ed.edit(ops, "perf");
      out.push(performance.now() - t);
      if (i % 20 === 0) await new Promise((r) => setTimeout(r, 30));
    }
    return out;
  });
  console.log(`300 edits while playing: apply+rebuild+reschedule p50 ${pct(lat, 0.5).toFixed(2)} ms, p95 ${pct(lat, 0.95).toFixed(2)} ms, max ${Math.max(...lat).toFixed(2)} ms`);
  await p.waitForFunction(`${M}.editor.state.inFlight === 0`, null, { timeout: 120000, polling: 200 });
  await p.waitForTimeout(3000);
  const m1 = await mem();
  // undo all 300 (back to the start), then measure again
  await p.evaluate(async () => {
    for (let i = 0; i < 300; i++) window.__muestra.editor.undoEdit();
  });
  await p.waitForFunction(`${M}.editor.state.inFlight === 0`, null, { timeout: 180000, polling: 200 });
  await p.waitForFunction(`Object.values(${M}.editor.state.trackStatus).every((s) => s.state === "exact")`, null, { timeout: 240000, polling: 500 });
  await p.waitForTimeout(3000);
  const m2 = await mem();
  const mb = (x) => (x / 1048576).toFixed(1);
  console.log(`memory: JS heap ${mb(m0.heap)} -> ${mb(m1.heap)} (after 300 edits) -> ${mb(m2.heap)} MB (after 300 undos); decoded pieces ${mb(m0.pieces)} -> ${mb(m1.pieces)} -> ${mb(m2.pieces)} MB; scheduled sources ${m0.scheduled} -> ${m1.scheduled} -> ${m2.scheduled}`);
  const t = await p.evaluate(() => window.__muestra.editor.timings);
  console.log(`wet render (request -> buffer swapped) ms: p50 ${pct(t.wet, 0.5).toFixed(0)}, max ${Math.max(...t.wet).toFixed(0)} (${t.wet.length}); dry pieces ms p50 ${pct(t.pieces, 0.5).toFixed(0)}`);
}

if (mode === "fidelity") {
  // The working song must equal the latest version (no edits): compare per track.
  const res = await p.evaluate(async () => {
    const m = window.__muestra;
    const ed = m.editor;
    const versions = m.store.state.versions;
    const v = versions[versions.length - 1].version;
    const meta = m.store.state.metas[v];
    m.engine.setEditorLevel(0);
    const bands = [[20, 120], [120, 500], [500, 2000], [2000, 6000], [6000, 16000]];
    const spectrum = (x, sr) => {
      // energy per band from a 16384-point Hann FFT averaged over hops (naive DFT on bins of interest)
      const N = 4096;
      const out = bands.map(() => 0);
      const hann = Float32Array.from({ length: N }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N));
      for (let off = 0; off + N < x.length; off += N * 4) {
        const re = new Float64Array(N);
        const im = new Float64Array(N);
        for (let i = 0; i < N; i++) re[i] = x[off + i] * hann[i];
        // radix-2 FFT
        for (let i = 1, j = 0; i < N; i++) {
          let bit = N >> 1;
          for (; j & bit; bit >>= 1) j ^= bit;
          j ^= bit;
          if (i < j) {
            [re[i], re[j]] = [re[j], re[i]];
            [im[i], im[j]] = [im[j], im[i]];
          }
        }
        for (let len = 2; len <= N; len <<= 1) {
          const ang = (-2 * Math.PI) / len;
          for (let i = 0; i < N; i += len) {
            for (let k = 0; k < len / 2; k++) {
              const wr = Math.cos(ang * k), wi = Math.sin(ang * k);
              const ur = re[i + k], ui = im[i + k];
              const vr = re[i + k + len / 2] * wr - im[i + k + len / 2] * wi;
              const vi = re[i + k + len / 2] * wi + im[i + k + len / 2] * wr;
              re[i + k] = ur + vr; im[i + k] = ui + vi;
              re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
            }
          }
        }
        bands.forEach(([lo, hi], bi) => {
          for (let k = Math.floor((lo * N) / sr); k < Math.min(N / 2, Math.ceil((hi * N) / sr)); k++) out[bi] += re[k] * re[k] + im[k] * im[k];
        });
      }
      return out;
    };
    const rmsDb = (chs) => {
      let s = 0, n = 0;
      for (const c of chs) for (let i = 0; i < c.length; i++) { s += c[i] * c[i]; n++; }
      return 10 * Math.log10(s / n + 1e-20);
    };
    const results = [];
    for (const forceDry of [false, true]) {
      for (const tr of meta.tracks) {
        const res = await fetch(`/files/${tr.stem}`);
        const off = new OfflineAudioContext(2, 1, meta.sample_rate);
        const ref = await off.decodeAudioData(await res.arrayBuffer());
        const refCh = [ref.getChannelData(0), ref.getChannelData(1)];
        const saved = new Map(ed.piecesSet().wet);
        if (forceDry) { ed.piecesSet().wet.clear(); }
        ed.state.solo.forEach((s) => ed.setSolo(s, false));
        ed.setSolo(tr.id, true);
        const out = await m.engine.renderEditorOffline({ bypassBus: true });
        ed.setSolo(tr.id, false);
        if (forceDry) { for (const [k, x] of saved) ed.piecesSet().wet.set(k, x); ed.rebuild(); }
        const n = Math.min(out.channels[0].length, refCh[0].length);
        const a = out.channels.map((c) => c.subarray(0, n));
        const r = refCh.map((c) => c.subarray(0, n));
        let err = 0, num = 0;
        for (let c = 0; c < 2; c++) for (let i = 0; i < n; i++) { const d = a[c][i] - r[c][i]; err += d * d; num += r[c][i] * r[c][i]; }
        const sa = spectrum(a[0], meta.sample_rate);
        const sr = spectrum(r[0], meta.sample_rate);
        results.push({
          mode: forceDry ? "dry (instant)" : "exact (wet)",
          track: tr.id,
          levelDiffDb: +(rmsDb(a) - rmsDb(r)).toFixed(2),
          residualDb: +(10 * Math.log10(err / (num + 1e-20) + 1e-20)).toFixed(1),
          bandDiffDb: sa.map((x, i) => +(10 * Math.log10((x + 1e-12) / (sr[i] + 1e-12))).toFixed(1)),
        });
      }
    }
    return { version: v, results };
  });
  console.log(`browser mix vs ${res.version} stems (track soloed, no master, bus bypassed); bands 20-120/120-500/500-2k/2k-6k/6k-16k Hz`);
  for (const r of res.results) console.log(`${r.mode.padEnd(14)} ${r.track.padEnd(8)} level ${String(r.levelDiffDb).padStart(6)} dB  residual ${String(r.residualDb).padStart(6)} dB  bands ${r.bandDiffDb.join(" / ")}`);
}
await b.close();
