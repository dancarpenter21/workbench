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
    const width = page.getByRole("spinbutton", { name: "base_link box size (m) X", exact: true });
    await width.fill("0.123");
    await page.getByRole("button", { name: "Save model draft", exact: true }).click();
    await expect(page.locator("#robot-model-status")).toContainText("incomplete");
    await page.reload();
    await page.locator("#pulse-tab").click();
    await expect(page.locator("#robot-model-status")).toContainText("Simulated model");
    await page.locator("#robot-model-links > details").first().locator("summary").click();
    await expect(width).toHaveValue("0.123");
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
