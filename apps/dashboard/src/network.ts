// Independent of hardware polling and the status WebSocket. This measures the
// browser → dashboard proxy → coordinator route, not WiFi signal strength.
export function mountNetwork(root: HTMLElement, streamAge: () => number | null) {
  root.innerHTML = `<span id="network-summary" role="status">Pi link · checking</span>
    <details><summary>Connection details</summary><div id="network-details"></div></details>`;
  let lastReply: number | null = null;
  let rtt: number | null = null;
  let failures = 0;
  let error = "";
  let simulated = false;
  const outcomes: boolean[] = [];

  function render() {
    const age = lastReply === null ? null : (performance.now() - lastReply) / 1000;
    const telemetryAge = streamAge();
    const lost = outcomes.filter(ok => !ok).length;
    const state = failures >= 3 || (age !== null && age >= 5) ? "offline"
      : lastReply === null ? failures ? "degraded" : "checking"
      : failures || (rtt ?? 0) >= 500 || telemetryAge === null || telemetryAge >= 1 ? "degraded" : "connected";
    root.dataset.state = state;
    root.querySelector("#network-summary")!.textContent = `Pi link · ${state}${rtt === null ? "" : ` · ${Math.round(rtt)} ms`}`;
    root.querySelector("#network-details")!.textContent =
      `${simulated ? "SIMULATED server route. " : ""}Browser → dashboard proxy → Pi coordinator (HTTP). Last reply: ${age === null ? "none" : `${age.toFixed(1)} s ago`}. ` +
      `Last round trip: ${rtt === null ? "—" : `${Math.round(rtt)} ms`}. ` +
      `Failed probes: ${lost}/${outcomes.length} (last 20); consecutive: ${failures}. ` +
      `Status stream: ${telemetryAge === null ? "awaiting updates" : `${telemetryAge.toFixed(1)} s since update`}. ` +
      `Probe every 1 s after completion; timeout 1.5 s. ${error} ` +
      "Measures server reachability, not WiFi signal strength or servo power.";
  }

  async function probe() {
    const started = performance.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
      const response = await fetch("/api/coordinator/health", {
        cache: "no-store", signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      if (health.service !== "coordinator") throw new Error("Unexpected heartbeat response");
      simulated = health.mode === "mock";
      rtt = performance.now() - started;
      lastReply = performance.now();
      failures = 0;
      error = "";
      outcomes.push(true);
    } catch (cause) {
      failures++;
      outcomes.push(false);
      error = controller.signal.aborted ? "Last probe timed out." :
        `Last probe failed: ${cause instanceof Error ? cause.message : "network error"}.`;
    } finally {
      clearTimeout(timeout);
      if (outcomes.length > 20) outcomes.shift();
      render();
      setTimeout(() => void probe(), 1000);
    }
  }
  render();
  setInterval(render, 250);
  void probe();
}
