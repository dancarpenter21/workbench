import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target:
          process.env.WORKBENCH_COORDINATOR_URL ?? "http://127.0.0.1:8100",
        rewrite: (path) => path.replace(/^\/api\/coordinator/, ""),
      },
    },
  },
});
