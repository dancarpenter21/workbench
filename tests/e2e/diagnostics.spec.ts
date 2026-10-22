import { test, expect } from "@playwright/test";

test("diagnostics show devices, play Pi test audio, and export a report", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Diagnostics", exact: true }).click();
  await expect(page.locator("#diagnostic-mode")).toHaveText("SIMULATED");
  await expect(page.getByRole("heading", { name: "Servo HAT", exact: true })).toBeVisible();
  await expect(page.locator("#diagnostic-camera")).toBeVisible();
  await expect.poll(() => page.locator("#diagnostic-camera").evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
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
  await expect(page.locator(".diagnostic-status").first()).toHaveText("STALE");
  await page.unroute("**/api/coordinator/diagnostics");
  await page.getByRole("button", { name: "Refresh checks" }).click();
  await expect(page.locator(".diagnostic-status").first()).toHaveText("OK");
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
