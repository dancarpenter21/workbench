import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174", screenshot: "only-on-failure" },
  webServer: {
    command: "uv run python scripts/dev.py --dashboard-port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: !process.env.CI,
    timeout: 90_000,
  },
});
