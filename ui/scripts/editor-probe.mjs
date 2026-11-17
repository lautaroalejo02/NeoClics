// Debug: where does the browser playback of a wet stem differ from the stem?
//   node scripts/editor-probe.mjs <url> <track>
import { chromium } from "@playwright/test";

const [url, track = "keys"] = process.argv.slice(2);
const b = await chromium.launch();
const p = await (await b.newContext()).newPage();
await p.addInitScript(() => localStorage.setItem("muestra.view", "arreglo"));
await p.goto(url);
await p.waitForFunction(`window.__muestra?.editor.state.status === "ready"`, null, { timeout: 60000 });
await p.waitForFunction(`Object.values(window.__muestra.editor.state.trackStatus).every((s) => s.state === "exact")`, null, { timeout: 240000, polling: 500 });
const out = await p.evaluate(async (tid) => {
  const m = window.__muestra;
  const ed = m.editor;
  m.engine.setEditorLevel(0);
  const t = ed.state.song.tracks.find((x) => x.id === tid);
  const wet = ed.piecesSet().wet.get(tid).buffer;
  ed.setSolo(tid, true);
  const r = await m.engine.renderEditorOffline({ bypassBus: true });
  ed.setSolo(tid, false);
  const g = 10 ** ((t.gain_db ?? 0) / 20);
  const a = (((t.pan ?? 0) + 1) * Math.PI) / 4;
  const k = [g * Math.cos(a) * Math.SQRT2, g * Math.sin(a) * Math.SQRT2];
  const sr = r.sampleRate;
  const rows = [];
  for (let s = 0; s < Math.floor(r.channels[0].length / sr); s++) {
    let e = 0, n = 0, maxd = 0, at = 0;
    for (let c = 0; c < 2; c++) {
      const x = r.channels[c], y = wet.getChannelData(c);
      for (let i = s * sr; i < (s + 1) * sr; i++) {
        const d = x[i] - y[i] * k[c];
        e += d * d; n += (y[i] * k[c]) ** 2;
        if (Math.abs(d) > maxd) { maxd = Math.abs(d); at = i; }
      }
    }
    rows.push([s, +(10 * Math.log10(e / (n + 1e-20) + 1e-20)).toFixed(1), +maxd.toFixed(4), +(at / sr).toFixed(4)]);
  }
  // lag check around the worst second
  return { len: [r.channels[0].length, wet.length], rows };
}, track);
console.log(JSON.stringify(out.len));
for (const r of out.rows) console.log(r.join("\t"));
await b.close();
