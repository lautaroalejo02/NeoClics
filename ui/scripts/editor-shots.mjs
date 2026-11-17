// Screenshots of the arrangement editor on a real server:
//   node scripts/editor-shots.mjs <url> <outdir> [only]
// Desktop 1440 and a 390 px phone, dark and light; also prints console
// errors, horizontal overflow and the editor's own frame/edit timings.
import { chromium, devices } from "@playwright/test";

const [url, out, only] = process.argv.slice(2);
const b = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });

async function shot(name, ctxOpts, theme, steps) {
  if (only && !name.includes(only)) return;
  const ctx = await b.newContext(ctxOpts);
  const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log(`[${name}] pageerror`, e.message));
  p.on("console", (m) => m.type() === "error" && console.log(`[${name}] console`, m.text()));
  await p.addInitScript((t) => {
    localStorage.setItem("muestra.prefs.v1", JSON.stringify({ theme: t }));
    localStorage.setItem("muestra.view", "arreglo");
  }, theme);
  await p.goto(url);
  await p.waitForSelector('[data-testid="timeline"]', { timeout: 30000 });
  await p.waitForTimeout(2500);
  await steps?.(p);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(name, "horizontal overflow px:", overflow);
  await p.screenshot({ path: `${out}/${name}.png` });
  await ctx.close();
}

const desk = { viewport: { width: 1440, height: 900 } };
const phone = { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } };
const clickCanvas = async (p, fx, fy) => {
  const box = await p.getByTestId("timeline").boundingBox();
  await p.mouse.click(box.x + box.width * fx, box.y + fy);
};
for (const theme of ["dark", "light"]) {
  await shot(`ed-desk-${theme}`, desk, theme);
  await shot(`ed-desk-${theme}-track`, desk, theme, async (p) => {
    await p.getByTestId("ed-head-sample").locator(".ed-head-name").click();
    await p.waitForTimeout(300);
  });
  await shot(`ed-desk-${theme}-clip`, desk, theme, async (p) => {
    await clickCanvas(p, 0.3, 60);
    await p.waitForTimeout(300);
  });
  await shot(`ed-phone-${theme}`, phone, theme);
}
const shiftClickHit = async (p) => {
  const pt = await p.evaluate(() => window.__muestra.editor.locate("drums", { beat0: 26, lane: "kick" }));
  const box = await p.getByTestId("timeline").boundingBox();
  await p.keyboard.down("Shift");
  await p.mouse.click(box.x + pt.x, box.y + pt.y);
  await p.keyboard.up("Shift");
  await p.waitForTimeout(300);
};
for (const theme of ["dark", "light"]) {
  await shot(`ed-desk-${theme}-hits`, desk, theme, shiftClickHit);
  await shot(`ed-desk-${theme}-source`, desk, theme, async (p) => {
    await p.getByTestId("ed-source").click();
    await p.waitForTimeout(3500);
  });
  await shot(`ed-phone-${theme}-sheet`, phone, theme, async (p) => {
    await shiftClickHit(p);
    await p.getByLabel("Panel de detalles").tap();
    await p.waitForTimeout(300);
  });
}
await shot("ed-desk-dark-help", desk, "dark", async (p) => {
  await p.keyboard.press("?");
  await p.waitForTimeout(300);
});
await b.close();
