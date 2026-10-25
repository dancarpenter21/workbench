import { test, expect } from "@playwright/test";

test("diagnostics show devices, play Pi test audio, and export a report", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Diagnostics", exact: true }).click();
  await expect(page.locator("#diagnostic-mode")).toHaveText("SIMULATED");
  await expect(page.getByRole("heading", { name: "Servo HAT", exact: true })).toBeVisible();
  await expect(page.locator("#diagnostic-camera")).toHaveCount(0);
  await expect(page.locator("#camera")).toBeVisible();
  await expect(page.locator("#camera-status")).toContainText("Live");
  await expect.poll(() => page.locator("#camera").evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Test microphone" }).click();
  await expect(page.locator("#diagnostic-audio-note")).toContainText("SIMULATED", { timeout: 10000 });
  const audio = page.locator("#diagnostic-audio");
  await expect(audio).toBeVisible();
  await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.duration)).toBeCloseTo(5);
  await audio.evaluate((el: HTMLAudioElement) => el.play());
  await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.currentTime)).toBeGreaterThan(0);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download report" }).click();
  expect((await download).suggestedFilename()).toMatch(/^workbench-diagnostics-.*\.json$/);
  await page.screenshot({ path: "test-results/diagnostics-dashboard.png", fullPage: true });
});

test("retained readings become stale when the coordinator is unavailable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Diagnostics", exact: true }).click();
  await expect(page.locator("#diagnostic-mode")).toHaveText("SIMULATED");
  await page.route("**/api/coordinator/diagnostics", (route) => route.abort());
  await page.getByRole("button", { name: "Refresh checks" }).click();
  await expect(page.locator("#diagnostic-note")).toContainText("stale");
  await expect(page.locator(".diagnostic-status").first()).toContainText("STALE");
  await page.unroute("**/api/coordinator/diagnostics");
  await page.getByRole("button", { name: "Refresh checks" }).click();
  await expect(page.locator(".diagnostic-status").first()).toContainText("OK");
});


test("cancelling a microphone test releases Pi capture through the proxy", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Diagnostics", exact: true }).click();
  await page.getByRole("button", { name: "Test microphone" }).click();
  const recording = async () => {
    const report = await (await request.get("/api/coordinator/diagnostics")).json();
    return report.checks.find((check: { component: string }) => check.component === "microphone").measurements.recording;
  };
  await expect.poll(recording).toBe(true);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator("#diagnostic-audio-note")).toContainText("cancelled");
  await expect.poll(recording, { timeout: 2000 }).toBe(false);
});

test("shared camera reports failures, stale frames and recovery", async ({ page }) => {
  await page.goto("/");
  const status = page.locator("#camera-status");
  await expect(status).toContainText("Live");
  await expect.poll(async () => {
    const text = await status.textContent();
    return Number(text?.match(/([\d.]+) preview fps/)?.[1] ?? 0);
  }).toBeGreaterThan(24);
  await expect(page.locator("#camera-timing")).toContainText("SIMULATED timing");
  await page.screenshot({ path: "test-results/camera-timing.png", fullPage: true });
  await page.route("**/api/coordinator/camera/preview?*", route => route.abort());
  await expect(status).toContainText("Unavailable");
  await page.unroute("**/api/coordinator/camera/preview?*");
  await expect(status).toContainText("Live");
  await page.route("**/api/coordinator/camera/preview?*", async route => {
    await new Promise(resolve => setTimeout(resolve, 4000));
    await route.continue();
  });
  await expect(status).toContainText("Stale", { timeout: 10000 });
  await page.unroute("**/api/coordinator/camera/preview?*");
  await expect(status).toContainText("Live", { timeout: 10000 });
});

test("repeated frames become stale and delayed frames show their age", async ({ page }) => {
  let id = "first";
  await page.route("**/api/coordinator/camera/preview?*", route => route.fulfill({
    status: 200,
    contentType: "image/svg+xml",
    headers: {
      "X-Frame-Id": id,
      "X-Frame-Captured-At": "2026-10-24T12:00:00.123+00:00",
      "X-Frame-Age-Ms": "450",
      "X-Frame-Time-Source": "sensor",
    },
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="green"/></svg>',
  }));
  await page.goto("/");
  const timing = page.locator("#camera-timing");
  await expect(timing).toContainText("Frame 1");
  await expect(timing).toContainText("12:00:00.123 UTC");
  await expect(timing).toContainText("sensor→display ≤");
  await expect(timing).toHaveAttribute("data-state", "delayed");
  await expect(page.locator("#camera-status")).toContainText("Stale", { timeout: 5000 });
  await expect(timing).toContainText("Frame 1");
  await expect(timing).toHaveAttribute("data-state", "stale");
  id = "second";
  await expect(timing).toContainText("Frame 2");
  await expect(page.locator("#camera-status")).toContainText("Live");
});
