import { expect, test, type Page } from "@playwright/test";

// Finding 1 (WP14): with mute/solo/fader touched the app plays the pre-master
// stems level-matched to the mix. They must never clip. This renders stem
// mode OFFLINE through the app's real graph (Deck + stem bus + level match +
// master volume) in the browser under test and measures the output peak.

type Diag = {
  engine: {
    renderOffline(
      v: string,
      o: { mixer?: unknown; seconds?: number; bypassBus?: boolean },
    ): Promise<{ channels: Float32Array[]; sampleRate: number; stemMatchDb: number | null; bus: string }>;
    getSnapshot(): { ready: string[]; stemBus: string };
  };
};

async function measure(page: Page, version: string, mixer: unknown) {
  return page.evaluate(
    async ({ version, mixer }) => {
      const eng = (window as unknown as { __muestra: Diag }).__muestra.engine;
      const stat = (chs: Float32Array[], from: number) => {
        let peak = 0;
        let acc = 0;
        let n = 0;
        for (const ch of chs)
          for (let i = from; i < ch.length; i++) {
            peak = Math.max(peak, Math.abs(ch[i]));
            acc += ch[i] * ch[i];
            n++;
          }
        return { peak, rms: Math.sqrt(acc / Math.max(1, n)) };
      };
      const skip = 4800; // ignore the 15 ms mode ramp
      const limited = await eng.renderOffline(version, { mixer });
      const raw = await eng.renderOffline(version, { mixer, bypassBus: true });
      const mix = await eng.renderOffline(version, {});
      return { bus: limited.bus, limited: stat(limited.channels, skip), raw: stat(raw.channels, skip), mix: stat(mix.channels, skip), stemMatchDb: limited.stemMatchDb };
    },
    { version, mixer },
  );
}

test.beforeEach(async ({ request }) => {
  await request.post("/debug/reset");
});

for (const flavour of ["worklet", "native (no AudioWorklet: plain-http phone)"] as const) {
test(`stem mode never clips and stays level-matched to the mix — ${flavour}`, async ({ page }, info) => {
  if (flavour.startsWith("native")) {
    // AudioWorklet only exists in secure contexts: simulate http://192.168.x.y
    await page.addInitScript(() => Object.defineProperty(BaseAudioContext.prototype, "audioWorklet", { get: () => undefined }));
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByTestId("now-version")).toHaveText("v004");
  await expect.poll(async () => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot().ready), { timeout: 20_000 }).toContain("v004");
  // a fader nudge forces stem mode with every track audible (the hottest case)
  const hot = { tracks: { bass: { mute: false, solo: false, gainDb: 0.1 } }, lanes: {} };
  const muteDrums = { tracks: { drums: { mute: true, solo: false, gainDb: 0 } }, lanes: {} };
  for (const [name, mixer] of [["all stems", hot], ["drums muted", muteDrums]] as const) {
    const r = await measure(page, "v004", mixer);
    const db = (x: number) => 20 * Math.log10(Math.max(x, 1e-9));
    const line = `bus=${r.bus} peak ${db(r.limited.peak).toFixed(2)} dBFS (unlimited ${db(r.raw.peak).toFixed(2)}), rms vs mix ${(db(r.limited.rms) - db(r.mix.rms)).toFixed(2)} dB`;
    info.annotations.push({ type: name, description: line });
    console.log(`[stems] ${name}: ${line}`);
    // never clips: strictly below 0 dBFS (worklet: -1 dBFS ceiling, native: 0.99 bound)
    expect(r.bus).toBe(flavour.startsWith("native") ? "native" : "worklet");
    expect(r.limited.peak, `${name}: peak`).toBeLessThan(r.bus === "worklet" ? 0.8914 : 0.9901);
    if (name === "all stems") {
      // the unprocessed sum must be a real-shaped problem for this to prove anything
      // (the native bus is calibrated to its own compressor makeup, so only the worklet flavour keeps the RMS-matched raw sum)
      if (r.bus === "worklet") expect(r.raw.peak, "unlimited stems peak").toBeGreaterThan(1);
      // level matched: within 1 dB of the mastered mix
      expect(Math.abs(db(r.limited.rms) - db(r.mix.rms))).toBeLessThan(1);
    }
  }
  expect(errors).toEqual([]);
});
}
