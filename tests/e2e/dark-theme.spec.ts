import { test, expect } from "@playwright/test";

for (const viewport of [{ width: 800, height: 480 }, { width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test(`dark controls remain readable at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.locator("#header-current .current-raw")).toContainText("mV");
    for (const view of ["workbench", "pulse", "jog", "diagnostics"]) {
      await page.locator(`#${view}-tab`).click();
      if (view === "pulse") {
        await expect(page.locator("#robot-model-revision")).toBeEnabled();
        await page.locator("#robot-model-links > details").first().locator("summary").click();
        await page.locator("#robot-model-joints > details").first().locator("summary").click();
        await page.locator("#legacy-geometry > summary").click();
        await expect(page.locator("#geometry-clearance")).toBeEnabled();
        await page.locator("#geometry-joint-details summary").click();
      }
      if (view === "jog") await expect(page.locator("#manual-joints button")).toHaveCount(6);
      await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
      const ratios = await page.evaluate(() => {
        function rgb(color: string) { return color.match(/[\d.]+/g)!.slice(0, 3).map(Number); }
        function luminance(color: string) {
          const channels = rgb(color).map(v => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
          return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
        }
        function contrast(a: string, b: string) {
          const x = luminance(a), y = luminance(b);
          return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
        }
        function background(element: Element): string {
          const color = getComputedStyle(element).backgroundColor;
          return color === "rgba(0, 0, 0, 0)" && element.parentElement ? background(element.parentElement) : color;
        }
        return [...document.querySelectorAll("button, input:not([type=checkbox]), select, textarea, .caption, .current-interlock, #init-error, #manual-clearance")]
          .filter(element => element.getClientRects().length && !(element as HTMLButtonElement).disabled)
          .map(element => ({ label: element.id || element.textContent?.slice(0, 40), ratio: contrast(getComputedStyle(element).color, background(element)) }));
      });
      for (const { label, ratio } of ratios) expect(ratio, `${label} text contrast`).toBeGreaterThanOrEqual(4.5);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `test-results/dark-${view}-${viewport.width}.png`, fullPage: true });
    }
    await page.locator("#header-current summary").click();
    await expect(page.locator("#header-current .current-details-body")).toBeVisible();
    await page.screenshot({ path: `test-results/dark-meter-details-${viewport.width}.png` });
    await page.locator("#jog-tab").focus();
    await page.keyboard.press("Tab");
    await expect(page.locator("#diagnostics-tab")).toBeFocused();
    await expect(page.locator("#diagnostics-tab")).toHaveCSS("outline-style", "solid");
  });
}
