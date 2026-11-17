// Stem-mode clipping check on REAL renders, through the app itself.
//
//   muestra serve --project <copy of a project> --port 8801      (another terminal)
//   node scripts/stem-real-bench.mjs http://127.0.0.1:8801 [--browser chromium|firefox] [--csv out.csv]
//
// Opens the UI in headless Chromium (or Firefox), and for every version the
// server lists asks the app's own engine (`engine.renderOffline`) for what the
// speakers would get in stem mode with every track audible (the hottest case:
// a fader nudge), with and without the stem-bus limiter, plus the mastered mix
// for reference. All numbers include the master volume (default 0.85 → x0.7225)
// and the loudness-match offset, exactly like playback. Read-only on the project.
import fs from "node:fs";
import { chromium, firefox } from "@playwright/test";

const args = process.argv.slice(2);
const base = args.find((a) => a.startsWith("http")) ?? "http://127.0.0.1:8801";
const browserName = args.includes("--browser") ? args[args.indexOf("--browser") + 1] : "chromium";
const csvOut = args.includes("--csv") ? args[args.indexOf("--csv") + 1] : null;

const browser = await (browserName === "firefox" ? firefox : chromium).launch();
const page = await browser.newPage();
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
await page.goto(base + "/");
await page.waitForFunction(() => window.__muestra?.store?.state?.boot === "ready", null, { timeout: 60_000 });
const versions = await page.evaluate(() => window.__muestra.store.state.versions.map((v) => v.version));
console.log(`${versions.length} versions; engine bus: ${await page.evaluate(() => window.__muestra.engine.getSnapshot().stemBus)}`);

const rows = [];
for (const v of versions) {
  const r = await page.evaluate(async (v) => {
    const eng = window.__muestra.engine;
    const st = window.__muestra.store;
    const meta = st.state.metas[v];
    const first = meta.tracks[0].id;
    const hot = { tracks: { [first]: { mute: false, solo: false, gainDb: 0.1 } }, lanes: {} };
    const skip = 4800;
    const stat = (chs) => {
      let peak = 0;
      let acc = 0;
      let n = 0;
      for (const ch of chs)
        for (let i = skip; i < ch.length; i++) {
          const a = Math.abs(ch[i]);
          if (a > peak) peak = a;
          acc += ch[i] * ch[i];
          n++;
        }
      return { peak, rms: Math.sqrt(acc / Math.max(1, n)), clipped: 0 };
    };
    const t0 = performance.now();
    const lim = await eng.renderOffline(v, { mixer: hot });
    const raw = await eng.renderOffline(v, { mixer: hot, bypassBus: true });
    const mix = await eng.renderOffline(v, {});
    return { limited: stat(lim.channels), raw: stat(raw.channels), mix: stat(mix.channels), matchDb: lim.stemMatchDb, bus: lim.bus, ms: performance.now() - t0 };
  }, v);
  const db = (x) => 20 * Math.log10(Math.max(x, 1e-9));
  const row = {
    v,
    matchDb: r.matchDb,
    mixPeak: db(r.mix.peak),
    beforePeak: db(r.raw.peak),
    afterPeak: db(r.limited.peak),
    beforeRmsVsMix: db(r.raw.rms) - db(r.mix.rms),
    afterRmsVsMix: db(r.limited.rms) - db(r.mix.rms),
  };
  rows.push(row);
  console.log(
    `${v}  match ${row.matchDb?.toFixed(1).padStart(5)} dB | mix peak ${row.mixPeak.toFixed(1).padStart(5)} | stems BEFORE peak ${row.beforePeak.toFixed(1).padStart(5)} (rms vs mix ${row.beforeRmsVsMix.toFixed(2).padStart(5)}) | AFTER peak ${row.afterPeak.toFixed(1).padStart(5)} (rms vs mix ${row.afterRmsVsMix.toFixed(2).padStart(5)})  [${(r.ms / 1000).toFixed(1)}s]`,
  );
}
const f = (k, fn) => fn(...rows.map((r) => r[k]));
console.log(
  `\nSUMMARY ${rows.length} renders (peaks at the speaker, master volume ${await page.evaluate(() => window.__muestra.store.state.volume)}):` +
    `\n  stems before: mean peak ${(rows.reduce((s, r) => s + r.beforePeak, 0) / rows.length).toFixed(2)} dBFS, worst ${f("beforePeak", Math.max).toFixed(2)}, clipping (>0 dBFS) in ${rows.filter((r) => r.beforePeak > 0).length}/${rows.length}` +
    `\n  stems after : mean peak ${(rows.reduce((s, r) => s + r.afterPeak, 0) / rows.length).toFixed(2)} dBFS, worst ${f("afterPeak", Math.max).toFixed(2)}, clipping in ${rows.filter((r) => r.afterPeak >= 0).length}/${rows.length}` +
    `\n  mix peak    : worst ${f("mixPeak", Math.max).toFixed(2)} dBFS` +
    `\n  level vs the mastered mix (RMS): before mean ${(rows.reduce((s, r) => s + r.beforeRmsVsMix, 0) / rows.length).toFixed(2)} dB; after mean ${(rows.reduce((s, r) => s + r.afterRmsVsMix, 0) / rows.length).toFixed(2)} dB, worst |Δ| ${Math.max(...rows.map((r) => Math.abs(r.afterRmsVsMix))).toFixed(2)} dB`,
);
if (csvOut) {
  fs.writeFileSync(csvOut, ["version,match_db,mix_peak,before_peak,after_peak,before_rms_vs_mix,after_rms_vs_mix", ...rows.map((r) => [r.v, r.matchDb?.toFixed(2), r.mixPeak.toFixed(2), r.beforePeak.toFixed(2), r.afterPeak.toFixed(2), r.beforeRmsVsMix.toFixed(2), r.afterRmsVsMix.toFixed(2)].join(","))].join("\n"));
}
await browser.close();
