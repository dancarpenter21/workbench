import { test, expect, type Page } from "@playwright/test";

async function move(page: Page, target: number) {
  await page.locator("#trial-target").fill(String(target));
  await page.locator("#trial-apply").click();
  await expect(page.locator("#trial-feedback")).toBeVisible();
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
  await page.goto("/");
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#trial-apply")).toBeEnabled();
});

test.afterEach(async ({ request }) => {
  await request.post("/api/coordinator/stop");
  const status = await (await request.get("/api/coordinator/commissioning/status")).json();
  if (status.reserved) await request.post("/api/coordinator/commissioning/finish", {
    data: { trial_id: status.trial_id, power_off_and_supported: true },
  });
  if (status.simulated) {
    await request.post("/api/coordinator/commissioning/reset-current", {
      data: { power_off_and_supported: true },
    });
    // Unsafe feedback also invalidates manual preparation; explicitly reset the mock fixture.
    const prepared = await request.post("/api/coordinator/manual/prepare", {
      data: { power_disconnected_and_arm_supported: true },
    });
    expect(prepared.ok()).toBe(true);
  }
  await request.post("/api/coordinator/recover", { data: { acknowledged: true } });
});

test("position and OK need no record labels or direction form, and save repeatable history", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await expect(page.locator("#trial-settings")).not.toHaveAttribute("open");
  await expect(page.locator("#trial-acquire")).toHaveCount(0);
  await expect(page.locator("#trial-prepare")).toHaveCount(0);
  await expect(page.locator("#trial-assembly")).toBeHidden();
  await move(page, 1490);
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await expect(page.locator("#submit")).toBeDisabled();
  await expect(page.locator("#trial-apply")).toBeDisabled();
  await expect(page.locator("#trial-settled")).toHaveText("OK");
  await expect(page.locator("#trial-unsafe")).toHaveText("Not OK");
  await expect(page.locator("#trial-settled")).toBeInViewport({ ratio: 1 });
  await expect(page.locator("#trial-unsafe")).toBeInViewport({ ratio: 1 });
  await page.locator("#commissioning-panel").screenshot({ path: "test-results/commissioning-simple.png" });
  await page.locator("#trial-settled").click();
  await expect(page.locator("#trial-message")).toHaveText("OK — saved.");
  await move(page, 1500);
  await page.locator("#trial-settled").click();
  await page.locator("#trial-stop").click();
  await page.locator("#trial-finish-ack").check();
  await page.locator("#trial-finish").click();
  await page.locator("#trial-settings > summary").click();
  await expect(page.locator("#trial-history")).toContainText("1490 μs — OK");
  const id = await page.locator("#trial-drafts").inputValue();
  await page.reload();
  await page.locator("#pulse-tab").click();
  await page.locator("#trial-settings > summary").click();
  await page.locator("#trial-drafts").selectOption(id);
  await expect(page.locator("#trial-history")).toContainText("1490 μs — OK");
  const download = page.waitForEvent("download");
  await page.locator("#trial-export").click();
  const stream = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const bundle = JSON.parse(Buffer.concat(chunks).toString());
  expect(bundle.record.joints[0].attempts[0].feedback).toHaveLength(2);
  expect(bundle.record.joints[0].attempts[0].feedback[0].observed_direction).toBe("unknown");
  expect(bundle.manual_config.enabled).toBe(false);
});

test("leaving the tab requires power-off finish before another test", async ({ page, request }) => {
  await move(page, 1490);
  await page.locator("#trial-settled").click();
  await page.locator("#diagnostics-tab").click();
  await expect.poll(async () => (await (await request.get("/api/coordinator/commissioning/status")).json()).stage).toBe("awaiting_power_off");
  await page.reload();
  await page.locator("#pulse-tab").click();
  await expect(page.locator("#trial-finish-panel")).toBeVisible();
  await expect(page.locator("#trial-motion")).toBeHidden();
  await expect(page.locator("#manual-enable")).toBeDisabled();
  await page.locator("#trial-finish-ack").check();
  await page.locator("#trial-finish").click();
  await expect(page.locator("#trial-apply")).toBeEnabled();
});

test("all-holding mode keeps other joints fixed and accepts simple feedback", async ({ page }) => {
  await move(page, 1490);
  await page.locator("#trial-settled").click();
  await page.locator('[data-joint="claw"]').click();
  await expect(page.locator("#trial-target")).toHaveValue("1500");
  await move(page, 1490);
  await page.locator("#trial-settled").click();
  await page.locator("#trial-settings > summary").click();
  await expect(page.locator("#trial-targets")).toContainText("Joint 2: 1500");
  await expect(page.locator("#trial-targets")).toContainText("Claw: 1490");
});

test("Not OK records the result, stops output and requires power-off finish", async ({ page }) => {
  await move(page, 1510);
  await page.locator("#trial-unsafe").click();
  await expect(page.locator("#trial-message")).toContainText("cut servo power");
  await expect(page.locator("#trial-finish-panel")).toBeVisible();
  await expect(page.locator("#trial-motion")).toBeHidden();
  await page.locator("#trial-settings > summary").click();
  await expect(page.locator("#trial-history")).toContainText("1510 μs — Not OK");
});

test("large positions are rejected without losing the test session", async ({ page }) => {
  await page.locator("#trial-target").fill("1800");
  await page.locator("#trial-apply").click();
  await expect(page.locator("#trial-error")).toContainText("within 10 μs");
  await expect(page.locator("#trial-apply")).toBeEnabled();
  await move(page, 1510);
  await page.locator("#trial-settled").click();
});
