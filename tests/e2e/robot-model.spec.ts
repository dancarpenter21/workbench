import { test, expect } from "@playwright/test";

test("ROS model drafts persist without movement and keep unknown measurements explicit", async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  const original = await (await request.get("/api/coordinator/commissioning/robot-model")).json();
  const bounds = await (await request.get("/api/coordinator/commissioning/bounds")).json();
  const motion: string[] = [];
  page.on("request", value => {
    if (value.method() === "POST" && /\/(initialize|position|resume|prepare|input)$/.test(value.url())) motion.push(value.url());
  });
  try {
    await page.goto("/");
    await page.locator("#pulse-tab").click();
    await expect(page.locator("#robot-model-status")).toContainText("Simulated model");
    await expect(page.locator("#legacy-geometry")).not.toHaveAttribute("open", "");
    const card = page.locator("#robot-model-links > details").first();
    await card.locator("summary").click();
    const width = page.getByRole("spinbutton", { name: "base_link box size (inches) X", exact: true });
    await width.fill("5");
    const boxPitch = page.getByRole("spinbutton", { name: "base_link box rotation (degrees) Pitch about Y", exact: true });
    await boxPitch.fill("-30");
    const joint = original.model.joints.find((value: { type: string }) => value.type !== "fixed");
    const jointCard = page.locator("#robot-model-joints > details").filter({ has: page.locator("summary", { hasText: joint.name }) });
    await jointCard.locator("summary").click();
    const angle = page.getByRole("spinbutton", { name: `${joint.name} lower limit (degrees)`, exact: true });
    await angle.fill("-90");
    await page.getByRole("spinbutton", { name: `${joint.name} velocity limit (degrees/s)`, exact: true }).fill("180");
    await page.getByRole("spinbutton", { name: `${joint.name} origin (inches) X`, exact: true }).fill("-2");
    await page.getByRole("spinbutton", { name: `${joint.name} roll / pitch / yaw (degrees) Yaw about Z`, exact: true }).fill("45");
    await page.getByRole("button", { name: "Save model draft", exact: true }).click();
    await expect(page.locator("#robot-model-status")).toContainText("incomplete");
    const saved = await (await request.get("/api/coordinator/commissioning/robot-model")).json();
    expect(saved.model.links[0].size[0]).toBeCloseTo(0.127, 12);
    expect(saved.model.links[0].rpy[1]).toBeCloseTo(-Math.PI / 6, 12);
    expect(saved.model.links[0].xyz).toEqual(original.model.links[0].xyz);
    const savedJoint = saved.model.joints.find((value: { name: string }) => value.name === joint.name);
    expect(savedJoint.lower).toBeCloseTo(-Math.PI / 2, 12);
    expect(savedJoint.velocity).toBeCloseTo(Math.PI, 12);
    expect(savedJoint.xyz[0]).toBeCloseTo(-0.0508, 12);
    expect(savedJoint.rpy[2]).toBeCloseTo(Math.PI / 4, 12);
    expect(savedJoint.axis).toEqual(joint.axis);
    expect(savedJoint.effort).toEqual(joint.effort);
    await page.reload();
    await page.locator("#pulse-tab").click();
    await expect(page.locator("#robot-model-status")).toContainText("Simulated model");
    await page.locator("#robot-model-links > details").first().locator("summary").click();
    await expect(width).toHaveValue("5");
    await expect(boxPitch).toHaveValue(String(-Math.PI / 6 / (Math.PI / 180)));
    await jointCard.locator("summary").click();
    await expect(angle).toHaveValue("-90");
    await width.fill("");
    await page.getByRole("button", { name: "Validate complete model", exact: true }).click();
    await expect(page.locator("#robot-model-status")).toContainText("measured finite number");
    expect(motion).toEqual([]);
    expect(await (await request.get("/api/coordinator/commissioning/bounds")).json()).toEqual(bounds);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator("#robot-model-panel").screenshot({ path: "test-results/ros-robot-model-mobile.png", style: "header, .view-navigation { visibility: hidden !important; }" });
  } finally {
    const latest = await (await request.get("/api/coordinator/commissioning/robot-model")).json();
    await request.post("/api/coordinator/commissioning/robot-model", { data: {
      context_key: latest.context_key, revision: latest.revision, model: original.model,
    } });
  }
});
