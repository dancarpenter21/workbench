import { test, expect } from "@playwright/test";

test("guided measurements preserve drafts and never command motion", async ({ page }) => {
  const motion: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && /\/(initialize|position|resume|prepare|input)$/.test(request.url())) motion.push(request.url());
  });
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#robot-model-status")).toContainText("Simulated model");
  const links = page.getByRole("navigation", { name: "Initialization steps" }).getByRole("link");
  await expect(links).toHaveCount(5);
  await page.locator("#robot-model-links > details").first().locator("summary").click();
  const width = page.getByRole("spinbutton", { name: "base_link box size (inches) X", exact: true });
  await width.fill("4.25");
  for (const link of await links.all()) {
    await link.click();
    await expect(link).toHaveAttribute("aria-current", "step");
    await expect(width).toHaveValue("4.25");
  }
  await page.locator('[data-model-channel="5"] > summary').click();
  await expect(page.getByRole("spinbutton", { name: "base_joint roll / pitch / yaw (degrees) Roll about X", exact: true })).toBeVisible();
  await page.getByText("Rotation mathematics and worked examples", { exact: true }).click();
  await expect(page.locator("#measurement-orientation")).toContainText("R = Rz(yaw) Ry(pitch) Rx(roll)");
  const image = page.locator(".measurement-art-pose img");
  await image.scrollIntoViewIfNeeded();
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  expect(motion).toEqual([]);
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator("#measurement-pose").screenshot({ path: `test-results/measurement-pose-${viewport.width}.png`, style: "header, .view-navigation { visibility: hidden !important; }" });
    await page.locator("#measurement-dimensions").screenshot({ path: `test-results/measurement-boxes-${viewport.width}.png`, style: "header, .view-navigation { visibility: hidden !important; }" });
    await page.locator("#init-form").screenshot({ path: `test-results/measurement-limits-${viewport.width}.png`, style: "header, .view-navigation { visibility: hidden !important; }" });
    await page.locator("#measurement-orientation .measurement-diagram").first().screenshot({ path: `test-results/measurement-pivots-${viewport.width}.png` });
  }
});

test("nominal pulse references handle base, other servos and invalid input without motion", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", request => { if (request.method() === "POST" && /\/commissioning\//.test(request.url())) posts.push(request.url()); });
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  for (const [joint, shaft, delta] of [["base", [0, 135, 270], [-135, 0, 135]], ["claw", [0, 90, 180], [-90, 0, 90]]] as const) {
    for (const [index, pulse] of [500, 1500, 2500].entries()) {
      await page.locator(`#init-${joint}-pulse`).fill(String(pulse));
      await expect(page.locator(`#init-${joint}-nominal`)).toContainText(`shaft ${shaft[index]}°`);
      await expect(page.locator(`#init-${joint}-nominal`)).toContainText(`midpoint ${delta[index] > 0 ? "+" : ""}${delta[index]}° (unverified)`);
    }
    for (const invalid of ["", "499", "2501"]) {
      await page.locator(`#init-${joint}-pulse`).fill(invalid);
      await expect(page.locator(`#init-${joint}-nominal`)).toContainText("Enter a pulse");
    }
  }
  expect(posts).toEqual([]);
});

test("sample rows save signed observations and unknowns separately from nominal references", async ({ page, request }) => {
  const initial = await (await request.get("/api/coordinator/commissioning/geometry")).json();
  initial.geometry.joints[0].pulse_angle_deg = [[1200, 24], [1500, 0], [1800, -25]];
  const saves: typeof initial.geometry[] = [];
  await page.route("**/commissioning/geometry", async route => {
    if (route.request().method() === "POST") saves.push(route.request().postDataJSON().geometry);
    await route.fulfill({ json: { ...initial, geometry: saves.at(-1) ?? initial.geometry } });
  });
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  await page.locator("#legacy-geometry > summary").click();
  await expect(page.locator("#geometry-save")).toBeEnabled();
  await page.locator("#geometry-joint-details > summary").click();
  const samples = page.locator("#geometry-joint-0-samples");
  await samples.locator("[data-sample-angle]").last().fill("-27.5");
  await page.getByRole("button", { name: "Add sample for channel 4", exact: true }).click();
  await expect(samples.locator(".angle-sample")).toHaveCount(4);
  await expect(samples.locator("[data-sample-angle]").last()).toHaveValue("");
  await page.locator("#geometry-save").click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0].joints[0].pulse_angle_deg).toEqual([[1200, 24], [1500, 0], [1800, -27.5], [null, null]]);
  await page.getByRole("button", { name: "Remove CH 4 sample 4", exact: true }).click();
  await page.locator("#geometry-save").click();
  await expect.poll(() => saves.length).toBe(2);
  expect(saves[1].joints[0].pulse_angle_deg).toEqual([[1200, 24], [1500, 0], [1800, -27.5]]);
});
