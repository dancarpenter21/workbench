import { test, expect } from "@playwright/test";

test("ammeter text is consistent and signed readings do not move its icon", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.addInitScript(() => {
    (window as any).__meterReading = { bus_v: 0.004, shunt_mv: 0.02, current_a: 0.001 };
    class MeterEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
      onmessage: ((event: { data: string }) => void) | null = null;
      timer: ReturnType<typeof setInterval>;
      constructor() {
        this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
          mode: "mock", commissioning_ready: true, retrieval_enabled: true, operation: null,
          recovery_required: false, message: "", manual: null, commissioning: null,
          claw_current: { simulated: true, reading_healthy: true, healthy: true, fault: "",
            age_seconds: 0.01, scale_verified: true, power_state: "energized",
            shunt_ohms: 0.1, verification_evidence: "Simulated test fixture",
            ...(window as any).__meterReading },
        }) }), 75);
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
  await expect(meter.locator(".current-shunt .current-value")).toHaveText("0.02 mV");
  const initial = (await meter.boundingBox())!;
  const icon = (await meter.locator(".status-light").boundingBox())!;
  const typography = await meter.evaluate(root => [".current-domain", ".current-label", ".current-raw", "summary"].map(selector => {
    const style = getComputedStyle(root.querySelector(selector)!);
    return [style.fontFamily, style.fontSize, style.fontWeight].join("|");
  }));
  expect(new Set(typography).size).toBe(1);
  for (const values of [
    { bus_v: 5.1, shunt_mv: -0.06, current_a: -0.15 },
    { bus_v: 12.34, shunt_mv: -123.45, current_a: 1.875 },
    { bus_v: 0.008, shunt_mv: 0.04, current_a: 0.002 },
  ]) {
    await page.evaluate(values => { (window as any).__meterReading = values; }, values);
    await expect(meter.locator(".current-shunt .current-value")).toHaveText(`${values.shunt_mv.toFixed(2)} mV`);
    const updated = (await meter.boundingBox())!;
    const updatedIcon = (await meter.locator(".status-light").boundingBox())!;
    expect(updated.width).toBeCloseTo(initial.width, 1);
    expect(updatedIcon.x).toBeCloseTo(icon.x, 1);
    expect(updatedIcon.y).toBeCloseTo(icon.y, 1);
  }
  await page.screenshot({ path: "test-results/ammeter-clean.png" });
  await page.setViewportSize({ width: 800, height: 480 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator("#servo-power")).toBeInViewport({ ratio: 1 });
  await expect(meter.locator(".current-amperes")).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: "test-results/ammeter-clean-800.png" });
});

test("hardware pills share typography and styling, and Details stays in place when expanded", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.goto("/");
  const meter = page.locator("#header-current");
  const hat = page.locator("#connection");
  await expect(meter).toHaveAttribute("data-pill-state", "ok");
  await expect(hat).toHaveAttribute("data-pill-state", "ok");
  const style = (element: Element) => {
    const css = getComputedStyle(element);
    return [css.fontFamily, css.fontSize, css.fontWeight, css.color, css.backgroundColor, css.borderColor, css.borderRadius, css.padding].join("|");
  };
  expect(await meter.evaluate(style)).toBe(await hat.evaluate(style));
  const iconStyle = (element: Element) => {
    const css = getComputedStyle(element);
    return [css.width, css.height, css.borderRadius, css.backgroundColor, css.fontSize].join("|");
  };
  expect(await meter.locator(".status-light").evaluate(iconStyle)).toBe(await hat.locator(".status-light").evaluate(iconStyle));
  const details = meter.locator("summary");
  const before = (await details.boundingBox())!;
  const pillBefore = (await meter.boundingBox())!;
  await details.click();
  await expect(meter.locator(".current-details-body")).toBeVisible();
  const after = (await details.boundingBox())!;
  const pillAfter = (await meter.boundingBox())!;
  expect(after.x).toBeCloseTo(before.x, 1);
  expect(after.y).toBeCloseTo(before.y, 1);
  expect(pillAfter.width).toBeCloseTo(pillBefore.width, 1);
  await page.screenshot({ path: "test-results/hardware-pills-consistent.png" });
  await details.click();
  await expect(meter.locator(".current-details-body")).toBeHidden();
});

for (const width of [1400, 800, 390]) {
  test(`ammeter keeps its size across all text states at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.addInitScript(() => {
      (window as any).__meterSample = null;
      class MeterEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
        onmessage: ((event: { data: string }) => void) | null = null;
        timer: ReturnType<typeof setInterval>;
        constructor() {
          this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
            mode: "mock", commissioning_ready: true, retrieval_enabled: false,
            operation: null, recovery_required: false, message: "", manual: null,
            commissioning: null, claw_current: (window as any).__meterSample,
          }) }), 75);
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
    await expect(meter.locator(".current-label")).toHaveText("Sensor unavailable");
    const initial = (await meter.boundingBox())!;
    const initialHeader = (await page.locator("header").boundingBox())!;
    const samples = [
      { reading_state: "ok", age_seconds: 0.01, scale_verified: false, reset_required: false, simulated: false, fault: "" },
      { reading_state: "ok", age_seconds: 0.01, scale_verified: true, reset_required: false, simulated: false, fault: "" },
      { reading_state: "ok", age_seconds: 0.01, scale_verified: true, reset_required: true, simulated: true, fault: "Previous sensor interruption" },
      { reading_state: "stale", age_seconds: 2, scale_verified: false, reset_required: true, simulated: false, fault: "Stale INA219 readings" },
      { reading_state: "error", age_seconds: 0.01, scale_verified: true, reset_required: true, simulated: false,
        fault: "INA219 read failed at I2C address 0x41. CUT PHYSICAL SERVO POWER; support the arm. Do not retry motion automatically after a sensor fault." },
      { reading_state: "error", age_seconds: 0.01, scale_verified: false, reset_required: true, simulated: true, fault: "Simulated sensor disconnected" },
    ];
    for (const sample of samples) {
      await page.evaluate(sample => {
        (window as any).__meterSample = {
          ...sample, bus_v: 12.34, shunt_mv: -123.45, current_a: -2.499,
          power_state: "energized", shunt_ohms: 0.1,
        };
      }, sample);
      await expect(meter).toHaveAttribute("data-simulated", String(sample.simulated));
      await expect(meter.locator(".current-label")).toHaveText(
        sample.reading_state === "ok" ? "" : sample.reading_state === "error" ? "Sensor fault" : "Updates delayed",
      );
      await expect(meter.locator(".current-amperes")).toHaveCSS("visibility", sample.scale_verified ? "visible" : "hidden");
      const box = (await meter.boundingBox())!;
      const header = (await page.locator("header").boundingBox())!;
      expect(box).toEqual(initial);
      expect(header).toEqual(initialHeader);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (sample.reading_state === "error") {
        await expect(meter.locator(".current-fault")).toBeVisible();
        if (!sample.simulated) await expect(meter.locator(".current-fault")).toContainText("CUT PHYSICAL SERVO POWER");
        await meter.locator("summary").click();
        await expect(meter.locator(".current-fault-detail")).toHaveText(sample.fault);
        expect(await meter.boundingBox()).toEqual(initial);
        await meter.locator("summary").click();
      }
    }
    await page.screenshot({ path: `test-results/ammeter-fixed-${width}.png`, fullPage: true });
  });
}
