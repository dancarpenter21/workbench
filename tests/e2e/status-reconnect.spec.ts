import { test, expect } from "@playwright/test";

test("device indicators recover after the status stream initially returns an error", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/coordinator/events", async route => {
    if (++attempts === 1) await route.fulfill({ status: 503, contentType: "text/plain", body: "Service restarting" });
    else await route.continue();
  });
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveAttribute("data-state", "ok");
  await expect(page.locator("#header-current")).toHaveAttribute("data-device-state", "ok");
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("a silent status stream is replaced without restoring motion ownership", async ({ page }) => {
  await page.addInitScript(() => {
    let attempts = 0;
    class DeviceEvents {
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      timer: ReturnType<typeof setInterval> | null = null;
      constructor() {
        (window as any).__statusAttempts = ++attempts;
        if (attempts === 1) return; // Open connection, but no messages or error events.
        this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
          mode: "mock", profile: "workbench", retrieval_enabled: true, operation: null,
          recovery_required: false, message: "", manual: null, commissioning: null,
          servo_hat: { state: "detected", simulated: true, address: 64, age_seconds: 0.01 },
          claw_current: { simulated: true, reading_healthy: true, healthy: true, fault: "",
            age_seconds: 0.01, bus_v: 0, shunt_mv: 0, current_a: null, scale_verified: false, power_state: "off" },
        }) }), 75);
      }
      close() { if (this.timer) clearInterval(this.timer); }
    }
    (window as any).EventSource = DeviceEvents;
  });
  const motionRequests: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && /\/(initialize|target|session|input)$/.test(request.url())) motionRequests.push(request.url());
  });
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveAttribute("data-state", "waiting");
  await expect(page.locator("#connection")).toHaveAttribute("data-state", "ok", { timeout: 8000 });
  expect(await page.evaluate(() => (window as any).__statusAttempts)).toBe(2);
  expect(motionRequests).toEqual([]);
});
