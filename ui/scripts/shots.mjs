// Screenshots of the real app (WP14 finding 7): node scripts/shots.mjs <url> <outdir>
import { chromium, devices } from "@playwright/test";
const [url, out] = process.argv.slice(2);
const b = await chromium.launch();
async function shot(name, ctxOpts, theme, steps) {
  const ctx = await b.newContext(ctxOpts);
  const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log(`[${name}] pageerror`, e.message));
  await p.addInitScript((t) => localStorage.setItem("muestra.prefs.v1", JSON.stringify({ theme: t })), theme);
  await p.goto(url);
  await p.waitForSelector('[data-testid="now-version"]');
  await p.waitForTimeout(1500);
  await steps?.(p);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(name, "horizontal overflow px:", overflow);
  await p.screenshot({ path: `${out}/${name}.png`, fullPage: name.includes("full") });
  await ctx.close();
}
const desk = { viewport: { width: 1440, height: 900 } };
const phone = { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } };
const land = { ...devices["Pixel 7"], viewport: { width: 844, height: 390 } };
for (const theme of ["dark", "light"]) {
  await shot(`desk-${theme}`, desk, theme);
  await shot(`desk-${theme}-ears`, desk, theme, (p) => p.getByTestId("tab-ears").click());
  await shot(`desk-${theme}-chops`, desk, theme, (p) => p.getByTestId("tab-chops").click());
  await shot(`phone-${theme}`, phone, theme);
  await shot(`phone-${theme}-mixer`, phone, theme, async (p) => (await p.getByTestId("tab-mixer").tap(), await p.waitForTimeout(400)));
  await shot(`phone-${theme}-ears`, phone, theme, (p) => p.getByTestId("tab-ears").tap());
  await shot(`phone-${theme}-chops`, phone, theme, (p) => p.getByTestId("tab-chops").tap());
  await shot(`phone-${theme}-versions`, phone, theme, (p) => p.getByTestId("tab-versions").tap());
  await shot(`land-${theme}`, land, theme);
}
// comment flow with the on-screen keyboard emulated: viewport shrinks to ~45%
await shot("phone-dark-keyboard", phone, "dark", async (p) => {
  await p.getByTestId("comment-here").scrollIntoViewIfNeeded();
  await p.getByTestId("comment-here").tap();
  await p.setViewportSize({ width: 390, height: 380 });
  await p.waitForTimeout(500);
  const input = p.getByTestId("quick-comment-input");
  await input.fill("el bajo tapa al bombo");
  const box = await input.boundingBox();
  const vh = await p.evaluate(() => window.innerHeight);
  console.log("keyboard: input box", JSON.stringify(box), "viewport h", vh, "visible:", box && box.y >= 0 && box.y + box.height <= vh);
});
await b.close();
