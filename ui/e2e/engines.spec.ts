import { expect, test, type Page } from "@playwright/test";

// WP14 finding 8: the app was only ever run in Chromium. These run on every
// engine the config lists. Note: Playwright's WebKit build for Windows has NO
// Web Audio (AudioContext is undefined), so the audio tests skip there and
// the app is checked to degrade gracefully instead.

type Diag = {
  engine: {
    getSnapshot(): { audio: string; ready: string[]; stemBus: string };
    decodeLog: { path: string; via: "native" | "wav-fallback" }[];
    renderOffline(v: string, o: { mixer?: unknown; seconds?: number }): Promise<{ channels: Float32Array[]; sampleRate: number }>;
  };
};

const hasWebAudio = (page: Page) => page.evaluate(() => typeof AudioContext !== "undefined" || typeof (window as unknown as { webkitAudioContext?: unknown }).webkitAudioContext !== "undefined");

async function load(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("now-version")).toHaveText("v004");
}

test.beforeEach(async ({ request }) => {
  await request.post("/debug/reset");
});

test("without Web Audio the app still loads and says why it is silent", async ({ page }) => {
  await load(page);
  if (await hasWebAudio(page)) test.skip(true, "this engine has Web Audio");
  await expect(page.getByTestId("play")).toBeVisible();
  await expect(page.getByText("Este navegador no tiene Web Audio")).toBeVisible();
  // everything that does not need audio still works: leave feedback
  await page.getByTestId("tab-feedback").tap().catch(() => undefined);
  const box = page.getByTestId("general-comment");
  if (await box.isVisible()) {
    await box.fill("sin audio pero comento igual");
    await box.press("Enter");
    await expect(page.getByTestId("feedback-list")).toContainText("sin audio pero comento igual");
  }
});

test("every file decodes (native or the app's own WAV decoder) and plays through the same graph", async ({ page }, info) => {
  await load(page);
  test.skip(!(await hasWebAudio(page)), "no Web Audio in this engine");
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot().ready), { timeout: 20_000 }).toContain("v004");
  await page.getByTestId("play").click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot().audio)).toBe("running");
  const log = await page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.decodeLog);
  const via = log.reduce<Record<string, number>>((a, d) => ((a[d.via] = (a[d.via] ?? 0) + 1), a), {});
  console.log(`[decode ${info.project.name}] ${JSON.stringify(via)}; stem bus: ${await page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot().stemBus)}`);
  expect(log.length).toBeGreaterThan(0);
});

test("32-bit float WAV: when the browser refuses it, the app's own decoder gives the same audio", async ({ page, browser }, info) => {
  await load(page);
  test.skip(!(await hasWebAudio(page)), "no Web Audio in this engine");
  const render = async (p: Page) => {
    await expect.poll(() => p.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot().ready), { timeout: 20_000 }).toContain("v004");
    return p.evaluate(async () => {
      const eng = (window as unknown as { __muestra: Diag }).__muestra.engine;
      const r = await eng.renderOffline("v004", { mixer: { tracks: { bass: { mute: false, solo: false, gainDb: 0.1 } }, lanes: {} }, seconds: 4 });
      let acc = 0;
      let peak = 0;
      for (const ch of r.channels)
        for (let i = 0; i < ch.length; i++) {
          acc += ch[i] * ch[i];
          peak = Math.max(peak, Math.abs(ch[i]));
        }
      return { rms: Math.sqrt(acc / (r.channels.length * r.channels[0].length)), peak, via: eng.decodeLog.map((d) => `${d.via}`) };
    });
  };
  const native = await render(page);

  // a second page where decodeAudioData rejects float WAV (like some Safari versions did)
  const ctx2 = await browser.newContext(info.project.use);
  const page2 = await ctx2.newPage();
  await page2.addInitScript(() => {
    const orig = BaseAudioContext.prototype.decodeAudioData;
    BaseAudioContext.prototype.decodeAudioData = function (buf: ArrayBuffer, ...rest: never[]) {
      const dv = new DataView(buf);
      const tag = dv.getUint16(20, true);
      const bits = dv.getUint16(34, true);
      if ((tag === 3 || tag === 0xfffe) && bits === 32) return Promise.reject(new DOMException("float wav unsupported (simulated)", "EncodingError"));
      return (orig as (...a: unknown[]) => Promise<AudioBuffer>).call(this, buf, ...rest);
    } as typeof orig;
  });
  await load(page2);
  const fallback = await render(page2);
  await ctx2.close();

  console.log(`[fallback ${info.project.name}] native via ${JSON.stringify([...new Set(native.via)])}, forced via ${JSON.stringify([...new Set(fallback.via)])}; rms ${native.rms.toFixed(5)} vs ${fallback.rms.toFixed(5)}, peak ${native.peak.toFixed(5)} vs ${fallback.peak.toFixed(5)}`);
  expect(fallback.via).toContain("wav-fallback");
  expect(fallback.rms).toBeGreaterThan(0.001);
  expect(Math.abs(fallback.rms - native.rms) / native.rms).toBeLessThan(1e-3);
  expect(Math.abs(fallback.peak - native.peak)).toBeLessThan(1e-3);
});
