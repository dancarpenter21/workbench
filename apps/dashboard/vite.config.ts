import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    allowedHosts: ["pi1-home-ddc-crew"],
    proxy: {
      "/api": {
        target:
          process.env.WORKBENCH_COORDINATOR_URL ?? "http://127.0.0.1:8100",
        configure(proxy) {
          // Forward browser cancellation to the bounded Pi recording request.
          proxy.on("proxyReq", (upstream, _request, response) => {
            const cancel = () => { if (!response.writableEnded) upstream.destroy(); };
            response.on("close", cancel);
            upstream.on("close", () => response.off("close", cancel));
          });
        },
        rewrite: (path) => path.replace(/^\/api\/coordinator/, ""),
      },
    },
  },
});
