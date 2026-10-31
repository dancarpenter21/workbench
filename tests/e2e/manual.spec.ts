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
  // Allow the neutral interlock to sample released keys before requesting motion.
  await page.waitForResponse(response => response.url().endsWith("/manual/input") &&
    response.request().postDataJSON()?.sequence >= 1 &&
    response.request().postDataJSON()?.speed === 0 && response.ok());
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
  await expect.poll(async () => Math.abs(parseFloat(await page.locator("#manual-value").innerText()) - 1700)).toBeLessThan(4.88);
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
  // Idle sessions intentionally survive timer throttling. Lose the connection
  // while jogging to exercise the movement watchdog and explicit recovery.
  await page.waitForResponse(response => response.url().endsWith("/manual/input") &&
    response.request().postDataJSON()?.sequence >= 1 && response.ok());
  await page.keyboard.down("Space");
  await page.keyboard.down("ArrowRight");
  await page.waitForResponse(response => response.url().endsWith("/manual/input") &&
    response.request().postDataJSON()?.speed === 1 && response.ok());
  await page.route("**/api/coordinator/manual/**", (route) => route.abort("failed"));
  await page.keyboard.up("ArrowRight");
  await page.keyboard.up("Space");
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

test("Manual prevents invalid pulses using each servo's assembly bounds", async ({ page }) => {
  const fields = page.locator("#manual-pulses input");
  const moves = page.locator("#manual-pulses button");
  const bounds = [[500, 2500], [600, 2100], [775, 2500], [500, 2500], [500, 2500], [850, 1600]];
  const commands: string[] = [];
  page.on("request", request => {
    if (/\/manual\/(session|position)$/.test(request.url()) && request.method() === "POST") commands.push(request.url());
  });
  for (const [index, [minimum, maximum]] of bounds.entries()) {
    const field = fields.nth(index);
    await expect(field).toHaveAttribute("min", String(minimum));
    await expect(field).toHaveAttribute("max", String(maximum));
    for (const invalid of ["", String(minimum - 1), String(maximum + 1), "1500.5"]) {
      await field.fill(invalid);
      await expect(field).toHaveAttribute("aria-invalid", "true");
      await expect(moves.nth(index)).toBeDisabled();
      await field.press("Enter");
    }
    for (const boundary of [minimum, maximum]) {
      await field.fill(String(boundary));
      await expect(field).toHaveAttribute("aria-invalid", "false");
      await expect(moves.nth(index)).toBeEnabled();
    }
    await field.fill("1500");
  }
  expect(commands).toEqual([]);
  await fields.last().fill("1601");
  await page.locator("#manual-panel").screenshot({ path: "test-results/manual-invalid-pulse.png" });
});
