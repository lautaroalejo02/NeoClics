// Pads (WP17) on a real server: screenshots and measurements.
//   node scripts/pads-probe.mjs <url> <outdir> [shots|latency|burst|all]
// shots: 1440 + 390 px, dark + light, with a kit loaded.
// latency: key event -> src.start() (ms), context base/output latency.
// burst: 16th notes at 140 BPM on 3 keys at once for 4 bars through real
//   key events (Playwright keyboard), counting presses vs scheduled voices.
import { chromium, devices } from "@playwright/test";

const [url, out, what = "all"] = process.argv.slice(2);
const b = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
const desk = { viewport: { width: 1440, height: 900 } };
const phone = { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } };

async function open(ctxOpts, theme) {
  const ctx = await b.newContext(ctxOpts);
  const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  p.on("console", (m) => m.type() === "error" && console.log("console", m.text()));
  await p.addInitScript(() => {
    // counts every pad-key event BEFORE the pads handler sees it (registered first)
    window.__padKeys = 0;
    window.addEventListener("keydown", (e) => { if (!e.repeat && /^(Key|Comma)/.test(e.code)) window.__padKeys++; }, true);
  });
  await p.addInitScript((t) => {
    localStorage.setItem("muestra.prefs.v1", JSON.stringify({ theme: t }));
    localStorage.setItem("muestra.view", "arreglo");
  }, theme);
  await p.goto(url);
  await p.waitForSelector('[data-testid="timeline"]', { timeout: 30000 });
  await p.waitForTimeout(1500);
  await p.getByTestId("ed-pads").click();
  await p.waitForSelector('[data-testid="pad-grid"]');
  return { ctx, p };
}

async function loadKit(p, kit = "disco-01") {
  const has = await p.evaluate(() => window.__muestra.pads.bank().pads.length);
  if (!has) {
    await p.getByTestId("pads-kit").click();
    await p.getByTestId(`pads-kit-${kit}`).click();
  }
  await p.waitForFunction(() => {
    const s = window.__muestra.pads;
    return s.bank().pads.length > 0 && s.state.missing.length === 0;
  }, null, { timeout: 30000 });
}

if (what === "shots" || what === "all") {
  for (const theme of ["dark", "light"]) {
    for (const [name, opts] of [["desk", desk], ["phone", phone]]) {
      const { ctx, p } = await open(opts, theme);
      await loadKit(p);
      await p.getByTestId("pad-KeyZ").click();
      await p.waitForTimeout(400);
      const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      console.log(`pads-${name}-${theme} overflow px:`, overflow);
      await p.getByTestId("pads-panel").scrollIntoViewIfNeeded();
      await p.screenshot({ path: `${out}/pads-${name}-${theme}.png`, fullPage: name === "phone" });
      await ctx.close();
    }
  }
}

if (what === "latency" || what === "all" || what === "burst") {
  const { ctx, p } = await open(desk, "dark");
  await loadKit(p);
  const assigned = await p.evaluate(() => window.__muestra.pads.bank().pads.map((x) => x.key));
  const keys = assigned.slice(0, 4);

  for (let i = 0; i < 60; i++) {
    await p.keyboard.down(keys[i % 4]);
    await p.waitForTimeout(20);
    await p.keyboard.up(keys[i % 4]);
    await p.waitForTimeout(40);
  }
  const lat = await p.evaluate(() => {
    const s = window.__muestra.pads;
    s.refreshLatency();
    const ctx = window.__muestra.engine.audioContext();
    const sched = s.presses.map((x) => x.schedMs).sort((a, b) => a - b);
    const q = (f) => sched[Math.min(sched.length - 1, Math.floor(f * sched.length))];
    return { n: sched.length, p50: q(0.5), p95: q(0.95), max: sched[sched.length - 1], baseLatency: ctx.baseLatency, outputLatency: ctx.outputLatency, sampleRate: ctx.sampleRate, latencyInfo: s.state.latency };
  });
  console.log("latency", JSON.stringify(lat));
  if (what !== "latency") {
    // burst: 16ths at 140 BPM, 3 keys pressed together on every 16th, 4 bars
    const step = 60000 / 140 / 4;
    const before = await p.evaluate(() => { window.__padKeys = 0; return window.__muestra.pads.presses.length; });
    const t0 = Date.now();
    for (let i = 0; i < 64; i++) {
      const target = t0 + i * step;
      const wait = target - Date.now();
      if (wait > 0) await p.waitForTimeout(wait);
      const six = assigned.slice(0, 6);
      const ks = i % 2 ? six.slice(3, 6) : six.slice(0, 3);
      for (const k of ks) await p.keyboard.down(k);
      await p.waitForTimeout(25);
      for (const k of ks) await p.keyboard.up(k);
    }
    const res = await p.evaluate((n0) => {
      const pr = window.__muestra.pads.presses.slice(n0);
      const gaps = [];
      for (let i = 1; i < pr.length; i++) gaps.push(pr[i].eventMs - pr[i - 1].eventMs);
      return { keyEvents: window.__padKeys, scheduled: pr.length, maxSchedMs: Math.max(...pr.map((x) => x.schedMs)) };
    }, before);
    console.log("burst 16ths@140 x3 keys x64:", JSON.stringify(res), "(expected 192)");
  }
  await ctx.close();
}
await b.close();
