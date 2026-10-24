import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
});

for (const state of ["off", "noise", "voltage", "on", "fault", "stale", "missing"] as const) {
  test(`current indicator distinguishes ${state} at 800×480`, async ({ page }) => {
    await page.route("**/api/coordinator/commissioning/status", async route => {
      const body: Record<string, unknown> = { available: true, simulated: true, reserved: false, stage: "idle", targets: {}, encoded_targets: {}, windows: {} };
      body.claw_current = state === "missing" ? null : {
        simulated: true, healthy: state !== "fault", fault: state === "fault" ? "Sensor disconnected" : "",
        sampled_at: new Date().toISOString(), age_seconds: state === "stale" ? 1 : 0.01,
        bus_v: state === "off" ? 0 : state === "noise" ? 0.008 : 5,
        shunt_mv: state === "on" ? 4 : state === "noise" ? -0.04 : 0,
        power_state: state === "off" || state === "noise" ? "off" : "energized",
        off_bounds: { baseline_id: "test-baseline", domain: "simulated", i2c_bus: 1,
          sensor_address: 65, pwm_address: 64, max_bus_v: 0.020, max_abs_shunt_mv: 0.080,
          evidence: "Simulated fixture" },
        current_a: null, scale_verified: false, cutoff_a: 2.5,
      };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page.locator("#pulse-tab").click();
    const current = page.locator("#trial-current");
    await expect(current).toHaveAttribute("data-state", state === "off" || state === "noise" ? "off" : state === "on" || state === "voltage" ? "ok" : state === "missing" ? "waiting" : "error");
    if (state === "off" || state === "noise") {
      await expect(current).toContainText("Powered down");
      await expect(current.locator(".current-fault")).toBeEmpty();
      await expect(current.locator(".current-scale")).toBeHidden();
      await current.locator("summary").click();
      await expect(current.locator(".current-scale")).toContainText("2.5 A cutoff UNVERIFIED");
    } else {
      await expect(current).not.toContainText("Powered down");
    }
  });
}

test("small display has separate views, visible stop and no horizontal scrolling", async ({ page }) => {
  await page.goto("/");
  for (const view of ["workbench", "pulse", "jog", "diagnostics"]) {
    await page.locator(`#${view}-tab`).click();
    await expect(page.locator("#stop")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (view === "workbench") await expect.poll(() => page.locator("#camera").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await page.screenshot({ path: `test-results/compact-${view}-800.png` });
  }
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.locator("#workbench-tab").click();
  await page.screenshot({ path: "test-results/compact-workbench-1024.png" });
});
