import { test, expect } from "@playwright/test";

test("edits prompts, inspects schemas and shows a simulated sequence trace", async ({ page }) => {
  let active = false;
  const arm = () => ({ active, simulated: true, prepared: true, recovery_required: false,
    message: active ? "Supervised harness enabled" : "Ready for supervised fixture",
    remaining_seconds: active ? 300 : 0, claw_current: {}, joints: [
      { id: "base", label: "Base", channel: 5, min_us: 1400, max_us: 1600,
        max_rate_us_per_second: 100, direction: "Increasing pulse width (simulated)", commanded_us: 1500 },
    ] });
  await page.route("**/harness/arm", route => route.fulfill({ json: arm() }));
  await page.route("**/harness/session", route => {
    expect(route.request().postDataJSON()).toMatchObject({ supervised: true, claw_open_us: 1450, claw_close_us: 1550 });
    active = true;
    return route.fulfill({ json: { token: "a0631b69-6f79-4d76-8399-61f7288c7081" } });
  });
  await page.route("**/harness/heartbeat", route => route.fulfill({ json: arm() }));
  await page.route("**/harness/runs", route => route.fulfill({ status: 202, json: { id: "b0631b69-6f79-4d76-8399-61f7288c7081" } }));
  await page.routeWebSocket("**/harness/runs/*/events", ws => {
    ws.send(JSON.stringify({ state: "completed", events: [
      { kind: "tool_call", data: { name: "execute_sequence", arguments: { steps: [
        { action: "move_joint", joint_id: "base", target_pulse_us: 1510 },
      ] } } },
      { kind: "tool_result", data: { simulated: true, message: "Targets commanded; physical arrival and grip unverified" } },
    ] }));
  });
  await page.goto("/");
  await page.locator("#workbench-tab").click();
  await page.getByText("Prompts and tool configuration", { exact: true }).click();
  await page.locator("#harness-model").fill("fixture-model");
  await page.locator("#harness-prompt").fill("Inspect the scene before requesting movement.");
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect(page.locator("#harness-message")).toContainText("Saved revision");
  await page.reload();
  await page.locator("#workbench-tab").click();
  await page.getByText("Prompts and tool configuration", { exact: true }).click();
  await expect(page.locator("#harness-prompt")).toHaveValue("Inspect the scene before requesting movement.");
  await page.getByText("Argument schema (read-only)", { exact: true }).first().click();
  await expect(page.locator("#harness-tools")).toContainText("additionalProperties");
  await page.getByText("Prompts and tool configuration", { exact: true }).click();
  await page.getByText("Physical session", { exact: true }).click();
  await page.locator("#harness-open").fill("1450");
  await page.locator("#harness-close").fill("1550");
  await page.locator("#harness-supervised").check();
  await page.locator("#harness-unverified").check();
  await page.locator("#harness-enable").click();
  await expect(page.locator("#harness-arm")).toContainText("Supervised harness enabled");
  await page.locator("#harness-text").fill("Inspect the scene and move base to 1510 microseconds.");
  await page.locator("#harness-run").click();
  await expect(page.locator("#harness-trace")).toContainText("physical arrival and grip unverified");
  await expect(page.locator("#harness-message")).toHaveText("Run completed");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: ".runtime/harness-workbench.png", fullPage: true });
  let stopped = false;
  await page.route("**/coordinator/stop", route => {
    stopped = true;
    return route.fulfill({ json: { mode: "mock", operation: null, recovery_required: true } });
  });
  await page.locator("#harness-stop").click();
  await expect.poll(() => stopped).toBe(true);
});
