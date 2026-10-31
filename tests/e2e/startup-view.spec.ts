import { test, expect } from "@playwright/test";

for (const ready of [false, true]) {
  test(`opens Workbench regardless of commissioning limits (${ready}) from commissioning limits`, async ({ page }) => {
    await page.addInitScript(({ ready }) => {
      class StartupEvents {
      static OPEN = 1;
      readyState = 1;
      send(_message: string) {}
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
      (window as any).WebSocket = new Proxy(window.WebSocket, {
      construct(native, args) {
        return String(args[0]).includes("/ws/status") ? new StartupEvents() : Reflect.construct(native, args);
      },
    });
    }, { ready });
    await page.goto("/");
    await expect(page.locator("#workbench-tab"))
      .toHaveAttribute("aria-pressed", "true");
    await page.locator("#diagnostics-tab").click();
    await expect(page.locator("#diagnostics-tab")).toHaveAttribute("aria-pressed", "true");
  });
}
