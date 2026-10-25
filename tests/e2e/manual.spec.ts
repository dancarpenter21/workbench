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
  await expect(page.locator("#manual-end")).toBeDisabled();
  await page.locator("#request").fill("Arrow keys and Space belong to this field");
  await page.keyboard.press("ArrowLeft");
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-enable")).toBeEnabled();
});

test("on-screen jog buttons stop on release and tab change ends ownership", async ({ page, request }) => {
  await page.locator("#manual-source").selectOption("pointer");
  await page.locator("#manual-enable").click();
  await expect(page.locator("#manual-end")).toBeEnabled();
  await page.waitForTimeout(150);
  const before = await page.locator("#manual-value").innerText();
  await page.locator("#manual-plus").hover();
  await page.mouse.down();
  await expect.poll(() => page.locator("#manual-value").innerText()).not.toBe(before);
  await page.mouse.up();
  await page.waitForTimeout(200);
  const held = await page.locator("#manual-value").innerText();
  await page.waitForTimeout(250);
  await expect(page.locator("#manual-value")).toHaveText(held);
  await page.locator("#diagnostics-tab").click();
  await expect(page.locator("#manual-panel")).toBeHidden();
  await expect.poll(async () => (await (await request.get("/api/coordinator/manual/status")).json()).active).toBe(false);
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-enable")).toBeEnabled();
  await expect(page.locator("#manual-end")).toBeDisabled();
});

test("Xbox dead zone, neutral selection, proportional movement, disconnect and stop", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.evaluate(() => {
    const pad = {
      index: 0, id: "Xbox test controller", connected: true, mapping: "standard",
      axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0, touched: false })),
    };
    (window as any).__manualPad = pad;
    Object.defineProperty(navigator, "getGamepads", { configurable: true, value: () => [pad.connected ? pad : null] });
  });
  const padInput = async (axis: number, bumper: boolean, down = false) => {
    await page.evaluate(({ axis, bumper, down }) => {
      const pad = (window as any).__manualPad;
      pad.axes[0] = axis;
      pad.buttons[4].pressed = bumper;
      pad.buttons[13].pressed = down;
    }, { axis, bumper, down });
  };
  await expect(page.locator("#manual-controller-note")).toContainText("Controller connected");
  await page.locator("#manual-source").selectOption("gamepad");
  await page.locator("#manual-enable").click();
  await expect(page.locator("#manual-end")).toBeEnabled();
  await page.waitForTimeout(150);
  const before = await page.locator("#manual-value").innerText();
  await padInput(0.1, true);
  await page.waitForTimeout(300);
  await expect(page.locator("#manual-value")).toHaveText(before);
  await padInput(0.7, true);
  await expect.poll(() => page.locator("#manual-value").innerText()).not.toBe(before);
  await padInput(0.7, true, true);
  await expect(page.locator("#manual-selected")).toHaveText("Joint 2 · Servo 4");
  await page.waitForTimeout(200);
  const neutral = await page.locator("#manual-value").innerText();
  await page.waitForTimeout(250);
  await expect(page.locator("#manual-value")).toHaveText(neutral); // Must release after selection.
  await padInput(0, false);
  await page.waitForTimeout(150);
  await padInput(0.8, true);
  await expect.poll(() => page.locator("#manual-value").innerText()).not.toBe(neutral);
  await page.locator("#manual-panel").screenshot({ path: "test-results/manual-xbox.png" });
  await page.evaluate(() => { (window as any).__manualPad.connected = false; });
  await expect(page.locator("#manual-end")).toBeDisabled();
  await page.evaluate(() => {
    const pad = (window as any).__manualPad;
    pad.connected = true; pad.axes[0] = 0; pad.buttons[4].pressed = false;
  });
  await expect(page.locator("#manual-enable")).toBeEnabled();
  await page.locator("#manual-enable").click();
  await expect(page.locator("#manual-end")).toBeEnabled();
  expect(errors).toEqual([]);
  await expect(page.locator("#manual-panel")).toBeFocused();
  await page.evaluate(() => { (window as any).__manualPad.buttons[1].pressed = true; });
  await expect(page.locator("#recovery")).toHaveCount(0);
  await expect(page.locator("#manual-end")).toBeDisabled();
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
