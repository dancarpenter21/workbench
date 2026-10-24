import { test, expect, type Page } from "@playwright/test";

const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];
const field = (page: Page, id: string, kind: string) => page.locator(`#init-${id}-${kind}`);

test.beforeEach(async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
  await request.post("/api/coordinator/commissioning/reset-current", { data: { fault_reviewed: true } });
  const bounds = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  const saved = await request.post("/api/coordinator/commissioning/bounds", { data: {
    assembly_id: bounds.assembly_id, setup: bounds.setup,
    joints: Object.fromEntries(joints.map(id => [id, { minimum_us: 1000, maximum_us: 2000 }])),
  } });
  expect(saved.ok()).toBe(true);
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#init-save")).toBeEnabled();
});

test.afterEach(async ({ request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
});

test("Initialization contains only six editable rows, Move, and Save limits", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await expect(page.locator("#commissioning-panel input")).toHaveCount(18);
  await expect(page.locator("#commissioning-panel button")).toHaveCount(7);
  await expect(page.locator("#commissioning-panel details, #commissioning-panel input[type=checkbox]")).toHaveCount(0);
  await expect(page.locator("#init-error")).toBeHidden();
  await expect(page.locator("#shared-recovery")).toBeHidden();
  await expect(page.locator("#commissioning-panel")).toContainText("SIMULATED");
  for (const id of joints) {
    await expect(field(page, id, "pulse")).toHaveValue("1500");
    await expect(field(page, id, "min")).toHaveValue("1000");
    await expect(field(page, id, "max")).toHaveValue("2000");
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator("#init-save")).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: "test-results/initialization-direct-values-800.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("limits save directly, survive reload, and never send movement requests", async ({ page, request }) => {
  const motion: string[] = [];
  page.on("request", r => {
    if (r.method() === "POST" && /\/(initialize|position|resume|prepare)$/.test(r.url())) motion.push(r.url());
  });
  await field(page, "base", "min").fill("1100");
  await field(page, "claw", "max").fill("1850");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(field(page, "base", "min")).toHaveValue("1100");
  await expect(field(page, "claw", "max")).toHaveValue("1850");
  expect(motion).toEqual([]);
  const saved = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  expect(saved.joints.base.minimum.source).toBe("manual");
});

test("Move sets a direct pulse beyond the old step window and leaves other joints alone", async ({ page, request }) => {
  await field(page, "base", "pulse").fill("1700");
  await page.locator('[data-move="base"]').click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).targets.base).toBe(1700);
  await field(page, "claw", "pulse").fill("1300");
  await page.locator('[data-move="claw"]').click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).targets.claw).toBe(1300);
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  expect(status.targets).toEqual({ base: 1700, joint_2: 1500, joint_3: 1500, joint_4: 1500, joint_5: 1500, claw: 1300 });
  await expect(page.locator("#init-error")).toBeHidden();
});

test("Move works with blank min/max and outside saved Jog limits", async ({ page, request }) => {
  for (const id of joints) {
    await field(page, id, "min").fill("");
    await field(page, id, "max").fill("");
  }
  await field(page, "base", "pulse").fill("2100");
  await page.locator('[data-move="base"]').click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).targets.base).toBe(2100);
  await expect(page.locator("#init-error")).toBeHidden();
  await expect(field(page, "base", "min")).toHaveValue("");
  const saved = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  expect(saved.joints.base.maximum.requested_us).toBe(2000);
});

test("a failed save keeps entered values and displays only the action error", async ({ page }) => {
  await page.route("**/api/coordinator/commissioning/bounds", route => route.request().method() === "POST"
    ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ message: "Storage unavailable" }) })
    : route.continue());
  await field(page, "base", "min").fill("1120");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-error")).toHaveText("Storage unavailable");
  await expect(field(page, "base", "min")).toHaveValue("1120");
  await expect(page.locator("#init-save")).toBeEnabled();
});

test("interrupted-session recovery lives in Diagnostics and sends no movement", async ({ page }) => {
  const calls: string[] = [];
  await page.route("**/api/coordinator/commissioning/status", route => route.fulfill({ json: {
    reserved: true, stage: "awaiting_power_off", trial_id: "stopped-trial",
  } }));
  for (const path of ["finish", "reset-current"]) await page.route(`**/api/coordinator/commissioning/${path}`, route => {
    calls.push(path);
    return route.fulfill({ json: { reserved: false, targets: {} } });
  });
  await page.locator("#diagnostics-tab").click();
  await expect(page.locator("#diagnostic-recovery")).toBeDisabled();
  await page.locator("#diagnostic-recovery-ack").check();
  await page.locator("#diagnostic-recovery").click();
  await expect(page.locator("#diagnostic-recovery-result")).toContainText("Interruption cleared");
  expect(calls).toEqual(["finish", "reset-current"]);
});

test("Move reinitializes an interrupted session without requiring a separate recovery workflow", async ({ page, request }) => {
  // Seed the state returned after a service restart, while letting the actual
  // initialize endpoint create a real monitored simulated session.
  await page.route("**/api/coordinator/commissioning/status", async route => {
    const response = await route.fetch();
    const status = await response.json();
    await route.fulfill({ response, json: { ...status, reserved: true,
      can_resume_powered: false, stage: "awaiting_power_off", trial_id: "old-session" } });
  });
  await field(page, "base", "pulse").fill("1800");
  await page.locator('[data-move="base"]').click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).targets.base).toBe(1800);
  await expect(page.locator("#init-error")).toBeHidden();
});

test("Save accepts only base limits and preserves omitted joints", async ({ page, request }) => {
  for (const id of joints) {
    await field(page, id, "min").fill("");
    await field(page, id, "max").fill("");
  }
  await field(page, "base", "min").fill("750");
  await field(page, "base", "max").fill("2200");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  const saved = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  expect(saved.joints.base.minimum.requested_us).toBe(750);
  expect(saved.joints.base.maximum.requested_us).toBe(2200);
  expect(saved.joints.claw.minimum.requested_us).toBe(1000);
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(field(page, "base", "min")).toHaveValue("750");
  await expect(field(page, "base", "max")).toHaveValue("2200");
  for (const id of joints) {
    await field(page, id, "min").fill("");
    await field(page, id, "max").fill("");
  }
  await field(page, "joint_2", "min").fill("850");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  await expect(field(page, "joint_2", "min")).toHaveValue("850");
  await expect(field(page, "joint_2", "max")).toHaveValue("2000");
});
