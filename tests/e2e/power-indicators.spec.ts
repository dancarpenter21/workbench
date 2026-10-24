import { test, expect } from "@playwright/test";

for (const state of ["off", "energized", "fault", "stale", "unknown", "missing", "recovered_off", "recovered_powered"]) {
  test(`main Workbench supply indicator: ${state}`, async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 480 });
    await page.addInitScript(({ state }) => {
      const sample = state === "missing" ? null : {
        simulated: false, healthy: state !== "fault" && !state.startsWith("recovered"),
        reading_healthy: state !== "fault",
        fault: state === "fault" || state.startsWith("recovered") ? "Stale INA219 readings (earlier interruption)" : "",
        sampled_at: new Date().toISOString(), age_seconds: state === "stale" ? 2 : 0.01,
        bus_v: (state === "energized" || state === "recovered_powered") ? 5 : 0.004, shunt_mv: -0.06,
        current_a: null, scale_verified: false, power_state: (state === "energized" || state === "recovered_powered") ? "energized" : state === "unknown" ? "unknown" : "off",
        off_bounds: state === "unknown" ? null : {
          baseline_id: "20261023T214233Z-journal-97c7c90c", domain: "physical", i2c_bus: 1,
          sensor_address: 65, pwm_address: 64, max_bus_v: 0.020, max_abs_shunt_mv: 0.120, evidence: "Recorded capture fixture",
        },
      };
      class FixtureEvents {
        onmessage: ((event: { data: string }) => void) | null = null;
        onerror: (() => void) | null = null;
        constructor() {
          setInterval(() => {
            if (!(window as any).__pauseCurrent) this.onmessage?.({ data: JSON.stringify({
              mode: "hardware", profile: "workbench", retrieval_enabled: false, operation: null,
              recovery_required: false, message: "", manual: null, commissioning: null, claw_current: sample,
            }) });
          }, 75);
        }
      }
      (window as any).EventSource = FixtureEvents;
    }, { state });
    await page.goto("/");
    const badge = page.locator("#servo-power");
    const label = state === "off" || state === "recovered_off" ? "Powered down" : state === "energized" || state === "recovered_powered" ? "Powered" : "Power unknown";
    await expect(badge).toContainText(label);
    if (state.startsWith("recovered")) {
      await expect(badge).toHaveAttribute("data-state", state === "recovered_off" ? "off" : "ok");
      await expect(page.locator("#header-current .current-fault")).toBeEmpty();
      await page.locator("#header-current summary").click();
      await expect(page.locator("#header-current .current-recovery")).toContainText("previous monitoring interruption");
      if (state === "recovered_off") await page.screenshot({ path: "test-results/powered-off-normal.png" });
    }
    if (state === "off") {
      await expect(page.locator("#servo-power .power-label")).toHaveText("Powered down");
      await expect(page.locator("#header-current .current-raw")).toContainText("-0.06 mV");
      await page.locator("#header-current summary").click();
      await expect(page.locator("#header-current .current-baseline")).toContainText("0.120 mV");
      await expect(page.locator("#header-current .current-scale")).toContainText("UNVERIFIED");
      await page.screenshot({ path: "test-results/workbench-current-off-800.png" });
      await page.locator("#header-current summary").click();
      await page.locator("#jog-tab").click();
      await expect(badge).toContainText("Powered down");
      await page.evaluate(() => { (window as any).__pauseCurrent = true; });
      await expect(badge).toContainText("Power unknown");
    }
  });
}
