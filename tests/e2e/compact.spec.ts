import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
});

for (const state of ["off", "noise", "voltage", "on", "fault", "stale", "missing"] as const) {
  test(`current indicator distinguishes ${state} at 800×480`, async ({ page }) => {
    await page.addInitScript(({ state }) => {
      const body: Record<string, unknown> = { available: true, simulated: true, reserved: false, stage: "idle", targets: {}, encoded_targets: {}, windows: {} };
      body.claw_current = state === "missing" ? null : {
        simulated: true, healthy: state !== "fault", fault: state === "fault" ? "Sensor disconnected" : "",
        sampled_at: new Date().toISOString(), age_seconds: state === "stale" ? 2 : 0.01,
        bus_v: state === "off" ? 0 : state === "noise" ? 0.008 : 5,
        shunt_mv: state === "on" ? 4 : state === "noise" ? -0.04 : 0,
        power_state: state === "off" || state === "noise" ? "off" : "energized",
        off_bounds: { baseline_id: "test-baseline", domain: "simulated", i2c_bus: 1,
          sensor_address: 65, pwm_address: 64, max_bus_v: 0.020, max_abs_shunt_mv: 0.080,
          evidence: "Simulated fixture" },
        current_a: null, scale_verified: false, cutoff_a: 2.5,
      };
      class FixtureEvents {
        onmessage: ((event: { data: string }) => void) | null = null;
        constructor() {
          setInterval(() => this.onmessage?.({ data: JSON.stringify({
            mode: "mock", profile: "workbench", retrieval_enabled: true, operation: null,
            recovery_required: false, message: "", manual: null, commissioning: body,
            claw_current: body.claw_current,
          }) }), 75);
        }
      }
      (window as any).EventSource = FixtureEvents;
    }, { state });
    await page.goto("/");
    await page.locator("#pulse-tab").click();
    const current = page.locator("#header-current");
    await expect(current).toHaveAttribute("data-state", state === "off" || state === "noise" ? "off" : state === "on" || state === "voltage" ? "ok" : state === "missing" || state === "stale" ? "waiting" : "error");
    if (state === "off" || state === "noise") {
      await expect(page.locator("#servo-power")).toContainText("Powered off");
      await expect(current).not.toContainText("Powered off");
      await expect(current.locator(".current-fault")).toBeEmpty();
      await expect(current.locator(".current-scale")).toBeHidden();
      await current.locator("summary").click();
      await expect(current.locator(".current-scale")).toContainText("2.5 A cutoff UNVERIFIED");
    } else {
      await expect(current).not.toContainText("Powered off");
    }
  });
}

test("small display has separate views, visible stop and no horizontal scrolling", async ({ page }) => {
  await page.goto("/");
  for (const view of ["workbench", "pulse", "jog", "diagnostics"]) {
    await page.locator(`#${view}-tab`).click();
    await expect(page.locator("#stop")).toBeVisible();
    await expect(page.locator("#header-current")).toBeInViewport({ ratio: 1 });
    const header = (await page.locator("header").boundingBox())!;
    const readings = (await page.locator("#header-current").boundingBox())!;
    expect(readings.y).toBeGreaterThanOrEqual(header.y);
    expect(readings.y + readings.height).toBeLessThanOrEqual(header.y + header.height);
    await expect(page.locator("#trial-current, #manual-current, #workbench-current")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.locator("#camera")).toBeVisible();
    await expect(page.locator(".camera-panel #stop")).toBeVisible();
    await expect(page.locator("#tools, #mode")).toHaveCount(0);
    await expect.poll(() => page.locator("#camera").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await page.screenshot({ path: `test-results/compact-${view}-800.png` });
  }
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.locator("#workbench-tab").click();
  await page.screenshot({ path: "test-results/compact-workbench-1024.png" });
});

test("header readings stay visible and Stop sits in the workbench widget on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#header-current .current-raw")).toContainText("mV");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(page.locator("#header-current")).toBeInViewport({ ratio: 1 });
  await page.locator("#stop").scrollIntoViewIfNeeded();
  await expect(page.locator(".camera-panel #stop")).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/header-current-phone.png" });
});

test("desktop status indicators use their content width and sit together on the right", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.goto("/");
  await expect(page.locator("#header-current .current-raw")).toContainText("mV");
  const logo = (await page.locator("header .wordmark").boundingBox())!;
  const meter = (await page.locator("#header-current").boundingBox())!;
  const controls = (await page.locator("header .header-status").boundingBox())!;
  expect(meter.x).toBeGreaterThan(logo.x + logo.width + 100);
  expect(meter.width).toBeLessThan(650);
  expect(controls.x - meter.x - meter.width).toBeGreaterThanOrEqual(0);
  expect(controls.x - meter.x - meter.width).toBeLessThanOrEqual(16);
  await page.screenshot({ path: "test-results/header-right-aligned.png" });
});
