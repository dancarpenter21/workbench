import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const reference = JSON.parse(readFileSync("config/workbench/neutral-reference.json", "utf8"));

for (const state of ["not_neutral", "within_reference", "outside_reference", "fault"]) {
  test(`neutral evidence stays in diagnostic reports without a banner: ${state}`, async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 480 });
    const evidence = { reference, state, detail: state === "not_neutral" ? "Neutral targets not established; advisory only" : "Recorded-range comparison", commanded_neutral: state !== "not_neutral" };
    await page.addInitScript(({ evidence, state }) => {
      class FixtureEvents {
        onmessage: ((event: { data: string }) => void) | null = null;
        onerror: (() => void) | null = null;
        constructor() {
          setInterval(() => this.onmessage?.({ data: JSON.stringify({
            mode: "hardware", commissioning_ready: true, retrieval_enabled: false, operation: null,
            recovery_required: false, message: "", manual: null, commissioning: null,
            neutral_evidence: evidence,
            claw_current: { simulated: false, healthy: state !== "fault", fault: state === "fault" ? "Sensor failed" : "",
              age_seconds: 0.01, bus_v: 4.988, shunt_mv: -0.75, scale_verified: false, power_state: "energized" },
          }) }), 75);
        }
      }
      (window as any).EventSource = FixtureEvents;
    }, { evidence, state });
    await page.route("**/api/coordinator/diagnostics", route => route.fulfill({ json: {
      mode: "hardware", commissioning_ready: true, retrieval_enabled: false, checked_at: new Date().toISOString(),
      neutral_evidence: evidence,
      checks: [{ component: "neutral_reference", status: "ok", checked_at: new Date().toISOString(),
        summary: "Neutral hold reference", measurements: { bus_min_v: 4.956, sample_count: 17192, scale_verified: false } }],
    } }));
    await page.goto("/");
    await expect(page.locator("#neutral-reference, .neutral-heading")).toHaveCount(0);
    await expect(page.locator("#header-current .current-raw")).toContainText("mV");
    await page.locator("#diagnostics-tab").click();
    await expect(page.locator("#diagnostic-neutral, .neutral-heading")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Neutral commissioning reference", exact: true })).toBeVisible();
    await page.locator("#diagnostic-neutral-record > summary").click();
    await expect(page.locator("#diagnostic-neutral-measurements")).toContainText("4.956");
    const download = page.waitForEvent("download");
    await page.locator("#diagnostic-download").click();
    const stream = await (await download).createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream!) chunks.push(chunk);
    const report = JSON.parse(Buffer.concat(chunks).toString());
    expect(report.neutral_evidence.reference.samples_sha256).toBe(reference.samples_sha256);
    expect(report.neutral_evidence.reference.scale_verified).toBe(false);
  });
}
