import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Against the REAL muestra-api and a real project (playwright.real.config.ts).
type Diag = {
  engine: {
    getSnapshot(): { playing: boolean; audio: string; active: string | null; listening: string; ready: string[] };
    getPosition(): number;
    lastSwitch: { from: string; to: string; at: number; fromPos: number; toOffset: number } | null;
  };
};

const REPO = path.resolve(HERE, "..", "..");
const UV = process.env.MUESTRA_UV ?? "uv";
const snap = (page: Page) => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getSnapshot());
const pos = (page: Page) => page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.getPosition());

async function projectPath(request: APIRequestContext): Promise<string> {
  const view = (await (await request.get("/project")).json()) as { project_path: string };
  return view.project_path;
}

/** `muestra render` from a separate CLI process, like an agent would. */
function cliRender(project: string, summary: string): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve) => {
    const proc = spawn(UV, ["run", "--directory", REPO, "muestra", "--json", "render", "--project", project, "--summary", summary, "--agent", "e2e-cli"], {
      env: { ...process.env, PYTHONUTF8: "1", MUESTRA_LIBRARY: "none" },
    });
    let out = "";
    proc.stdout.on("data", (d) => (out += String(d)));
    proc.stderr.on("data", (d) => (out += String(d)));
    proc.on("close", (code) => resolve({ code, out }));
  });
}

test("real API: load, play, A/B, mute, comment reaches feedback.jsonl", async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const project = await projectPath(request);
  await page.goto("/");
  // Playwright's WebKit for Windows has no Web Audio at all: nothing to play there
  test.skip(!(await page.evaluate(() => typeof AudioContext !== "undefined" || "webkitAudioContext" in window)), "this engine has no Web Audio");
  const latest = (await (await request.get("/project")).json()).versions[0].version as string;
  await expect(page.getByTestId("now-version")).toHaveText(latest);
  // the latest version and its A/B partner are decoded and ready
  await expect.poll(async () => (await snap(page)).ready.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
  expect((await snap(page)).ready).toContain(latest);

  await page.getByTestId("play").click();
  await expect.poll(async () => (await snap(page)).playing).toBe(true);
  const p0 = await pos(page);
  await page.waitForTimeout(600);
  expect((await pos(page)) - p0).toBeGreaterThan(0.3);

  // A/B between two REAL versions at the same position
  if (info.project.name.startsWith("phone")) await page.getByTestId("flip").tap();
  else await page.keyboard.press("s");
  await expect.poll(async () => (await snap(page)).active).not.toBe(latest);
  const sw = await page.evaluate(() => (window as unknown as { __muestra: Diag }).__muestra.engine.lastSwitch);
  expect(sw).not.toBeNull();
  expect(sw!.toOffset).toBeCloseTo(sw!.fromPos, 3);

  // mute a track: the real stems take over, no re-render
  if (info.project.name.startsWith("phone")) await page.getByTestId("tab-mixer").tap();
  await page.getByTestId("mute-drums").click();
  await expect.poll(async () => (await snap(page)).listening, { timeout: 20_000 }).toBe("stems");

  // a timestamped comment lands in the project's feedback.jsonl
  const text = `e2e real ${info.project.name} ${Date.now()}`;
  await page.getByTestId("comment-here").scrollIntoViewIfNeeded();
  await page.keyboard.press("m");
  const input = page.getByTestId("quick-comment-input");
  await expect(input).toBeFocused();
  await input.fill(text);
  await input.press("Enter");
  const feedback = path.join(project, "feedback.jsonl");
  await expect.poll(() => fs.readFileSync(feedback, "utf8").includes(text), { timeout: 10_000 }).toBe(true);
  const line = fs
    .readFileSync(feedback, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as Record<string, unknown>)
    .find((e) => e.text === text)!;
  expect(line.type).toBe("comment");
  expect(line.at_s as number).toBeGreaterThan(0);
  expect(fs.readFileSync(feedback, "utf8").includes("\r\n")).toBe(false);

  // the versions panel shows the musical diff from versions.diff
  if (info.project.name.startsWith("phone")) await page.getByTestId("tab-versions").tap().catch(() => undefined);
  await page.getByTestId("changes-v002").click();
  await expect(page.getByTestId("musical-diff-v002")).toContainText("semitono");

  // layout: real ears alarms carry at_s, so alarm markers sit on the waveform;
  // they must stay markers (an inherited .alarm card rule made them a
  // full-width bar that scrolled the page sideways on phones)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  for (const box of await page.locator(".marker.alarm").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().width))) {
    expect(box).toBeLessThanOrEqual(24);
  }

  await page.screenshot({ path: info.outputPath("real.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("real API: a render from the CLI in another process shows up live", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium" && info.project.name !== "desktop-firefox", "one CLI render per engine family is enough");
  const project = await projectPath(request);
  await page.goto("/");
  const before = ((await (await request.get("/project")).json()).versions as { version: string }[]).map((v) => v.version);
  await expect(page.getByTestId(`version-${before[0]}`)).toBeVisible();
  const next = `v${String(Number(before[0].slice(1)) + 1).padStart(3, "0")}`;
  const done = await cliRender(project, "Render desde la CLI");
  expect(done.code, done.out).toBe(0);
  // the watcher sees renders/<next>/meta.json and the page updates without reload
  await expect(page.getByTestId(`version-${next}`)).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: info.outputPath("live.png"), fullPage: true });
});

