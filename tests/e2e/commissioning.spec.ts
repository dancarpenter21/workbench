import { test, expect, type Page } from "@playwright/test";

async function move(page: Page, target: number) {
  await page.locator("#trial-target").fill(String(target));
  await page.locator("#trial-apply").click();
  await expect(page.locator("#trial-feedback")).toBeVisible();
}

async function finish(page: Page) {
  await page.locator("#trial-stop").click();
  await page.locator("#trial-finish-ack").check();
  await page.locator("#trial-finish").click();
  await expect(page.locator("#trial-apply")).toBeEnabled();
}

const joint = (page: Page, id: string) => page.locator(`#trial-joints [data-joint="${id}"]`);
const step = (page: Page, delta: number) => page.locator(`#trial-steps [data-delta="${delta}"]`);

test.beforeEach(async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#trial-apply")).toBeEnabled();
});

test.afterEach(async ({ request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
  if (status.simulated) {
    await request.post("/api/coordinator/commissioning/reset-current", {
      data: { power_off_and_supported: true },
    });
    const prepared = await request.post("/api/coordinator/manual/prepare", {
      data: { power_disconnected_and_arm_supported: true },
    });
    expect(prepared.ok()).toBe(true);
  }
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
});

test("capture bounds without operator forms and retain them across reload and new trials", async ({ page, request }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await expect(page.locator("#trial-settings, #trial-assembly, #trial-observer, #trial-history, #trial-export, #trial-scale")).toHaveCount(0);
  await expect(page.locator("#trial-joints button")).toHaveCount(6);
  await expect(page.locator("#trial-minimum")).toBeDisabled();
  await step(page, -40).click();
  await expect(page.locator("#trial-feedback")).toBeVisible();
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await expect(page.locator("#submit")).toBeDisabled();
  await expect(page.locator("#trial-apply")).toBeDisabled();
  await expect(page.locator("#trial-minimum")).toBeDisabled();
  await expect(page.locator("#trial-settled")).toBeInViewport({ ratio: 1 });
  await expect(page.locator("#trial-unsafe")).toBeInViewport({ ratio: 1 });
  await expect(page.locator("#trial-stop")).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: "test-results/pulse-feedback-800.png" });
  await page.locator("#trial-settled").click();
  const before = await (await request.get("/api/coordinator/commissioning/status")).json();
  await page.locator("#trial-minimum").click();
  await expect(joint(page, "base")).toContainText("Min: 1460 μs");
  const after = await (await request.get("/api/coordinator/commissioning/status")).json();
  expect(after.targets).toEqual(before.targets);
  await step(page, 40).click();
  await page.locator("#trial-settled").click();
  await page.locator("#trial-maximum").click();
  await expect(joint(page, "base")).toContainText("Max: 1500 μs");
  await expect(page.locator("#trial-minimum")).toBeDisabled(); // Minimum cannot equal maximum.
  await page.setViewportSize({ width: 1100, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "test-results/pulse-bounds.png", fullPage: true });
  await page.setViewportSize({ width: 800, height: 480 });
  await finish(page);
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(joint(page, "base")).toContainText("Min: 1460 μs");
  await expect(joint(page, "base")).toContainText("Max: 1500 μs");
  await move(page, 1470);
  await page.locator("#trial-settled").click();
  await page.locator("#trial-minimum").click();
  await expect(joint(page, "base")).toContainText("Min: 1470 μs");
});

test("extend the test range explicitly without moving or changing saved bounds", async ({ page, request }) => {
  await step(page, 40).click();
  await page.locator("#trial-settled").click();
  await expect(step(page, 20)).toBeDisabled();
  await expect(page.locator("#trial-extend-high")).toBeVisible();
  await expect(page.locator("#trial-extend-high")).toBeDisabled();
  const saved = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  await page.locator("#trial-window-ack").check();
  await page.locator("#trial-extend-high").click();
  await expect(page.locator("#trial-window-range")).toContainText("1450–1600 μs");
  await expect(page.locator("#trial-window-ack")).not.toBeChecked();
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  expect(status.requested_us).toBe(1540);
  expect(await (await request.get("/api/coordinator/commissioning/bounds")).json()).toEqual(saved);
  await step(page, 20).click();
  await expect(page.locator("#trial-pulse")).toContainText("1560 μs");
  await page.locator("#trial-settled").click();
  await finish(page);
  await move(page, 1510);
  await page.locator("#trial-settled").click();
  await expect(page.locator("#trial-window-range")).toContainText("1450–1550 μs");
});

test("leaving the tab requires power-off finish before another test", async ({ page, request }) => {
  await move(page, 1490);
  await page.locator("#trial-settled").click();
  await page.locator("#diagnostics-tab").click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).stage).toBe("awaiting_power_off");
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#trial-finish-panel")).toBeVisible();
  await expect(page.locator("#trial-motion")).toBeHidden();
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await page.locator("#trial-finish-ack").check();
  await page.locator("#trial-finish").click();
  await expect(page.locator("#trial-apply")).toBeEnabled();
});

test("all-holding mode keeps other joints fixed and supports every step button", async ({ page, request }) => {
  await move(page, 1490);
  await page.locator("#trial-settled").click();
  await joint(page, "claw").click();
  await expect(page.locator("#trial-target")).toHaveValue("1500");
  await expect(page.locator("#trial-minimum")).toBeDisabled();
  for (const delta of [1, -1, 10, -10, 20, -20, 40, -40]) {
    const before = await (await request.get("/api/coordinator/commissioning/status")).json();
    await step(page, delta).click();
    await expect(page.locator("#trial-feedback")).toBeVisible();
    const after = await (await request.get("/api/coordinator/commissioning/status")).json();
    expect(after.targets).toEqual({ ...before.targets, claw: before.targets.claw + delta });
    await page.locator("#trial-settled").click();
  }
});

test("Not OK stops output and requires power-off finish", async ({ page, request }) => {
  await move(page, 1510);
  await page.locator("#trial-unsafe").click();
  await expect(page.locator("#trial-message")).toContainText("cut servo power");
  await expect(page.locator("#trial-finish-panel")).toBeVisible();
  await expect(page.locator("#trial-motion")).toBeHidden();
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  expect(status.targets).toEqual({});
  expect(status.claw_current.healthy).toBe(false);
});

test("invalid target disables Move without losing the test session", async ({ page }) => {
  for (const value of ["1800", "1541", "1449"]) {
    await page.locator("#trial-target").fill(value);
    await expect(page.locator("#trial-apply")).toBeDisabled();
  }
  await move(page, 1510);
  await page.locator("#trial-settled").click();
});

test("current-scale verification is available in Diagnostics", async ({ page }) => {
  await page.locator("#diagnostics-tab").click();
  await page.locator("#diagnostic-current-scale > details > summary").click();
  await expect(page.locator("#scale-resistance")).toBeVisible();
  await expect(page.locator("#scale-attest")).toBeVisible();
  await expect(page.locator("#scale-save")).toBeDisabled();
});
