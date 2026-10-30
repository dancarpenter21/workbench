import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
  await page.goto("/");
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-enable")).toBeEnabled();
});

test.afterEach(async ({ request }) => {
  await request.post("/api/coordinator/stop");
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
});

test("keyboard jogging, joint mapping, precision, release and focus loss", async ({ page }) => {
  const value = () => page.locator("#manual-value").innerText();
  await expect(page.locator("#manual-selected")).toHaveText("Base · Servo 5");
  await page.locator("#manual-panel").screenshot({ path: "test-results/manual-ready.png" });
  await page.locator("#manual-enable").click();
  await expect(page.locator("#manual-end")).toBeEnabled();
  await expect(page.locator("#submit")).toBeDisabled();
  const initial = await value();
  await page.keyboard.down("ArrowRight");
  await page.waitForTimeout(300);
  expect(await value()).toBe(initial); // No Space enable.
  await page.keyboard.down("Space");
  await expect.poll(value).not.toBe(initial);
  await page.waitForTimeout(400);
  await page.keyboard.up("ArrowRight");
  await page.keyboard.up("Space");
  await page.waitForTimeout(200);
  const held = await value();
  await page.waitForTimeout(300);
  expect(await value()).toBe(held);
  await page.keyboard.press("6");
  await expect(page.locator("#manual-selected")).toHaveText("Claw · Servo 0");
  // Joint selection requires a sampled neutral input before re-enabling movement.
  await page.waitForResponse(response => response.url().endsWith("/manual/input") &&
    response.request().postDataJSON()?.joint_id === "claw" &&
    response.request().postDataJSON()?.speed === 0 && response.ok());
  const claw = await value();
  await page.keyboard.down("Shift");
  await page.keyboard.down("Space");
  await page.keyboard.down("ArrowRight");
  await expect.poll(value).not.toBe(claw);
  await page.waitForTimeout(250);
  await page.keyboard.up("ArrowRight");
  await page.keyboard.up("Space");
  await page.keyboard.up("Shift");
  await page.waitForTimeout(150);
  const delta = parseFloat(await value()) - parseFloat(claw);
  expect(delta).toBeGreaterThan(0);
  expect(delta).toBeLessThan(35);
  await page.screenshot({ path: "test-results/manual-keyboard.png", fullPage: true });
  await page.locator("#workbench-tab").click();
  await page.locator("#request").focus();
  await expect(page.locator("#manual-end")).toBeEnabled();
  await page.locator("#request").fill("Arrow keys and Space belong to this field");
  await page.keyboard.press("ArrowLeft");
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-end")).toBeEnabled();
});

test("numeric moves and neutral reset with keyboard-only jogging", async ({ page }) => {
  await expect(page.locator("#jog-tab")).toContainText("Manual");
  await expect(page.locator("#manual-controller, #manual-source, #manual-plus")).toHaveCount(0);
  const fields = page.locator("#manual-pulses input");
  await expect(fields).toHaveCount(6);
  for (let i = 0; i < 6; i++) await expect(fields.nth(i)).toHaveValue("1500");
  await expect(page.locator("#manual-reset")).toBeEnabled();
  await expect(page.locator("#manual-ready")).toHaveCount(0);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.waitForTimeout(500);
  await expect(page.locator("#manual-pulses button").first()).toBeEnabled();
  await fields.first().fill("1800");
  await page.locator("#manual-pulses button").first().click();
  await expect(page.locator("#manual-end")).toBeEnabled();
  await expect.poll(() => page.locator("#manual-value").innerText()).toMatch(/^180/);
  await fields.first().fill("1700");
  await fields.first().press("Enter");
  await expect.poll(() => page.locator("#manual-value").innerText()).toMatch(/^170/);
  await page.locator("#manual-reset").click();
  for (let i = 0; i < 6; i++) await expect(fields.nth(i)).toHaveValue("1500");
  await expect.poll(() => page.locator("#manual-value").innerText()).toMatch(/^149/);
  await page.locator("#workbench-tab").click();
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-reset")).toBeEnabled();
});

test("lost manual connection expires ownership and requires explicit recovery", async ({ page, request }) => {
  await page.locator("#manual-enable").click();
  await expect(page.locator("#manual-message")).toContainText("Manual control enabled");
  await page.route("**/api/coordinator/manual/**", (route) => route.abort("failed"));
  await expect(page.locator("#manual-end")).toBeDisabled();
  await expect(page.locator("#recovery")).toHaveCount(0);
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await expect(page.locator("#manual-message")).toContainText("recover");
  await page.locator("#manual-panel").screenshot({ path: "test-results/manual-unavailable.png" });
  await page.unroute("**/api/coordinator/manual/**");
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
  await expect(page.locator("#manual-enable")).toBeEnabled();
  await expect(page.locator("#manual-end")).toBeDisabled();
});

test("Manual recovers through preparation without visiting Initialization", async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  await expect(page.locator("#manual-preparation")).toBeVisible();
  await page.locator("#manual-power-off").check();
  await page.locator("#manual-prepare").click();
  await expect(page.locator("#manual-enable")).toBeEnabled();
  await expect(page.locator("#manual-error")).toBeEmpty();
  await page.locator("#manual-panel").screenshot({ path: "test-results/jog-recovered.png" });
});
