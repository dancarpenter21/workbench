import { test, expect } from "@playwright/test";

test("independent Pi heartbeat detects failure and recovers", async ({ page }) => {
  let fail = false;
  await page.route("**/api/coordinator/health", route => fail
    ? route.abort("failed")
    : route.fulfill({ json: { service: "coordinator", mode: "mock", ready: true } }));
  await page.goto("/");
  const network = page.locator("#network-status");
  await expect(page.locator("header #network-status")).toBeVisible();
  await expect(network).toHaveAttribute("data-state", "connected");
  await network.locator("summary").click();
  await expect(page.locator("#network-details")).toContainText("Failed probes: 0/");
  await expect(page.locator("#servo-power .power-label")).toHaveText(/Powered/);
  const initialPower = await page.locator("#servo-power .power-label").textContent();
  fail = true;
  await expect(network).toHaveAttribute("data-state", "offline", { timeout: 10000 });
  await expect(page.locator("#network-details")).toContainText("Last probe failed");
  // A failed HTTP heartbeat does not impersonate an ammeter power change.
  await expect(page.locator("#servo-power .power-label")).toHaveText(initialPower!);
  await page.screenshot({ path: "test-results/network-heartbeat-offline.png" });
  fail = false;
  await expect(network).toHaveAttribute("data-state", "connected");
  await expect(page.locator("#network-details")).toContainText("consecutive: 0");
  for (const width of [800, 375]) {
    await page.setViewportSize({ width, height: 600 });
    await expect(network).toBeInViewport();
    const bounds = await network.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    const powerBounds = await page.locator("#servo-power").boundingBox();
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(powerBounds!.x);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `test-results/network-header-${width}.png` });
  }
});

test("heartbeat times out a stalled request and resumes probing", async ({ page }) => {
  let stall = true;
  await page.route("**/api/coordinator/health", async route => {
    if (stall) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      await route.abort().catch(() => {});
    } else {
      await route.fulfill({ json: { service: "coordinator" } });
    }
  });
  await page.goto("/");
  await page.locator("#network-status summary").click();
  await expect(page.locator("#network-details")).toContainText("Last probe timed out");
  stall = false;
  await expect(page.locator("#network-status")).toHaveAttribute("data-state", "connected", { timeout: 10000 });
});
