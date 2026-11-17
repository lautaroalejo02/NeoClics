import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import YAML from "yaml";

// Performance pads (WP17) against the REAL muestra-api and project
// (playwright.real.config.ts; drums kick/clap/ohat at 125 BPM). A rhythm is
// "played" with key events timed in the page against the running transport
// (the same clock math as a person hearing the loop), recorded as a take,
// committed to a new track through song.edit, checked hit by hit in
// song.yaml, in the arrangement and in a real draft render. Then a chop is
// cut onto pads and played, and a reload mid-take is recovered.
// Simulated: the key events (synthetic, page-timed). NOT simulated: a human,
// a real keyboard, real speakers.

const BPM = 125;
const SPB = 60 / BPM;

type M = {
  editor: {
    state: { status: string; inFlight: number; loop: unknown; final: { draftId: string; state: string } | null; song: { tracks: { id: string }[] } };
    setLoop(l: { startBeat: number; endBeat: number } | null): void;
    togglePlay(): void;
    toggleFinal(): Promise<void>;
    locate(track: string, at: { beat0: number; lane?: string }): { x: number; y: number } | null;
  };
  engine: {
    isPlaying(): boolean;
    pause(): void;
    audioContext(): AudioContext;
    editorClock(): { when: number; offset: number; loopStart: number; loopEnd: number; busLatency: number } | null;
  };
  pads: {
    state: { on: boolean; missing: string[]; takes: { id: string; n_events: number; status: string }[]; rec: { phase: string }; latency: { outputMs: number; baseMs: number } };
    bank(): { pads: { key: string; name: string; slice?: unknown; lane?: string }[] };
    presses: { code: string; schedMs: number; beat: number | null; eventMs: number }[];
    setCalibrationMs(ms: number | null): void;
    setQuantize(q: object): void;
    setCountIn(n: number): void;
    select(c: string | null): void;
    loadTake(id: string): Promise<{ events: { beat: number; pad: string; cycle: number }[]; pads: Record<string, { lane?: string; name: string }> }>;
  };
};

async function projectPath(request: APIRequestContext): Promise<string> {
  return ((await (await request.get("/project")).json()) as { project_path: string }).project_path;
}


async function settle(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.state.inFlight), { timeout: 20_000 }).toBe(0);
}

/**
 * Fire keydown/keyup for each target at the moment its loop beat is HEARD in
 * the next pass (output timestamp + stem-bus delay, the store's own mapping).
 * Returns, per event, the beat the event's own timestamp corresponds to.
 */
async function playRhythm(page: Page, notes: { beat: number; code: string }[]): Promise<{ code: string; target: number; actual: number }[]> {
  return page.evaluate(async (ns) => {
    const mu = (window as unknown as { __muestra: M }).__muestra;
    const ctx = mu.engine.audioContext();
    const spb = 60 / 125;
    const heardNow = (perf: number) => {
      const ts = ctx.getOutputTimestamp?.();
      if (ts && ts.contextTime && ts.performanceTime) return ts.contextTime + (perf - ts.performanceTime) / 1000;
      return ctx.currentTime - (performance.now() - perf) / 1000 - (ctx.outputLatency || 0);
    };
    const clock = mu.engine.editorClock()!;
    const L = clock.loopEnd - clock.loopStart;
    const unwrapped = (c: number) => clock.offset - clock.loopStart + (c - clock.busLatency - clock.when);
    const k = Math.floor(unwrapped(heardNow(performance.now())) / L) + 1;
    const perfOf = (u: number) => {
      const c = clock.when + clock.busLatency + (u - (clock.offset - clock.loopStart));
      return performance.now() + (c - heardNow(performance.now())) * 1000;
    };
    const out: { code: string; target: number; actual: number }[] = [];
    await Promise.all(
      ns.map(
        (n) =>
          new Promise<void>((resolve) => {
            const at = perfOf(k * L + n.beat * spb);
            setTimeout(() => {
              while (performance.now() < at) {
                /* spin the last ms: a page timer alone is late by up to tens of ms */
              }
              const down = new KeyboardEvent("keydown", { code: n.code, key: n.code.slice(3).toLowerCase(), bubbles: true });
              window.dispatchEvent(down);
              const u = unwrapped(heardNow(down.timeStamp));
              const into = (u - Math.floor(u / L) * L) / spb;
              out.push({ code: n.code, target: n.beat, actual: into >= L / spb - 0.0625 ? into - L / spb : into });
              setTimeout(() => {
                window.dispatchEvent(new KeyboardEvent("keyup", { code: n.code, bubbles: true }));
                resolve();
              }, 40);
            }, Math.max(0, at - performance.now() - 12));
          }),
      ),
    );
    return out.sort((a, b) => a.target - b.target);
  }, notes);
}