test("real API: with renders/ gone every version is still listed, greyed out, with how to regenerate it", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium" && info.project.name !== "phone-chromium", "one engine family is enough");
  const project = await projectPath(request);
  const renders = path.join(project, "renders");
  const hidden = path.join(project, "renders.hidden");
  const before = ((await (await request.get("/project")).json()).versions as { version: string }[]).map((v) => v.version);
  expect(before.length).toBeGreaterThan(0);
  fs.renameSync(renders, hidden);
  try {
    await page.goto("/");
    // the old behaviour here was "Todavía no hay ninguna versión"
    await expect(page.getByTestId("empty")).toHaveCount(0);
    await expect(page.getByTestId("no-audio")).toBeVisible({ timeout: 20_000 });
    // (no tabs in this view: the version list sits right under the card)
    for (const v of before) {
      const row = page.getByTestId(`version-${v}`);
      await expect(row).toHaveAttribute("data-noaudio", "true");
      await expect(page.getByTestId(`noaudio-${v}`)).toContainText(`render de ${v}`);
    }
    await page.screenshot({ path: info.outputPath("noaudio.png"), fullPage: true });
  } finally {
    fs.renameSync(hidden, renders);
  }
});

test("real LAN: public shell, Spanish missing-token screen, token link and route restrictions", async ({ page, request, baseURL }, info) => {
  test.skip(info.project.name !== "desktop-chromium", "one real LAN session");
  const address = Object.values(os.networkInterfaces()).flat().find((n) => n?.family === "IPv4" && !n.internal)?.address;
  test.skip(!address, "no non-loopback network interface available");
  const lan = new URL(baseURL!);
  lan.hostname = address!;
  await page.goto(lan.href);
  await expect(page.getByTestId("unauthorized")).toBeVisible();
  await page.goto(`${lan.href}?token=e2e-lan-token`);
  await expect(page.getByTestId("now-version")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.body.textContent?.includes("Sin conexión"))).toBe(false);
  expect(page.url()).not.toContain("token=");
  const denied = await request.post(`${lan.origin}/tools/render`, { headers: { "Authorization": "Bearer e2e-lan-token" }, data: {} });
  expect(denied.status()).toBe(403);
  const allowed = await request.get(`${lan.origin}/project`, { headers: { "Authorization": "Bearer e2e-lan-token" } });
  expect(allowed.status()).toBe(200);
  const wsStatus = await page.evaluate(() => new Promise<number>((resolve) => {
    const ws = new WebSocket(`ws://${location.host}/events?token=e2e-lan-token`);
    ws.onopen = () => { ws.close(); resolve(101); };
    ws.onerror = () => resolve(0);
  }));
  expect(wsStatus).toBe(101);
});

test("real API: longer manual chop is recorded and preserves clip span", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium", "one manual edit");
  const project = await projectPath(request);
  const songFile = path.join(project, "song.yaml");
  const original = fs.readFileSync(songFile);
  try {
    await page.goto("/");
    await page.getByTestId("tab-chops").click();
    await page.getByTestId("cand-cand2").click();
    for (let n = 0; n < 8; n++) await page.getByRole("button", { name: "Largo de cand2: un beat más", exact: true }).click();
    await page.getByTestId("confirm-chop").click();
    await expect(page.getByTestId("chop-outcome")).toHaveAttribute("data-status", "ok");
    await expect(page.getByTestId("chop-outcome")).toContainText("repetici");
    const song = (await (await request.post("/tools/song_get", { data: {} })).json()).data.song;
    expect(song.chops.find((c: { id: string }) => c.id === "c1").length_beats).toBe(16);
    expect(song.tracks.find((t: { id: string }) => t.id === "sample").clips[0].repeat).toBe(4);
    expect(fs.readFileSync(path.join(project, "feedback.jsonl"), "utf8")).toContain('"type": "chop_choice"');
  } finally {
    fs.writeFileSync(songFile, original);
  }
});

test("real API: CLI draft appears live and plays without a version", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium", "one CLI draft");
  const project = await projectPath(request);
  await page.goto("/");
  await expect(page.getByTestId("now-version")).toBeVisible();
  const before = (await (await request.get("/tools/versions_list")).json()).data.versions.length;
  const result = await new Promise<{ code: number | null; out: string }>((resolve) => {
    const proc = spawn(UV, ["run", "--directory", REPO, "muestra", "--json", "render", "--project", project, "--draft", "--range-bars", "[1,3]", "--summary", "Borrador desde CLI"], { env: { ...process.env, PYTHONUTF8: "1", MUESTRA_LIBRARY: "none" } });
    let out = "";
    proc.stdout.on("data", (d) => { out += String(d); });
    proc.on("close", (code) => resolve({ code, out }));
  });
  expect(result.code, result.out).toBe(0);
  const draft = JSON.parse(result.out).data.draft_id as string;
  await expect(page.getByTestId(`draft-${draft}`)).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`play-draft-${draft}`).click();
  await expect(page.getByTestId(`play-draft-${draft}`)).toHaveAttribute("aria-pressed", "true");
  expect((await (await request.get("/tools/versions_list")).json()).data.versions.length).toBe(before);
});
