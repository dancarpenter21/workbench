import { test, expect } from "@playwright/test";

test("brief late updates keep ammeter status steady, but actual read failures show immediately", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__meterPaused = false;
    (window as any).__meterFailed = false;
    class MeterEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      timer: ReturnType<typeof setInterval>;
      constructor() {
        this.timer = setInterval(() => {
          if ((window as any).__meterPaused) return;
          const failed = (window as any).__meterFailed;
          this.onmessage?.({ data: JSON.stringify({
            mode: "mock", commissioning_ready: true, retrieval_enabled: true, operation: null,
            recovery_required: false, message: "", manual: null, commissioning: null,
            claw_current: { simulated: true, reading_healthy: !failed, healthy: !failed,
              fault: failed ? "I2C read failed. CUT PHYSICAL SERVO POWER" : "", age_seconds: 0.01,
              bus_v: 0, shunt_mv: 0, current_a: null, scale_verified: false, power_state: "off" },
          }) });
        }, 75);
      }
      close() { clearInterval(this.timer); }
    }
    (window as any).WebSocket = new Proxy(window.WebSocket, {
      construct(native, args) {
        return String(args[0]).includes("/ws/status") ? new MeterEvents() : Reflect.construct(native, args);
      },
    });
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

test("short status gaps retain confirmed power, but expiry, faults and power changes do not", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__powerSample = {
      simulated: true, reading_state: "ok", reading_healthy: true, healthy: true,
      reset_required: false, fault: "", age_seconds: 0.01, monitor_session_id: "fixture",
      bus_v: 0.004, shunt_mv: 0, current_a: null, scale_verified: false, power_state: "off",
    };
    class PowerEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
      onmessage: ((event: { data: string }) => void) | null = null;
      timer: ReturnType<typeof setInterval>;
      constructor() {
        this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
          mode: "mock", commissioning_ready: true, retrieval_enabled: false, operation: null,
          recovery_required: false, message: "", manual: null, commissioning: null,
          claw_current: (window as any).__powerSample,
        }) }), 50);
      }
      close() { clearInterval(this.timer); }
    }
    (window as any).WebSocket = new Proxy(window.WebSocket, {
      construct(native, args) {
        return String(args[0]).includes("/ws/status") ? new PowerEvents() : Reflect.construct(native, args);
      },
    });
  });
  await page.goto("/");
  const power = page.locator("#servo-power");
  await expect(power).toContainText("Powered off");
  const healthy = await page.evaluate(() => (window as any).__powerSample);
  await page.evaluate(() => { (window as any).__powerSample = null; });
  await expect(power).toHaveAttribute("data-cached", "true");
  await page.waitForTimeout(300);
  await expect(power).toContainText("Powered off");
  await expect(power).toHaveAttribute("aria-label", /simulated/);
  await expect(power).toContainText("Power unknown", { timeout: 2000 });
  await expect(power).toHaveAttribute("data-cached", "false");

  await page.evaluate(sample => { (window as any).__powerSample = sample; }, healthy);
  await expect(power).toContainText("Powered off");
  await page.evaluate(sample => {
    (window as any).__powerSample = { ...sample, reading_state: "stale", reading_healthy: false, power_state: "unknown", age_seconds: 0.4 };
  }, healthy);
  await expect(power).toHaveAttribute("data-cached", "true");
  await expect(power).toContainText("Powered off");
  await expect(page.locator("#header-current .current-label")).toHaveText("Updates delayed");
  await expect(power).toContainText("Power unknown", { timeout: 2000 });

  await page.evaluate(sample => { (window as any).__powerSample = sample; }, healthy);
  await expect(power).toContainText("Powered off");
  await page.evaluate(sample => {
    (window as any).__powerSample = { ...sample, reading_state: "error", reading_healthy: false, power_state: "unknown", reset_required: true, fault: "I2C read failure" };
  }, healthy);
  await expect(power).toContainText("Power unknown", { timeout: 500 });
  await expect(power).toHaveAttribute("data-cached", "false");

  await page.evaluate(sample => { (window as any).__powerSample = sample; }, healthy);
  await expect(power).toContainText("Powered off");
  await page.evaluate(sample => {
    (window as any).__powerSample = { ...sample, power_state: "energized", bus_v: 5 };
  }, healthy);
  await expect(power.locator(".power-label")).toHaveText("Powered", { timeout: 500 });
  await page.evaluate(sample => {
    (window as any).__powerSample = { ...sample, power_state: "unknown" };
  }, healthy);
  await expect(power).toContainText("Power unknown", { timeout: 500 });
});
