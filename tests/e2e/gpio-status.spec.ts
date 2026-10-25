import { test, expect } from "@playwright/test";

for (const state of ["detected", "hat_missing", "hat_stale", "ammeter_missing", "ammeter_stale"] as const) {
  test(`header peripheral status: ${state}`, async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 480 });
    await page.addInitScript(({ state }) => {
      const sample = {
        simulated: false, reading_healthy: state !== "ammeter_missing", healthy: state !== "ammeter_missing",
        fault: state === "ammeter_missing" ? "I2C read failed" : "", age_seconds: state === "ammeter_stale" ? 2 : 0.01,
        bus_v: 0.008, shunt_mv: -0.04, current_a: null, scale_verified: false, power_state: "off",
      };
      const hat = { state: state === "hat_missing" ? "error" : "detected", simulated: false,
        address: 64, age_seconds: state === "hat_stale" ? 4 : 0.01, detail: state === "hat_missing" ? "No response at 0x40" : "Register reads succeeded" };
      class DeviceEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
        onmessage: ((event: { data: string }) => void) | null = null;
        onerror: (() => void) | null = null;
        constructor() {
          (window as any).__deviceEvents = this;
          setInterval(() => {
            if (!(window as any).__deviceDisconnected) this.onmessage?.({ data: JSON.stringify({
              mode: "hardware", commissioning_ready: true, retrieval_enabled: false, operation: null,
              recovery_required: false, message: "", servo_hat: hat, claw_current: sample,
              manual: null, commissioning: null,
            }) });
          }, 75);
        }
      }
      (window as any).WebSocket = new Proxy(window.WebSocket, {
      construct(native, args) {
        return String(args[0]).includes("/ws/status") ? new DeviceEvents() : Reflect.construct(native, args);
      },
    });
    }, { state });
    await page.goto("/");
    const hat = page.locator("#connection");
    const ammeter = page.locator("#header-current");
    await expect(hat).toHaveAttribute("data-state", state === "hat_missing" ? "error" : state === "hat_stale" ? "waiting" : "ok");
    await expect(hat).toContainText("Servo HAT");
    await expect(ammeter).toContainText("Ammeter");
    await expect(ammeter).toHaveAttribute("data-device-state", state === "ammeter_missing" ? "error" : state === "ammeter_stale" ? "waiting" : "ok");
    await expect(ammeter.locator(".current-raw")).toContainText("-0.04 mV");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (state === "detected") {
      await page.screenshot({ path: "test-results/header-peripherals.png" });
      await page.evaluate(() => {
        (window as any).__deviceDisconnected = true;
        (window as any).__deviceEvents.onerror();
      });
      await expect(hat).toHaveAttribute("data-state", "waiting");
      await expect(ammeter).toHaveAttribute("data-device-state", "waiting");
    }
  });
}
