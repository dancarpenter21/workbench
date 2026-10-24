import { test, expect } from "@playwright/test";

for (const ready of [false, true]) {
  test(`opens ${ready ? "Workbench" : "Initialization"} from commissioning limits`, async ({ page }) => {
    await page.addInitScript(({ ready }) => {
      class StartupEvents {
        onmessage: ((event: { data: string }) => void) | null = null;
        timer: ReturnType<typeof setInterval>;
        constructor() {
          this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({
            mode: "mock", commissioning_ready: ready, retrieval_enabled: true,
            operation: null, recovery_required: false, message: "", manual: null,
            commissioning: null,
          }) }), 75);
        }
        close() { clearInterval(this.timer); }
      }
      (window as any).EventSource = StartupEvents;
    }, { ready });
    await page.goto("/");
    await expect(page.locator(ready ? "#workbench-tab" : "#pulse-tab"))
      .toHaveAttribute("aria-pressed", "true");
    await page.locator("#diagnostics-tab").click();
    await expect(page.locator("#diagnostics-tab")).toHaveAttribute("aria-pressed", "true");
  });
}
