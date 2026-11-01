import { test, expect, type Page } from "@playwright/test";
import preview from "./fixtures/simulated-preview.json";
import model from "../../ros/src/workbench_description/config/simulated.json";

async function mockStatus(page: Page) {
  await page.addInitScript(() => {
    class StatusSocket {
      static OPEN = 1;
      readyState = 1;
      onmessage: ((event: { data: string }) => void) | null = null;
      timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({ mode: "mock", commissioning_ready: false, retrieval_enabled: false, operation: null, recovery_required: false, message: "", manual: null, commissioning: null }) }), 75);
      send() {}
      close() { clearInterval(this.timer); }
    }
    const Native = window.WebSocket;
    window.WebSocket = new Proxy(Native, { construct(target, args) { return String(args[0]).includes("/ws/status") ? new StatusSocket() : Reflect.construct(target, args); } });
  });
}

test("model workspace converts units and keeps preview separate from motion", async ({ page }) => {
  await mockStatus(page);
  const mutations: string[] = [];
  let savedModel: typeof model | null = null;
  await page.route("**/api/**", route => {
    if (route.request().method() === "POST" && !route.request().url().includes("/camera/")) mutations.push(route.request().url());
    return route.fulfill({ json: {} });
  });
  await page.route("**/commissioning/robot-model", route => {
    if (route.request().method() === "POST") savedModel = route.request().postDataJSON().model;
    return route.fulfill({ json: { context_key: "simulated", revision: savedModel ? 1 : 0, domain: "simulated", model: savedModel ?? model, valid: !savedModel, validation_error: savedModel ? "Measurements incomplete" : "" } });
  });
  await page.route("**/commissioning/robot-model/preview", route => route.fulfill({ json: { context_key: "simulated", revision: 0, ...preview } }));
  await page.goto("/");
  await page.locator("#model-tab").click();
  await expect(page.locator("#model-view")).toBeVisible();
  await expect(page.locator("#robot-model-links > details")).toHaveCount(5);
  await expect(page.locator("#robot-model-joints > details")).toHaveCount(6);
  await expect(page.locator("#robot-model-links > details > summary")).toHaveText(["Base · orange", "Base-to-shoulder connector · yellow", "Upper arm · green · channel 4", "Forearm · blue · channel 3", "Claw assembly · red · channels 2, 1 and 0"]);
  await expect(page.locator("#robot-model-joints")).not.toContainText("tool_mount");
  await expect(page.locator("[data-model-assembly=\"wrist_link\"] > summary")).toHaveText("Claw assembly · red · channels 2, 1 and 0");
  await expect(page.locator('[data-model-assembly="wrist_link"] [data-model-link]')).toHaveCount(3);
  await page.locator("#robot-model-links summary").first().click();
  const original = await page.getByRole("spinbutton", { name: "base_link box size (inches) X", exact: true }).inputValue();
  await page.locator("#robot-model-units").selectOption("mm");
  await page.locator("#robot-model-links summary").first().click();
  await expect(page.getByRole("spinbutton", { name: "base_link box size (mm) X", exact: true })).toHaveValue(String(model.links[0].size![0] / 0.001));
  await page.locator("#robot-model-units").selectOption("inches");
  await page.locator("#robot-model-links summary").first().click();
  await expect(page.getByRole("spinbutton", { name: "base_link box size (inches) X", exact: true })).toHaveValue(original);
  await page.locator("#robot-model-upgrade").click();
  await page.locator("#robot-model-links summary").first().click();
  await page.getByRole("button", { name: "Add part box", exact: true }).first().click();
  await page.locator("#robot-model-links summary").first().click();
  await expect(page.getByRole("spinbutton", { name: "base_link 2 allowance (inches)", exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Add table / obstacle", exact: true }).click();
  await page.getByRole("button", { name: "Save model draft", exact: true }).click();
  await expect(page.locator("#robot-model-status")).toContainText("saved revision 1");
  expect(savedModel).toMatchObject({ schema_version: 2, complete: false, domain: "simulated", claw_envelope: [] });
  expect((savedModel as any).links[0].boxes).toHaveLength(2);
  expect((savedModel as any).links[0].boxes[0].margin_m).toBeNull();
  expect((savedModel as any).scene).toHaveLength(1);
  expect(mutations).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: ".runtime/model-workspace.png", fullPage: true });
});

test("inspection rejects stale poses and unavailable results", async ({ page }) => {
  await mockStatus(page);
  await page.route("**/api/**", route => route.fulfill({ json: {} }));
  await page.route("**/commissioning/robot-model", route => route.fulfill({ json: {
    context_key: "simulated", revision: 0, domain: "simulated", model, valid: true, validation_error: "",
  } }));
  await page.route("**/commissioning/robot-model/preview", route => route.fulfill({ json: { context_key: "simulated", revision: 0, ...preview } }));
  let release: () => void = () => {};
  let started: () => void = () => {};
  const requested = new Promise<void>(resolve => { started = resolve; });
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/commissioning/robot-model/inspect", async route => {
    started(); await delayed;
    await route.fulfill({ json: { available: true, errors: [], in_bounds: true, collision: false, colliding_pairs: [], tool_xyz: [0, 0, 0], bundle: {} } });
  });
  await page.goto("/");
  await page.locator("#model-tab").click();
  const slider = page.getByRole("slider", { name: "shoulder_joint simulated angle", exact: true });
  await expect(slider).toBeVisible();
  const original = await slider.inputValue();
  await page.getByRole("button", { name: "Check collisions at preview pose", exact: true }).click();
  await requested;
  await slider.fill("0.2");
  await slider.fill(original);
  release();
  await expect(page.locator("#robot-model-inspection")).toHaveText("Model or pose changed; inspect again.");
  await expect(page.locator("#robot-model-inspect")).toBeEnabled();
  await page.route("**/commissioning/robot-model/inspect", route => route.fulfill({ json: { available: false, errors: [] } }));
  await page.locator("#robot-model-inspect").click();
  await expect(page.locator("#robot-model-inspection")).toHaveText("Collision validation unavailable.");
  await expect(page.locator("#robot-model-inspect")).toBeEnabled();
  await page.route("**/commissioning/robot-model/inspect", route => route.fulfill({ json: { available: true, errors: [], in_bounds: true, collision: false, colliding_pairs: [], tool_xyz: [0, 0, 0], bundle: {} } }));
  await page.locator("#robot-model-inspect").click();
  await expect(page.locator("#robot-model-inspection")).toContainText("No collision at this pose");
  // An imported incomplete model may have no sliders to invalidate the old result.
  const incomplete = structuredClone(model);
  incomplete.joints.forEach(joint => { (joint as { xyz: unknown }).xyz = null; });
  await page.route("**/commissioning/robot-model/preview", route => route.fulfill({ json: {
    context_key: "simulated", revision: 0, urdf: '<robot name="workbench"><link name="base_link"/></robot>', omissions: ["Incomplete frames"], visible_boxes: 0,
  } }));
  await page.locator("#robot-model-import").setInputFiles({
    name: "incomplete.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(incomplete)),
  });
  await expect(page.locator("#robot-model-inspection")).toHaveText("Model imported; inspect the preview pose again.");
  page.once("dialog", dialog => dialog.accept());
  await page.locator("#robot-model-reload").click();
  await expect(page.locator("#robot-model-inspection")).toHaveText("Model loaded; inspect the preview pose again.");
  await page.locator("#robot-model-inspect").click();
  await expect(page.locator("#robot-model-inspection")).toContainText("No collision at this pose");
  await page.locator("#robot-model-upgrade").click();
  await expect(page.locator("#robot-model-inspection")).not.toContainText("No collision at this pose");
});
