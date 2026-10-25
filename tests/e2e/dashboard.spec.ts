import { test, expect } from "@playwright/test";

test("retrieve, reset and stop without a shared recovery panel", async ({
  page,
  request,
}) => {
  const status = await request.get("/api/coordinator/status");
  expect((await status.json()).mode).toBe("mock");
  await request.post("/api/coordinator/recover", {
    data: { acknowledged: true },
  });
  await request.post("/api/coordinator/mock/reset");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#connection")).toContainText("Servo HAT");
  await expect(page.locator("#camera-note")).toContainText("Simulated scene");
  await page.locator("#request").fill("Grab me the pliers");
  await page.getByRole("button", { name: "Retrieve tool" }).click();
  await expect(page.locator("#operation-state")).toHaveText("COMPLETED", {
    timeout: 15_000,
  });
  await expect(page.locator("#operation-message")).toHaveText(
    "Tool delivered to the tray",
  );
  await page.getByRole("button", { name: "Reset mock scene" }).click();
  await page.locator("#stop").click();
  await expect(page.locator("#shared-recovery, #recovery, #recover, #ack")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Retrieve tool" }),
  ).toBeDisabled();
  await page.screenshot({ path: "test-results/workbench-stopped.png", fullPage: true });
  await expect(page.locator("#camera")).toBeVisible();
  await expect.poll(() => page.locator("#camera").evaluate((image: HTMLVideoElement) => image.videoWidth)).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});
