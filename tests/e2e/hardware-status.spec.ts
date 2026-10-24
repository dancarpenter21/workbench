import { test, expect } from "@playwright/test";

for (const state of ["off", "energized", "latched_off", "latched_on", "error", "unreadable", "stale", "missing", "unknown"]) {
  test(`shared hardware status stays consistent across tabs: ${state}`, async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 480 });
    await page.addInitScript(({ state }) => {
      (window as any).__pauseHardware = false;
      const latched = state.startsWith("latched") || ["error", "unreadable", "stale"].includes(state);
      const powered = state === "energized" || state === "latched_on";
      const valid = state !== "error" && state !== "unreadable" && state !== "stale";
      const sample = state === "missing" ? null : {
        simulated: true, reading_state: (state === "error" || state === "unreadable") ? "error" : state === "stale" ? "stale" : "ok",
        reading_healthy: valid, healthy: valid && !latched, reset_required: latched,
        fault: latched ? (state === "error" || state === "unreadable") ? "Injected I2C read failure" : "Earlier stale INA219 readings" : "",
        sampled_at: new Date().toISOString(), age_seconds: state === "unreadable" ? null : state === "stale" ? 2 : 0.01,
        bus_v: powered ? 5 : 0.004, shunt_mv: -0.06, current_a: null, scale_verified: false,
        power_state: !valid || state === "unknown" ? "unknown" : powered ? "energized" : "off",
        off_bounds: state === "unknown" ? null : {
          baseline_id: "simulated-off", domain: "simulated", i2c_bus: 1, sensor_address: 65,
          pwm_address: 64, max_bus_v: 0.02, max_abs_shunt_mv: 0.12, evidence: "Simulated fixture",
        },
      };
      class HardwareEvents {
        onmessage: ((event: { data: string }) => void) | null = null;
        constructor() {
          setInterval(() => {
            if ((window as any).__pauseHardware) return;
            this.onmessage?.({ data: JSON.stringify({
              mode: "mock", commissioning_ready: true, retrieval_enabled: false,
              operation: null, recovery_required: latched, message: "", manual: null, commissioning: null,
              claw_current: sample,
              servo_hat: { simulated: true, state: "detected", age_seconds: 0.01, address: 64, detail: "Simulated cached HAT" },
              neutral_evidence: { state: valid && !powered && state !== "unknown" ? "off" : "not_neutral", commanded_neutral: false,
                detail: valid && !powered && state !== "unknown" ? "Supply off; recorded powered-neutral reference retained" : "Neutral comparison inactive" },
            }) });
          }, 75);
        }
        close() {}
      }
      (window as any).EventSource = HardwareEvents;
    }, { state });
    const posts: string[] = [];
    page.on("request", request => { if (request.method() === "POST") posts.push(request.url()); });
    await page.goto("/");
    const power = state === "off" || state === "latched_off" ? "Powered off" : state === "energized" || state === "latched_on" ? "Powered" : "Power unknown";
    for (const tab of ["workbench", "pulse", "jog", "diagnostics"]) {
      await page.locator(`#${tab}-tab`).click();
      await expect(page.locator("#servo-power .power-label")).toHaveText(power);
      if (state.startsWith("latched")) {
        await expect(page.locator("#header-current .current-interlock")).toHaveText("Motion inhibited — reset required");
        await expect(page.locator("#header-current .current-fault")).toBeEmpty();
      }
    }
    await expect(page.locator("#diagnostic-servo-power .power-label")).toHaveText(power);
    await expect(page.locator("#diagnostic-live-hat")).toHaveAttribute("data-state", "ok");
    await expect(page.locator("#diagnostic-note")).toContainText("SIMULATED");
    await expect(page.locator("#diagnostic-note")).not.toContainText("Diagnostics profile");
    if (["off", "energized", "latched_off", "latched_on", "unknown"].includes(state)) {
      await expect(page.locator("#diagnostic-sensor-state")).toHaveText("Sensor responding");
    }
    if (state === "error" || state === "unreadable") {
      await expect(page.locator("#diagnostic-sensor-state")).toHaveText("Sensor fault");
    }
    if (state.startsWith("latched")) {
      await expect(page.locator("#diagnostic-interlock-state")).toHaveText("Motion inhibited — reset required");
      await expect(page.locator("#diagnostic-interlock-reason")).toContainText("Earlier stale");
    }
    if (state === "latched_off") {
      await expect(page.locator("#diagnostic-neutral-state")).toContainText("Supply off");
      await page.screenshot({ path: "test-results/diagnostics-recovered-powered-off.png", fullPage: true });
    }
    if (state === "off") {
      await page.evaluate(() => { (window as any).__pauseHardware = true; });
      await expect(page.locator("#servo-power .power-label")).toHaveText("Power unknown");
      await expect(page.locator("#diagnostic-servo-power .power-label")).toHaveText("Power unknown");
      await expect(page.locator("#diagnostic-sensor-state")).toHaveText("Updates delayed");
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(posts).toEqual([]); // Viewing status never resets a fault or commands output.
  });
}
