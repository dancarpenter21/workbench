import { test, expect } from "@playwright/test";

test("ammeter text is consistent and signed readings do not move its icon", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.addInitScript(() => {
    (window as any).__meterReading = { bus_v: 0.004, shunt_mv: 0.02, current_a: 0.001 };
    class MeterEvents {
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
    (window as any).EventSource = MeterEvents;
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