test.describe.configure({ mode: "serial" });

test("pads: assign a kit, record a rhythm over the loop, commit it, render it; slice a chop; recover a reload mid-take", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium", "pads flows run once, on desktop Chromium");
  test.setTimeout(360_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (msg) => msg.type() === "error" && !/Failed to load resource/.test(msg.text()) && errors.push(msg.text()));
  const project = await projectPath(request);
  const songFile = path.join(project, "song.yaml");
  const original = fs.readFileSync(songFile, "utf-8");
  try {
    await page.addInitScript(() => localStorage.setItem("muestra.view", "arreglo"));
    await page.goto("/");
    await expect(page.getByTestId("timeline")).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.state.status)).toBe("ready");

    // ------------------------------------------------ open the pads, drop the kit
    await page.getByTestId("ed-pads").click();
    await expect(page.getByTestId("pad-grid")).toBeVisible();
    await expect(page.getByTestId("pads-power")).toHaveAttribute("aria-pressed", "true");
    await page.getByTestId("pads-kit").click();
    await page.getByTestId("pads-kit-disco-01").click();
    await expect
      .poll(() => page.evaluate(() => { const p = (window as unknown as { __muestra: M }).__muestra.pads; return p.bank().pads.length === 3 && p.state.missing.length === 0; }), { timeout: 30_000 })
      .toBe(true);
    const layout = await page.evaluate(() => Object.fromEntries((window as unknown as { __muestra: M }).__muestra.pads.bank().pads.map((p) => [p.name, p.key])));
    expect(layout).toEqual({ kick: "KeyZ", clap: "KeyX", ohat: "KeyB" });
    await expect(page.getByTestId("pad-KeyZ")).toContainText("kick");

    // ------------------------------------------------ loop bars 1-2, play, record
    // wait until the editor stops preparing audio (decoding blocks the page)
    await expect.poll(() => page.evaluate(() => { const s = (window as unknown as { __muestra: { editor: { state: { pieces: string; rendering: string[] } } } }).__muestra.editor.state; return s.pieces === "ready" && s.rendering.length === 0; }), { timeout: 90_000 }).toBe(true);
    await page.evaluate(() => {
      const mu = (window as unknown as { __muestra: M }).__muestra;
      mu.pads.setCalibrationMs(0);
      mu.pads.setQuantize({ grid: "off", strength: 1, swing: 0 });
      mu.editor.setLoop({ startBeat: 0, endBeat: 8 });
      mu.editor.togglePlay();
    });
    await expect.poll(() => page.evaluate(() => !!(window as unknown as { __muestra: M }).__muestra.engine.editorClock()), { timeout: 20_000 }).toBe(true);
    await page.waitForTimeout(600);
    await page.keyboard.press("Enter"); // record (pads mode)
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.state.rec.phase)).toBe("recording");
    const rhythm = [
      { beat: 0, code: "KeyZ" },
      { beat: 1.03, code: "KeyX" },
      { beat: 1.5, code: "KeyB" },
      { beat: 2.1, code: "KeyZ" },
      { beat: 3, code: "KeyX" },
      { beat: 4.5, code: "KeyZ" },
      { beat: 5.25, code: "KeyB" },
      { beat: 6, code: "KeyX" },
      { beat: 6.75, code: "KeyZ" },
    ];
    const played = await playRhythm(page, rhythm);
    await page.waitForTimeout(300);
    await page.keyboard.press("Enter"); // stop recording
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.state.takes.length), { timeout: 15_000 }).toBe(1);
    const takeId = await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.state.takes[0].id);
    const take = await page.evaluate((id) => (window as unknown as { __muestra: M }).__muestra.pads.loadTake(id), takeId);
    expect(take.events).toHaveLength(rhythm.length);
    const jitter = played.map((p, i) => (take.events[i].beat - p.target) * SPB * 1000);
    const vsEvent = played.map((p, i) => (take.events[i].beat - p.actual) * SPB * 1000);
    console.log(`recorded vs intended (ms, page timer jitter): ${jitter.map((x) => x.toFixed(1)).join(", ")}`);
    console.log(`recorded vs the event's own timestamp (ms, compensation exactness): ${vsEvent.map((x) => x.toFixed(3)).join(", ")}`);
    for (const [i, e] of take.events.entries()) expect(e.pad).toBe(played[i].code);
    for (const x of vsEvent) expect(Math.abs(x)).toBeLessThan(1); // compensation math is exact
    for (const x of jitter) expect(Math.abs(x)).toBeLessThan(60); // page setTimeout lateness (test harness, not the product)

    // ------------------------------------------------ commit to a new track at bar 3
    if (!(await page.getByTestId("take-detail").isVisible())) await page.getByTestId(`take-${takeId}`).locator(".take-name").click();
    await page.getByTestId("take-at-bar").fill("3");
    await page.getByTestId("take-commit").click();
    await expect.poll(() => fs.readFileSync(songFile, "utf-8").includes("id: pads"), { timeout: 20_000 }).toBe(true);
    await settle(page);
    const song = YAML.parse(fs.readFileSync(songFile, "utf-8")) as { tracks: { id: string; pattern?: { lanes: Record<string, { sample?: string }>; hits: { beat: number; lane: string; velocity?: number }[] } }[] };
    const pads = song.tracks.find((t) => t.id === "pads")!;
    expect(pads, "a new pads track in song.yaml").toBeTruthy();
    expect(Object.keys(pads.pattern!.lanes).sort()).toEqual(["clap", "kick", "ohat"]);
    const laneOf: Record<string, string> = { KeyZ: "kick", KeyX: "clap", KeyB: "ohat" };
    const hits = pads.pattern!.hits;
    expect(hits).toHaveLength(rhythm.length);
    for (const [i, e] of take.events.entries()) {
      expect(hits[i].lane).toBe(laneOf[e.pad]);
      expect(hits[i].beat).toBeCloseTo(9 + e.beat, 5); // bar 3 = song beat 9
      expect(Math.abs(hits[i].beat - (9 + rhythm[i].beat)) * SPB * 1000).toBeLessThan(60);
    }
    // visible in the arrangement, hit by hit
    await expect(page.getByTestId("ed-head-pads")).toBeVisible();
    for (const h of hits) expect(await page.evaluate(([b, l]) => (window as unknown as { __muestra: M }).__muestra.editor.locate("pads", { beat0: (b as number) - 1, lane: l as string }), [h.beat, h.lane] as const)).not.toBeNull();

    // ------------------------------------------------ the real renderer: draft, hit positions
    await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.toggleFinal());
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.state.final?.state), { timeout: 150_000 }).toBe("ready");
    const draftId = await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.state.final!.draftId);
    const onsets = await page.evaluate(
      async ([id, hs]) => {
        const out: { lane: string; expected: number; onset: number | null; lead: number }[] = [];
        const ctx = new OfflineAudioContext(2, 1, 48000);
        const cache = new Map<string, AudioBuffer>();
        for (const h of hs as { beat: number; lane: string }[]) {
          if (!cache.has(h.lane)) {
            const res = await fetch(`/files/renders/${id}/stems/pads.${h.lane}.wav`);
            cache.set(h.lane, await ctx.decodeAudioData(await res.arrayBuffer()));
          }
          const b = cache.get(h.lane)!;
          const sr = b.sampleRate;
          const expected = ((h.beat - 1) * 60) / 125;
          const a = Math.round((expected - 0.03) * sr);
          let onset: number | null = null;
          for (let i = a; i < a + Math.round(0.06 * sr) && onset == null; i++) for (let c = 0; c < b.numberOfChannels; c++) if (Math.abs(b.getChannelData(c)[i]) > 1e-4) { onset = i / sr; break; }
          out.push({ lane: h.lane, expected, onset, lead: 0 });
        }
        return out;
      },
      [draftId, hits] as const,
    );
    const errs = onsets.map((o) => ((o.onset ?? NaN) - o.expected) * 1000);
    console.log(`draft ${draftId}: rendered onset - recorded hit (ms): ${errs.map((x) => x.toFixed(2)).join(", ")}`);
    for (const x of errs) {
      expect(Number.isFinite(x)).toBe(true);
      expect(x).toBeGreaterThanOrEqual(-0.05); // never early
      expect(x).toBeLessThan(3); // the one-shot's own first audible sample
    }
    await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.editor.toggleFinal());

    // ------------------------------------------------ the browser plays the committed take at the same times
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: { piecesSet(): { oneshots: Map<string, unknown> } } } }).__muestra.editor.piecesSet().oneshots.size), { timeout: 30_000 }).toBeGreaterThanOrEqual(3);
    const browserOnsets = await page.evaluate(async (hs) => {
      const mu = (window as unknown as { __muestra: { editor: { setSolo(id: string, on: boolean): void }; engine: { renderEditorOffline(o: object): Promise<{ channels: Float32Array[]; sampleRate: number }> } } }).__muestra;
      mu.editor.setSolo("pads", true);
      const from = ((hs as { beat: number }[])[0].beat - 1) * 60 / 125 - 0.2;
      const out = await mu.engine.renderEditorOffline({ from, seconds: 4.2, bypassBus: true });
      mu.editor.setSolo("pads", false);
      return (hs as { beat: number }[]).map((h) => {
        const t = ((h.beat - 1) * 60) / 125;
        const a = Math.round((t - from - 0.03) * out.sampleRate);
        for (let i = a; i < a + Math.round(0.06 * out.sampleRate); i++) for (const ch of out.channels) if (Math.abs(ch[i] ?? 0) > 1e-4) return (from + i / out.sampleRate - t) * 1000;
        return null;
      });
    }, hits);
    console.log(`browser playback onset - recorded hit (ms): ${browserOnsets.map((x) => (x == null ? "none" : x.toFixed(2))).join(", ")}`);
    for (const [i, x] of browserOnsets.entries()) {
      expect(x).not.toBeNull();
      expect(Math.abs(x! - errs[i])).toBeLessThan(0.1); // same frame as the render (±0.1 ms)
    }

    // ------------------------------------------------ cut the chop into pads and play a slice
    await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.select(null));
    await page.getByTestId("pads-slice").click();
    await page.getByTestId("slice-go").click();
    await expect
      .poll(() => page.evaluate(() => { const p = (window as unknown as { __muestra: M }).__muestra.pads; const s = p.bank().pads.filter((x) => x.slice); return s.length >= 8 && p.state.missing.length === 0 ? s.length : 0; }), { timeout: 60_000 })
      .toBeGreaterThanOrEqual(8);
    const n0 = await page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.presses.length);
    await page.keyboard.press("KeyC"); // third slice
    await page.keyboard.press("KeyZ");
    const slicePresses = await page.evaluate((n) => (window as unknown as { __muestra: M }).__muestra.pads.presses.slice(n), n0);
    expect(slicePresses.map((p) => p.code)).toEqual(["KeyC", "KeyZ"]);

    // ------------------------------------------------ reload mid-take: nothing lost
    await page.keyboard.press("Enter");
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.state.rec.phase)).not.toBe("idle");
    await expect.poll(() => page.evaluate(() => !!(window as unknown as { __muestra: M }).__muestra.engine.editorClock())).toBe(true);
    await playRhythm(page, [
      { beat: 0, code: "KeyZ" },
      { beat: 1, code: "KeyX" },
      { beat: 2, code: "KeyC" },
    ]);
    await page.waitForTimeout(400); // < the 3 s server save: only the browser copy has the presses
    await page.reload();
    await expect(page.getByTestId("timeline")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("ed-pads").click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: M }).__muestra.pads.state.takes.map((t) => [t.n_events, t.status])), { timeout: 20_000 }).toEqual([
      [rhythm.length, "done"],
      [3, "done"],
    ]);
    await expect(page.getByTestId("pads-takes")).toContainText("3 golpes");
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(() => { const mu = (window as unknown as { __muestra: M }).__muestra; if (mu?.engine.isPlaying()) mu.engine.pause(); }).catch(() => undefined);
    fs.writeFileSync(songFile, original, "utf-8");
  }
});
