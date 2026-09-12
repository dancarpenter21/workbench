import { test, expect } from "@playwright/test";

test("retrieve, reset, stop and recover from the dashboard", async ({
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
  await expect(page.locator("#connection")).toHaveText("CONNECTED");
  await expect(page.locator("#mode")).toHaveText("SIMULATED");
  await page.getByRole("button", { name: "Pliers", exact: true }).click();
  await page.getByRole("button", { name: "Retrieve tool" }).click();
  await expect(page.locator("#operation-state")).toHaveText("COMPLETED", {
    timeout: 15_000,
  });
  await expect(page.locator("#operation-message")).toHaveText(
    "Tool delivered to the tray",
  );
  await page.getByRole("button", { name: "Reset mock scene" }).click();
  await page.getByRole("button", { name: "Stop arm" }).click();
  await expect(page.locator("#recovery")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retrieve tool" }),
  ).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Recover", exact: true }).click();
  await expect(page.locator("#recovery")).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Retrieve tool" }),
  ).toBeEnabled();
  await expect(page.locator("#camera")).toBeVisible();
  expect(
    await page
      .locator("#camera")
      .evaluate((image: HTMLImageElement) => image.naturalWidth),
  ).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});
