// Request the latest new frame as soon as the previous frame is displayed.
// Only one request/decode is active, so slow clients cannot build a frame queue.
export function mountCamera() {
  const camera = document.querySelector<HTMLImageElement>("#camera")!;
  const status = document.querySelector<HTMLElement>("#camera-status")!;
  const note = document.querySelector<HTMLElement>("#camera-note")!;
  const timing = document.querySelector<HTMLElement>("#camera-timing")!;
  let frameId: string | null = null;
  let frameCount = 0;
  let lastFrameAt: number | null = null;
  let frameTimes: number[] = [];
  let ageAtDisplay: number | null = null;
  let timestamp = "";
  let source = "";
  let failed = false;
  let currentUrl: string | null = null;
  let closed = false;
  let controller: AbortController | null = null;

  function render() {
    const now = performance.now();
    frameTimes = frameTimes.filter(time => now - time < 3000);
    const elapsed = lastFrameAt === null ? null : now - lastFrameAt;
    const state = failed ? "Unavailable" : elapsed === null ? "Connecting" : elapsed >= 3000 ? "Stale" : "Live";
    const fps = state === "Live" && frameTimes.length > 1
      ? ((frameTimes.length - 1) * 1000 / (frameTimes.at(-1)! - frameTimes[0])).toFixed(1)
      : "0.0";
    status.textContent = `${state} · ${fps} preview fps · target 30`;
    if (elapsed === null) return;
    const age = ageAtDisplay === null ? null : ageAtDisplay + elapsed;
    const label = source === "sensor" ? "sensor→display" : "capture→display";
    const delay = source === "simulated" ? "SIMULATED timing"
      : age === null ? "Delay unavailable"
      : `${label} ≤ ${Math.ceil(age)} ms${source === "sensor" ? "" : " · sensor buffering unknown"}`;
    timing.textContent = `${frameCount % 2 ? "●" : "○"} Frame ${frameCount} · ${timestamp} · ${delay}`;
    timing.dataset.state = failed || elapsed >= 3000 ? "stale" : age !== null && age > 200 ? "delayed" : "live";
  }

  async function receive() {
    while (!closed) {
      const started = performance.now();
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 5000);
      let nextUrl: string | null = null;
      try {
        const query = new URLSearchParams({ t: String(Date.now()) });
        if (frameId !== null) query.set("after", frameId);
        const response = await fetch(`/api/coordinator/camera/preview?${query}`, {
          cache: "no-store", signal: controller.signal,
        });
        if (response.status === 204) continue;
        if (!response.ok) throw new Error("Camera unavailable");
        const id = response.headers.get("X-Frame-Id");
        if (!id) throw new Error("Camera service needs restart for frame timing");
        if (id === frameId) {
          // A repeated frame never counts as fresh, even if the HTTP request succeeds.
          await new Promise(resolve => setTimeout(resolve, 10));
          continue;
        }
        const blob = await response.blob();
        nextUrl = URL.createObjectURL(blob);
        const decoded = new Image();
        decoded.src = nextUrl;
        await decoded.decode();
        if (closed) break;
        camera.src = nextUrl;
        if (currentUrl) URL.revokeObjectURL(currentUrl);
        currentUrl = nextUrl;
        nextUrl = null;
        frameId = id;
        frameCount++;
        lastFrameAt = performance.now();
        frameTimes.push(lastFrameAt);
        source = response.headers.get("X-Frame-Time-Source") ?? "";
        const ageHeader = response.headers.get("X-Frame-Age-Ms");
        const serverAge = ageHeader === null ? NaN : Number(ageHeader);
        // Server age + entire request/decode duration is a conservative bound.
        // This includes long-poll waiting, and requires no shared browser/Pi clock.
        ageAtDisplay = Number.isFinite(serverAge) && serverAge >= 0
          ? serverAge + lastFrameAt - started : null;
        const captured = response.headers.get("X-Frame-Captured-At");
        timestamp = captured ? captured.slice(11, 23) + " UTC" : "Time unavailable";
        failed = false;
        note.hidden = source !== "simulated";
        note.textContent = "Simulated scene · reset after each delivery";
        render();
      } catch (error) {
        if (closed) break;
        failed = true;
        note.hidden = false;
        note.textContent = error instanceof Error && error.message.includes("restart")
          ? error.message : "Camera unavailable — check the vision application.";
        render();
        await new Promise(resolve => setTimeout(resolve, 500));
      } finally {
        clearTimeout(timeout);
        if (nextUrl) URL.revokeObjectURL(nextUrl);
      }
    }
  }

  const timer = window.setInterval(render, 100);
  window.addEventListener("pagehide", () => {
    closed = true;
    controller?.abort();
    clearInterval(timer);
    if (currentUrl) URL.revokeObjectURL(currentUrl);
  }, { once: true });
  void receive();
}
