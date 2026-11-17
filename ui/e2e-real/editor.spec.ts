import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The arrangement editor against the REAL muestra-api and a real project
// (playwright.real.config.ts builds it: sample clips, drums kick/clap/ohat,
// an SFZ bass). Every edit is checked in song.yaml on disk; audio is
// measured by rendering the browser's own scheduled graph offline, and in a
// real draft render.

type Ed = {
  state: {
    status: string;
    inFlight: number;
    revision: string | null;
    lastEditMs: number | null;
    final: { draftId: string; state: string } | null;
    listen: string;
    trackStatus: Record<string, { state: string }>;
    song: { tracks: { id: string; notes?: unknown[]; clips?: { at_bar: number; repeat?: number; length_beats?: number }[] }[] };
  };
  locate(track: string, at: { beat0: number; lane?: string; pitch?: number }): { x: number; y: number } | null;
  setView(p: Record<string, unknown>): void;
  setSolo(id: string, on: boolean): void;
  piecesSet(): { oneshots: Map<string, unknown>; materials: unknown[]; wet: Map<string, unknown> };
  timings: { edit: number[]; pieces: number[]; wet: number[] };
};

const BPM = 125;
const SPB = 60 / BPM;

async function projectPath(request: APIRequestContext): Promise<string> {
  return ((await (await request.get("/project")).json()) as { project_path: string }).project_path;
}


async function settle(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.inFlight), { timeout: 20_000 }).toBe(0);
}

async function at(page: Page, track: string, where: { beat0: number; lane?: string; pitch?: number }) {
  const p = await page.evaluate(([t, w]) => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.locate(t as string, w as never), [track, where] as const);
  const box = (await page.getByTestId("timeline").boundingBox())!;
  return { x: box.x + p!.x, y: box.y + p!.y };
}

/** Peak of the browser mix (drums soloed) in 40 ms after each song time. */
async function browserPeaks(page: Page, times: number[]): Promise<number[]> {
  return page.evaluate(async (ts) => {
    const m = (window as unknown as { __muestra: { editor: Ed; engine: { renderEditorOffline(o: object): Promise<{ channels: Float32Array[]; sampleRate: number }> } } }).__muestra;
    m.editor.setSolo("drums", true);
    const from = Math.min(...ts) - 0.5;
    const out = await m.engine.renderEditorOffline({ from, seconds: Math.max(...ts) - from + 0.5, bypassBus: true });
    m.editor.setSolo("drums", false);
    return ts.map((t) => {
      const a = Math.round((t - from) * out.sampleRate);
      let p = 0;
      for (const ch of out.channels) for (let i = a; i < a + Math.round(0.04 * out.sampleRate); i++) p = Math.max(p, Math.abs(ch[i] ?? 0));
      return p;
    });
  }, times);
}

const songYaml = (project: string) => fs.readFileSync(path.join(project, "song.yaml"), "utf-8");

test.describe.configure({ mode: "serial" });

// leave the shared real project as the other specs expect it
let restore: (() => void) | null = null;
test.afterEach(() => {
  restore?.();
  restore = null;
});

