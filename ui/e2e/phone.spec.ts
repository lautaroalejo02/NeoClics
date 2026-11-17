import { expect, test } from "@playwright/test";

// WP14 finding 7: phone-sized checks (portrait, landscape, on-screen keyboard).

test.beforeEach(async ({ request }, info) => {
  test.skip(!info.project.name.startsWith("phone"), "phone viewport only");
  await request.post("/debug/reset");
});

test("the pinned mini transport shows when the play button scrolls away, and hides while typing", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("now-version")).toHaveText("v004");
  await page.getByTestId("tab-versions").tap();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(page.locator(".minibar")).toBeVisible();
  const box = await page.locator(".minibar").boundingBox();
  const vh = await page.evaluate(() => window.innerHeight);
  expect(box!.y + box!.height).toBeLessThanOrEqual(vh);
  // typing a general comment: the bar steps aside
  await page.getByTestId("tab-feedback").tap();
  await page.getByTestId("general-comment").tap();
  await expect(page.locator(".minibar")).toHaveCount(0);
  await page.getByTestId("general-comment").blur();
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".minibar")).toHaveCount(0); // the real transport is on screen
});

test("no sideways scroll in any tab, portrait or landscape", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("now-version")).toHaveText("v004");
  for (const size of [{ width: 393, height: 851 }, { width: 851, height: 393 }, { width: 360, height: 640 }]) {
    await page.setViewportSize(size);
    for (const tab of ["versions", "mixer", "feedback", "ears", "chops"]) {
      const t = page.getByTestId(`tab-${tab}`);
      if (await t.count()) await t.tap();
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over, `${tab} @ ${size.width}x${size.height}`).toBeLessThanOrEqual(0);
    }
  }
});
