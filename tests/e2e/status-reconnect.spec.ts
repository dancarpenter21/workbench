import { test, expect } from "@playwright/test";

test("device indicators recover after the status stream initially returns an error", async ({ page }) => {
  let attempts = 0;
  await page.routeWebSocket("**/ws/status", socket => {
    if (++attempts === 1) socket.close({ code: 1013, reason: "Service restarting" });
    else socket.connectToServer();
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
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      timer: ReturnType<typeof setInterval> | null = null;
      constructor() {
        (window as any).__statusAttempts = ++attempts;
        if (attempts === 1) return; // Open connection, but no messages or error events.
        this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
          mode: "mock", commissioning_ready: true, retrieval_enabled: true, operation: null,
          recovery_required: false, message: "", manual: null, commissioning: null,
          servo_hat: { state: "detected", simulated: true, address: 64, age_seconds: 0.01 },
          claw_current: { simulated: true, reading_healthy: true, healthy: true, fault: "",
            age_seconds: 0.01, bus_v: 0, shunt_mv: 0, current_a: null, scale_verified: false, power_state: "off" },
        }) }), 75);
      }
      close() { if (this.timer) clearInterval(this.timer); }
    }
    (window as any).WebSocket = new Proxy(window.WebSocket, {
      construct(native, args) {
        return String(args[0]).includes("/ws/status") ? new DeviceEvents() : Reflect.construct(native, args);
      },
    });
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


test("a slow status client receives the latest snapshot after acknowledgement", async ({ page }) => {
  let hold = true;
  const samples: { claw_current: { sample_sequence: number; age_seconds: number } }[] = [];
  let acknowledge = () => {};
  await page.routeWebSocket("**/ws/status", socket => {
    const server = socket.connectToServer();
    server.onMessage(message => {
      samples.push(JSON.parse(String(message)));
      socket.send(message);
    });
    socket.onMessage(message => { if (!hold) server.send(message); });
    acknowledge = () => { hold = false; server.send("ack"); };
  });
  await page.goto("/");
  await expect.poll(() => samples.length).toBe(1);
  const first = samples[0].claw_current.sample_sequence;
  await page.waitForTimeout(650);
  expect(samples).toHaveLength(1);
  acknowledge();
  await expect.poll(() => samples.length).toBeGreaterThan(1);
  expect(samples[1].claw_current.sample_sequence).toBeGreaterThan(first + 2);
  expect(samples[1].claw_current.age_seconds).toBeLessThan(0.3);
});
