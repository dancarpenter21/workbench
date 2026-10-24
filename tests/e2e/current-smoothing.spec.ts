import { test, expect } from "@playwright/test";

test("brief late updates keep ammeter status steady, but actual read failures show immediately", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__meterPaused = false;
    (window as any).__meterFailed = false;
    class MeterEvents {
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      timer: ReturnType<typeof setInterval>;
      constructor() {
        this.timer = setInterval(() => {
          if ((window as any).__meterPaused) return;
          const failed = (window as any).__meterFailed;
          this.onmessage?.({ data: JSON.stringify({
            mode: "mock", profile: "workbench", retrieval_enabled: true, operation: null,
            recovery_required: false, message: "", manual: null, commissioning: null,
            claw_current: { simulated: true, reading_healthy: !failed, healthy: !failed,
              fault: failed ? "I2C read failed. CUT PHYSICAL SERVO POWER" : "", age_seconds: 0.01,
              bus_v: 0, shunt_mv: 0, current_a: null, scale_verified: false, power_state: "off" },
          }) });
        }, 75);
      }
      close() { clearInterval(this.timer); }
    }
    (window as any).EventSource = MeterEvents;
  });
  await page.goto("/");
  const meter = page.locator("#header-current");
  await expect(meter).toHaveAttribute("data-device-state", "ok");
  await page.evaluate(() => { (window as any).__meterPaused = true; });
  await page.waitForTimeout(450); // Beyond the controller deadline; still within the UI grace.
  await expect(meter).toHaveAttribute("data-device-state", "ok");
  await expect(page.locator("#servo-power")).toContainText("Powered off");
  await expect(meter).toContainText("Updates delayed", { timeout: 2000 });
  await expect(meter).toHaveAttribute("data-device-state", "waiting");
  await expect(meter.locator(".current-fault")).toBeEmpty();
  await page.evaluate(() => { (window as any).__meterPaused = false; });
  await expect(meter).toHaveAttribute("data-device-state", "ok");
  await page.evaluate(() => { (window as any).__meterFailed = true; });
  await expect(meter).toHaveAttribute("data-device-state", "error", { timeout: 500 });
  await expect(meter).toContainText("CUT PHYSICAL SERVO POWER");
});
