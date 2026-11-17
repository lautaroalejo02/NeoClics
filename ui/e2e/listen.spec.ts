import { expect, test, type Page } from "@playwright/test";
import { compile, makeAjv } from "../mock/schema.mjs";

const ajv = makeAjv();
const validFeedback = compile(ajv, "feedback.schema.json");

type Diag = {
  engine: {
    getSnapshot(): { playing: boolean; audio: string; active: string | null; listening: string; ready: string[] };
    getPosition(): number;
    now(): number;
    lastSwitch: { from: string; to: string; at: number; fromPos: number; toOffset: number } | null;
    decodeLog: { path: string; via: string }[];
  };
};

const snap = (page: Page) => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot());
const pos = (page: Page) => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getPosition());

test.beforeEach(async ({ request }) => {
  await request.post("/debug/reset");
});

test("listen, A/B, mute a track and leave a comment the agent can read", async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  // Playwright's WebKit for Windows has no Web Audio at all: nothing to play there
  test.skip(!(await page.evaluate(() => typeof AudioContext !== "undefined" || "webkitAudioContext" in window)), "this engine has no Web Audio");
  await expect(page.getByTestId("now-version")).toHaveText("v004");
  await expect.poll(async () => (await snap(page)).ready, { timeout: 15_000 }).toEqual(expect.arrayContaining(["v002", "v004"]));

  // play (the click is the user gesture that unlocks audio)
  await page.getByTestId("play").click();
  await expect.poll(async () => (await snap(page)).playing).toBe(true);
  expect((await snap(page)).audio).toBe("running");
  const p0 = await pos(page);
  await page.waitForTimeout(600);
  const p1 = await pos(page);
  expect(p1 - p0).toBeGreaterThan(0.3); // the playhead really advances

  // A/B while playing: same position, scheduled crossfade
  if (info.project.name.startsWith("phone")) await page.getByTestId("flip").tap();
  else await page.keyboard.press("s");
  await expect.poll(async () => (await snap(page)).active).toBe("v002");
  const sw = await page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.lastSwitch);
  expect(sw).not.toBeNull();
  expect(sw!.from).toBe("v004");
  expect(sw!.to).toBe("v002");
  expect(sw!.toOffset).toBe(sw!.fromPos); // same bpm/range → identical offset
  expect(sw!.fromPos).toBeGreaterThan(p1 - 0.05);
  const p2 = await pos(page);
  expect(p2).toBeGreaterThan(sw!.fromPos - 0.1); // no jump back to 0
  await expect(page.getByTestId("now-version")).toHaveText("v002");

  // mute the drums: stems take over without a re-render
  if (info.project.name.startsWith("phone")) await page.getByTestId("tab-mixer").tap();
  await page.getByTestId("mute-drums").click();
  await expect.poll(async () => (await snap(page)).listening, { timeout: 10_000 }).toBe("stems");
  await expect(page.getByTestId("stems-badge")).toContainText("stems sin master");
  expect((await snap(page)).playing).toBe(true);

  // M → type → Enter
  await page.getByTestId("comment-here").scrollIntoViewIfNeeded();
  await page.keyboard.press("m");
  const input = page.getByTestId("quick-comment-input");
  await expect(input).toBeFocused();
  await input.fill("acá el bombo se come al sample");
  await input.press("Enter");
  await expect(page.getByTestId("quick-comment")).toHaveCount(0);

  await expect
    .poll(async () => {
      const rec = (await (await request.get("/debug/received")).json()) as { event: Record<string, unknown>; valid: boolean }[];
      return rec.find((r) => r.event.type === "comment" && r.event.text === "acá el bombo se come al sample");
    })
    .toBeTruthy();
  const rec = (await (await request.get("/debug/received")).json()) as { event: Record<string, unknown>; valid: boolean }[];
  const ev = rec.find((r) => r.event.text === "acá el bombo se come al sample")!;
  expect(ev.valid).toBe(true);
  expect(validFeedback(ev.event), ajv.errorsText(validFeedback.errors)).toBe(true);
  expect(ev.event.version).toBe("v002");
  expect(ev.event.at_s as number).toBeGreaterThan(0);

  // the agent reads it from feedback.jsonl via the contract tool
  const fbRes = await request.post("/tools/feedback_read", { data: {} });
  const fbBody = (await fbRes.json()) as { ok: boolean; data: { events: { text?: string }[] } };
  expect(fbBody.ok).toBe(true);
  expect(fbBody.data.events.some((e) => e.text === "acá el bombo se come al sample")).toBe(true);

  // stems are 32-bit float WAV: decoded by the browser natively
  const decode = await page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.decodeLog);
  expect(decode.some((d) => d.path.includes("/stems/"))).toBe(true);
  await page.screenshot({ path: info.outputPath("final.png"), fullPage: false });
  expect(errors).toEqual([]);
});
