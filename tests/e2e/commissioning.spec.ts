import { test, expect } from "@playwright/test";

test("Config separates stored settings from Diagnostics and Manual", async ({ page }) => {
  await page.goto("/");
  await page.locator("#config-tab").click();
  await expect(page.locator("#config-view")).toBeVisible();
  await expect(page.locator("#init-save")).toBeEnabled();
  await expect(page.locator("#init-form input")).toHaveCount(12);
  await expect(page.locator("#harness-endpoint")).toBeVisible();
  await expect(page.locator("#harness-open")).toBeVisible();
  await expect(page.locator("#robot-model-panel, #geometry-panel, #scale-resistance")).toHaveCount(0);
  await page.screenshot({ path: "test-results/dfrobot-config.png", fullPage: true });
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-pulses input").first()).toBeVisible();
  await expect(page.locator("#manual-pulses button").first()).toBeVisible();
  await expect(page.locator("#init-save")).toBeHidden();
  await page.locator("#diagnostics-tab").click();
  await expect(page.locator("#diagnostic-live-current")).toBeVisible();
  await expect(page.locator('#diagnostics-view input[type="number"], #diagnostics-view textarea')).toHaveCount(0);
});

test("saving harness limits retains values without commanding outputs", async ({ page, request }) => {
  await page.goto("/");
  await page.locator("#config-tab").click();
  await expect(page.locator("#init-save")).toBeEnabled();
  const before = await (await request.get("/api/coordinator/commissioning/status")).json();
  await page.locator("#init-base-min").fill("1100");
  await page.locator("#init-base-max").fill("1900");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  const after = await (await request.get("/api/coordinator/commissioning/status")).json();
  expect(after.reserved).toBe(before.reserved);
  expect(after.trial_id).toBe(before.trial_id);
  await page.reload();
  await page.locator("#config-tab").click();
  await expect(page.locator("#init-base-min")).toHaveValue("1100");
  await expect(page.locator("#init-base-max")).toHaveValue("1900");
});