test("editor: remove one kick, hear it gone, undo/redo, sounds, clips, notes, save, ask", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-chromium", "editor flows run once, on desktop Chromium");
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const project = await projectPath(request);
  const original = songYaml(project);
  restore = () => fs.writeFileSync(path.join(project, "song.yaml"), original, "utf-8");
  await page.addInitScript(() => localStorage.setItem("muestra.view", "arreglo"));
  await page.goto("/");
  await expect(page.getByTestId("timeline")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.status)).toBe("ready");
  await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.setView({ pxPerBeat: 26, scrollX: 0, snap: "16th" }));
  // pieces: chop material + the three one-shots, then the drums' wet render
  await expect
    .poll(() => page.evaluate(() => { const s = (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.piecesSet(); return s.oneshots.size >= 3 && s.materials.length >= 1; }), { timeout: 60_000 })
    .toBe(true);
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.trackStatus.drums?.state), { timeout: 60_000 }).toBe("exact");

  // ---------------------------------------------------- remove the kick of bar 7, beat 3
  const bar7b3 = 6 * 4 + 2;
  const bar8b3 = 7 * 4 + 2;
  const before = await browserPeaks(page, [bar7b3 * SPB, bar8b3 * SPB]);
  expect(before[0]).toBeGreaterThan(0.05);
  const p = await at(page, "drums", { beat0: bar7b3, lane: "kick" });
  await page.mouse.click(p.x, p.y);
  await settle(page);
  expect(songYaml(project)).toContain('overrides: {7: "x...x.......x..."}');
  const editMs = await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.lastEditMs);
  const after = await browserPeaks(page, [bar7b3 * SPB, bar8b3 * SPB]);
  console.log(`kick bar7 beat3: browser peak ${before[0].toFixed(3)} -> ${after[0].toFixed(3)}; bar8 ${before[1].toFixed(3)} -> ${after[1].toFixed(3)}; edit applied in ${editMs?.toFixed(1)} ms`);
  expect(after[0]).toBeLessThan(before[0] * 0.3);
  expect(after[1]).toBeGreaterThan(before[1] * 0.7);
  // the drums' wet render catches up (seconds) and still has no kick there
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.trackStatus.drums?.state), { timeout: 60_000 }).toBe("exact");
  const wetAfter = await browserPeaks(page, [bar7b3 * SPB, bar8b3 * SPB]);
  expect(wetAfter[0]).toBeLessThan(wetAfter[1] * 0.3);

  // ---------------------------------------------------- the true render of the current state (draft)
  await page.keyboard.press("f");
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.final?.state), { timeout: 120_000 }).toBe("ready");
  const draftId = await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.final!.draftId);
  const draftPeaks = await page.evaluate(
    async ([id, ts]) => {
      const res = await fetch(`/files/renders/${id}/stems/drums.kick.wav`);
      const ctx = new OfflineAudioContext(2, 1, 48000);
      const b = await ctx.decodeAudioData(await res.arrayBuffer());
      return (ts as unknown as number[]).map((t) => {
        const a = Math.round(t * b.sampleRate);
        let m = 0;
        for (let c = 0; c < b.numberOfChannels; c++) {
          const d = b.getChannelData(c);
          for (let i = a; i < a + Math.round(0.04 * b.sampleRate); i++) m = Math.max(m, Math.abs(d[i]));
        }
        return m;
      });
    },
    [draftId, [bar7b3 * SPB, bar8b3 * SPB]] as const,
  );
  console.log(`draft ${draftId} kick lane: bar7 beat3 ${draftPeaks[0].toFixed(4)}, bar8 beat3 ${draftPeaks[1].toFixed(4)}`);
  expect(draftPeaks[0]).toBeLessThan(1e-4);
  expect(draftPeaks[1]).toBeGreaterThan(0.05);
  await page.keyboard.press("f"); // back to the editor preview
  await expect.poll(() => page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.listen)).toBe("editor");

  // ---------------------------------------------------- undo / redo (byte-identical)
  const removed = songYaml(project);
  await page.keyboard.press("Control+z");
  await settle(page);
  expect(songYaml(project)).toBe(original);
  await page.keyboard.press("Control+y");
  await settle(page);
  expect(songYaml(project)).toBe(removed);

  // ---------------------------------------------------- change a hit's sound (the kick lane)
  const k1 = await at(page, "drums", { beat0: 0, lane: "kick" });
  await page.keyboard.down("Shift");
  await page.mouse.click(k1.x, k1.y);
  await page.keyboard.up("Shift");
  await page.getByTestId("insp-change-sound").click();
  await page.getByTestId("sound-browser").getByRole("searchbox").fill("clap");
  const use = page.getByTestId("sound-use").first();
  await expect(use).toBeEnabled({ timeout: 15_000 });
  await use.click();
  await settle(page);
  expect(songYaml(project)).toMatch(/kick: \{sample: "library:\/\/kits\/disco-01\/clap\.wav"/);
  await page.keyboard.press("Control+z"); // keep the kick a kick
  await settle(page);
  expect(songYaml(project)).toMatch(/kick: \{sample: "library:\/\/kits\/disco-01\/kick\.wav"/);

  // ---------------------------------------------------- move a clip: 4 passes, dragged 2 bars right
  const c = await at(page, "sample", { beat0: 6 });
  await page.mouse.click(c.x, c.y);
  const reps = page.getByLabel("Repeticiones", { exact: true });
  await reps.fill("4");
  await reps.press("Enter");
  await settle(page);
  const from = await at(page, "sample", { beat0: 6 });
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y, { steps: 4 });
  await page.mouse.move(from.x + 8 * 26, from.y, { steps: 6 });
  await page.mouse.up();
  await settle(page);
  expect(songYaml(project)).toMatch(/\{chop: c1, at_bar: 3, repeat: 4\}/);

  // ---------------------------------------------------- trim: one pass, right edge 2 beats earlier
  await reps.fill("1");
  await reps.press("Enter");
  await settle(page);
  const edge = await at(page, "sample", { beat0: 8 + 8 });
  await page.mouse.move(edge.x - 4, edge.y);
  await page.mouse.down();
  await page.mouse.move(edge.x - 30, edge.y, { steps: 4 });
  await page.mouse.move(edge.x - 4 - 2 * 26, edge.y, { steps: 4 });
  await page.mouse.up();
  await settle(page);
  expect(songYaml(project)).toMatch(/\{chop: c1, at_bar: 3, length_beats: 6\}/);

  // ---------------------------------------------------- add a note to the bass (double click on the roll)
  const notes0 = await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.song.tracks.find((t) => t.id === "bass")!.notes!.length);
  const n = await at(page, "bass", { beat0: 2.25, pitch: 46 });
  await page.mouse.dblclick(n.x, n.y);
  await settle(page);
  const notes1 = await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.state.song.tracks.find((t) => t.id === "bass")!.notes!.length);
  expect(notes1).toBe(notes0 + 1);
  expect(songYaml(project)).toMatch(/\{beat: 3\.25, pitch: \d+, length_beats: 0\.5\}/);

  // ---------------------------------------------------- ask the agent about one hit
  const k2 = await at(page, "drums", { beat0: 2 * 4, lane: "kick" });
  await page.keyboard.down("Shift");
  await page.mouse.click(k2.x, k2.y);
  await page.keyboard.up("Shift");
  const ask = page.getByTestId("ed-ask");
  await ask.fill("sacá esto");
  await ask.press("Enter");
  await expect
    .poll(() => {
      const lines = fs.readFileSync(path.join(project, "feedback.jsonl"), "utf-8").trim().split("\n");
      if (!lines[lines.length - 1]) return null;
      return JSON.parse(lines[lines.length - 1]) as { text?: string; selection?: { hits?: { bar: number; step: number; lane: string }[]; track?: string } };
    }, { timeout: 15_000 })
    .toMatchObject({ text: "sacá esto", selection: { track: "drums", hits: [{ lane: "kick", bar: 3, step: 0 }] } });

  // ---------------------------------------------------- save a version with the Spanish summary
  const n0 = ((await (await request.get("/project")).json()) as { versions: unknown[] }).versions.length;
  await expect(page.getByTestId("ed-dirty")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("ed-save").click();
  await expect
    .poll(async () => ((await (await request.get("/project")).json()) as { versions: unknown[] }).versions.length, { timeout: 120_000 })
    .toBe(n0 + 1);
  const newest = ((await (await request.get("/project")).json()) as { versions: { meta: { summary: string; agent: string } }[] }).versions[0].meta;
  console.log(`saved version summary: ${newest.summary}`);
  expect(newest.summary).toMatch(/^Edición manual: /);
  expect(newest.summary).toContain("saqué el bombo de la batería del tercer tiempo del compás 7");
  expect(newest.agent).toBe("muestra-editor");
  await expect(page.getByTestId("ed-clean")).toBeVisible({ timeout: 20_000 });
  const t = await page.evaluate(() => (window as unknown as { __muestra: { editor: Ed } }).__muestra.editor.timings);
  console.log(`timings ms: edits ${JSON.stringify(t.edit.map((x) => +x.toFixed(1)))} dry pieces ${JSON.stringify(t.pieces.map(Math.round))} wet ${JSON.stringify(t.wet.map(Math.round))}`);
  expect(errors).toEqual([]);
});
