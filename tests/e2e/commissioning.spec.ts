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
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
  await expect(page.locator("#init-save")).toBeEnabled();
});

test.afterEach(async ({ request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
});

test("Initialization separates capsule measurements from optional pulse limits", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await expect(page.locator("#init-form input")).toHaveCount(18);
  await expect(page.locator("#init-form button")).toHaveCount(19);
  await expect(page.locator("#geometry-panel")).toBeVisible();
  await expect(page.locator("#geometry-save")).toBeEnabled();
  await expect(page.locator("#init-error")).toBeHidden();
  await expect(page.locator("#shared-recovery")).toHaveCount(0);
  await expect(page.locator("#commissioning-panel")).toContainText("SIMULATED");
  for (const id of joints) {
    await expect(field(page, id, "pulse")).toHaveValue("1500");
    await expect(field(page, id, "min")).toHaveValue("1000");
    await expect(field(page, id, "max")).toHaveValue("2000");
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator("#init-save").scrollIntoViewIfNeeded();
  await expect(page.locator("#init-save")).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: "test-results/initialization-direct-values-800.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator(".geometry-frame").screenshot({ path: "test-results/coordinate-frame-mobile.png", style: "header, .view-navigation { visibility: hidden !important; }" });
});

test("photo guide opens the matching part with keyboard and pointer without motion", async ({ page }) => {
  const motion: string[] = [];
  page.on("request", r => {
    if (r.method() === "POST" && /\/(initialize|position|resume|prepare)$/.test(r.url())) motion.push(r.url());
  });
  const guide = page.locator("#arm-guide");
  for (const [name, channel] of [["Claw", 0], ["Claw mount", 1], ["Wrist section", 2], ["Middle arm", 3], ["Upper arm", 4], ["Base", 5]] as const) {
    await expect(guide.getByRole("button", { name: `${name}, servo ${channel + 1}, channel ${channel}`, exact: true }))
      .toHaveText(`CH ${channel} · Servo ${channel + 1}${name}`);
  }
  await expect(guide.locator("img")).toBeVisible();
  expect(await guide.locator("img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  const middle = guide.getByRole("button", { name: "Middle arm, servo 4, channel 3", exact: true });
  await middle.focus();
  await page.keyboard.press("Enter");
  const card = page.locator('[data-model-channel="3"]');
  await expect(card).toHaveAttribute("open", "");
  await expect(card.locator(":scope > summary")).toBeFocused();
  await expect(card).toHaveClass(/arm-part-selected/);
  await expect(card.locator(":scope > summary")).toContainText("Middle arm · servo 4 · channel 3");
  await expect(card.getByRole("spinbutton", { name: "elbow_joint origin (inches) X", exact: true })).toBeVisible();
  await guide.getByRole("button", { name: "Claw, servo 1, channel 0", exact: true }).click();
  await expect(page.locator('[data-model-channel="0"]')).toHaveAttribute("open", "");
  await guide.getByRole("button", { name: "Base, servo 6, channel 5", exact: true }).click();
  await expect(page.locator('[data-model-channel="5"] > summary')).toBeFocused();
  expect(motion).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 900 });
  const screenshotStyle = "header, .view-navigation { visibility: hidden !important; }";
  await guide.screenshot({ path: "test-results/arm-guide-desktop.png", style: screenshotStyle });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await middle.click();
  await expect(card.locator(":scope > summary")).toBeInViewport({ ratio: 1 });
  const summaryTop = await card.locator(":scope > summary").evaluate(item => item.getBoundingClientRect().top);
  const navBottom = await page.locator(".view-navigation").evaluate(item => item.getBoundingClientRect().bottom);
  expect(summaryTop).toBeGreaterThan(navBottom);
  await guide.screenshot({ path: "test-results/arm-guide-mobile.png", style: screenshotStyle });
});

test("primary photo navigation preserves custom capsule drafts", async ({ page }) => {
  const upper = page.locator('[data-arm-part="link_4"]');
  await upper.locator(".capsule-mapping > summary").click();
  const name = upper.locator('input[id$="-name"]');
  await name.fill("Custom bracket");
  const before = await name.inputValue();
  await page.locator("#arm-guide").getByRole("button", { name: "Upper arm, servo 5, channel 4", exact: true }).click();
  await expect(page.locator('[data-model-channel="4"]')).toHaveAttribute("open", "");
  await expect(name).toHaveValue(before);
  await name.fill("link_4");
  await upper.getByRole("button", { name: "Remove part", exact: true }).click();
  const count = await page.locator("#geometry-capsules > details").count();
  await page.locator("#arm-guide").getByRole("button", { name: "Upper arm, servo 5, channel 4", exact: true }).click();
  await expect(page.locator('[data-model-channel="4"]')).toHaveAttribute("open", "");
  await expect(page.locator("#geometry-capsules > details")).toHaveCount(count);
  // Discard this unsaved draft, so later cases still use the saved scene.
  await page.locator("#geometry-reload").click();
});

test("arm legend remains usable when the photo cannot load", async ({ page }) => {
  await page.route("**/images/dfrobot-rob0036-dark.png", route => route.abort());
  await page.reload();
  await page.locator("#pulse-tab").click();
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
  await expect(page.locator("#geometry-save")).toBeEnabled();
  await page.locator(".arm-guide-legend").getByRole("button", { name: /Middle arm/ }).click();
  await expect(page.locator('[data-model-channel="3"]')).toHaveAttribute("open", "");
});

test("neutral axis buttons edit the draft without moving or rewriting angle samples", async ({ page, request }) => {
  const initial = await (await request.get("/api/coordinator/commissioning/geometry")).json();
  initial.geometry.joints[0].axis = [0, -1, 0];
  initial.geometry.joints[3].axis = [-1, 0, 0];
  const saves: typeof initial.geometry[] = [];
  const motion: string[] = [];
  page.on("request", r => {
    if (r.method() === "POST" && /\/(initialize|position|resume|prepare)$/.test(r.url())) motion.push(r.url());
  });
  await page.route("**/commissioning/geometry", async route => {
    if (route.request().method() === "POST") saves.push(route.request().postDataJSON().geometry);
    await route.fulfill({ json: { ...initial, geometry: saves.at(-1) ?? initial.geometry } });
  });
  await page.reload();
  await page.locator("#pulse-tab").click();
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
  await expect(page.locator("#geometry-save")).toBeEnabled();
  await page.locator("#geometry-joint-details > summary").click();
  await expect(page.locator("#geometry-joint-0-axis-1")).toHaveValue("-1");
  await expect(page.locator("#geometry-joint-3-axis-0")).toHaveValue("-1");
  const sampleValues = () => page.locator("#geometry-joint-0-samples input").evaluateAll(inputs => inputs.map(input => (input as HTMLInputElement).value));
  const samples = await sampleValues();
  await page.getByRole("button", { name: "Use neutral +Y axis for channel 4", exact: true }).click();
  await page.getByRole("button", { name: "Use neutral +X axis for channel 1", exact: true }).click();
  await expect(page.locator("#geometry-joint-0-axis-1")).toHaveValue("1");
  await expect(page.locator("#geometry-joint-3-axis-0")).toHaveValue("1");
  expect(await sampleValues()).toEqual(samples);
  expect(saves).toHaveLength(0);
  await page.locator("#geometry-save").click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0].joints[0].axis).toEqual([0, 1, 0]);
  expect(saves[0].joints[3].axis).toEqual([1, 0, 0]);
  expect(saves[0].joints[0].pulse_angle_deg).toEqual(initial.geometry.joints[0].pulse_angle_deg);
  expect(motion).toEqual([]);
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
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
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
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
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

test("capsule drafts save independently without sending movement", async ({ page, request }) => {
  const motion: string[] = [];
  page.on("request", req => {
    if (req.method() === "POST" && /\/(initialize|position|input)$/.test(req.url())) motion.push(req.url());
  });
  const before = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  await expect(page.locator("#geometry-save")).toBeEnabled();
  await page.locator("#capsule-0-a-0").fill("12");
  await page.locator("#capsule-0-a-1").fill("0");
  await page.locator("#capsule-0-a-2").fill("100");
  await page.locator("#capsule-0-radius").fill("14");
  await page.locator("#geometry-error-mm").fill("");
  await page.locator("#geometry-save").click();
  await expect(page.locator("#geometry-status")).not.toContainText("Unsaved");
  await expect(page.locator("#geometry-error")).toBeHidden();
  const saved = await (await request.get("/api/coordinator/commissioning/geometry")).json();
  expect(saved.geometry.capsules[0].a_mm).toEqual([12, 0, 100]);
  expect(saved.geometry.capsules[0].radius_mm).toBe(14);
  expect(saved.valid).toBe(false);
  expect(await (await request.get("/api/coordinator/commissioning/bounds")).json()).toEqual(before);
  expect(motion).toEqual([]);
  await page.reload();
  await page.locator("#pulse-tab").click();
  await page.locator("#legacy-geometry").evaluate(element => element.setAttribute("open", ""));
  await expect(page.locator("#capsule-0-radius")).toHaveValue("14");
  await page.locator("#geometry-panel").screenshot({ path: "test-results/capsule-measurements.png" });
  await page.locator("#geometry-activate").click();
  await expect(page.locator("#geometry-error")).toBeVisible();
  await expect(page.locator("#capsule-0-radius")).toHaveValue("14");
});

test("complete capsule geometry activates while Jog stays free play", async ({ page, request }) => {
  const state = await (await request.get("/api/coordinator/commissioning/geometry")).json();
  const origins = [[0, 0, 100], [100, 0, 100], [160, 0, 100], [200, 0, 100]];
  const geometry = {
    schema_version: 1, domain: "simulated", revision: "synthetic-browser-test", calibrated: false,
    reference_pulse_us: 1500, base_axis_perpendicular_to_table: true, claw_envelope: "full_opening_range",
    clearance_mm: 10, geometry_error_mm: 1, max_boxes: 4096,
    joints: [4, 3, 2, 1].map((channel, i) => ({ channel, origin_mm: origins[i], axis: [0, -1, 0],
      pulse_angle_deg: [[500, -90], [1500, 0], [2500, 90]], error_deg: 0.5 })),
    capsules: [4, 3, 2, 1].map((parent_channel, i) => ({ name: `link_${parent_channel}`, parent_channel,
      a_mm: origins[i], b_mm: origins[i + 1] ?? [220, 0, 100], radius_mm: 5 }))
      .concat([{ name: "claw", parent_channel: 1, a_mm: [220, 0, 100], b_mm: [240, 0, 100], radius_mm: 10 }]),
  };
  const seeded = await request.post("/api/coordinator/commissioning/geometry", {
    data: { context_key: state.context_key, revision: state.revision, geometry, activate: false },
  });
  expect(seeded.ok()).toBe(true);
  const nextRevision = (await seeded.json()).revision + 1;
  await page.locator("#geometry-reload").click();
  await expect(page.locator("#geometry-status")).toContainText("Measurements are valid");
  await page.locator("#geometry-activate").click();
  await expect(page.locator("#geometry-status")).toContainText(`Active geometry: revision ${nextRevision}.`);
  await expect(page.locator("#geometry-error")).toBeHidden();
  expect((await request.post("/api/coordinator/recover", { data: { acknowledged: true } })).ok()).toBe(true);
  await page.locator("#jog-tab").click();
  await expect(page.locator("#manual-clearance")).toContainText("table-clearance checks off");
  await expect(page.locator("#manual-enable")).toBeEnabled();
  await page.locator("#manual-panel").screenshot({ path: "test-results/manual-free-play.png" });
});

test("clear saved endpoints supports undo and persists without motion", async ({ page, request }) => {
  const motion: string[] = [];
  page.on("request", r => {
    if (r.method() === "POST" && /\/(initialize|position|resume|prepare|input)$/.test(r.url())) motion.push(r.url());
  });
  const clearMin = page.locator('[data-clear="base-min"]');
  await clearMin.click();
  await expect(field(page, "base", "min")).toHaveValue("");
  await expect(clearMin).toHaveText("Undo clear min");
  await clearMin.click();
  await expect(field(page, "base", "min")).toHaveValue("1000");
  await clearMin.click();
  await page.locator('[data-clear="claw-min"]').click();
  await page.locator('[data-clear="claw-max"]').click();
  await field(page, "joint_2", "min").fill("850");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  const saved = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  expect(saved.joints.base.minimum).toBeNull();
  expect(saved.joints.base.maximum.requested_us).toBe(2000);
  expect(saved.joints.claw).toEqual({ minimum: null, maximum: null });
  expect(saved.joints.joint_2.minimum.requested_us).toBe(850);
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#init-save")).toBeEnabled();
  await expect(field(page, "base", "min")).toHaveValue("");
  await expect(field(page, "base", "max")).toHaveValue("2000");
  await expect(field(page, "claw", "min")).toHaveValue("");
  await expect(field(page, "claw", "max")).toHaveValue("");
  expect(motion).toEqual([]);
});

test("failed endpoint deletion stays staged for retry", async ({ page }) => {
  await page.route("**/commissioning/bounds", async route => {
    if (route.request().method() === "POST") await route.fulfill({ status: 503, json: { message: "Storage unavailable" } });
    else await route.continue();
  });
  await page.locator('[data-clear="base-min"]').click();
  await page.locator("#init-save").click();
  await expect(page.locator("#init-error")).toContainText("Storage unavailable");
  await expect(field(page, "base", "min")).toHaveValue("");
  await expect(page.locator('[data-clear="base-min"]')).toHaveText("Undo clear min");
  await page.unroute("**/commissioning/bounds");
  await page.locator("#init-save").click();
  await expect(page.locator("#init-save")).toHaveText("Saved");
  await expect(field(page, "base", "min")).toHaveValue("");
});
