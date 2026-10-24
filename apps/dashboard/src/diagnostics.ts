import createClient from "openapi-fetch";
import type { paths, components } from "@workbench/contracts";
import { mountCurrentScale } from "./current-scale";
import { ageCurrent, currentPresentation, renderCurrent, renderHatPresence, renderServoPower } from "./current";

type Report = components["schemas"]["DiagnosticsReport"];
type Check = components["schemas"]["Diagnostic"];
const api = createClient<paths>({ baseUrl: "/api" });

export function mountDiagnostics(root: HTMLElement) {
  root.innerHTML = `
    <div class="intro diagnostics-intro"><p class="eyebrow">HARDWARE / DIAGNOSTICS</p>
      <h1>Device checks</h1>
      <p>Live inputs and device checks from the Raspberry Pi.</p>
      <div class="diagnostic-toolbar"><span id="diagnostic-mode" class="badge">Connecting…</span>
        <button id="diagnostic-refresh" class="secondary">Refresh checks</button>
        <button id="diagnostic-download" class="secondary" disabled>Download report</button></div>
      <p id="diagnostic-note" role="status">Checking devices…</p>
    </div>
    <div class="workspace">
      <section class="command-panel"><p class="eyebrow">AUDIO INPUT</p><h2>Pi USB microphone</h2>
        <p>Record five seconds from the microphone attached to the Pi, then listen here.</p>
        <label for="diagnostic-device">Capture device</label>
        <select id="diagnostic-device"><option value="">Loading devices…</option></select>
        <div class="actions"><button id="diagnostic-record" disabled>Test microphone · 5 seconds</button>
          <button id="diagnostic-cancel" class="secondary" hidden>Cancel</button></div>
        <p id="diagnostic-audio-note" role="status">Audio stays in memory and is excluded from reports.</p>
        <label for="diagnostic-level">Peak input level</label>
        <meter id="diagnostic-level" min="0" max="1" value="0"></meter>
        <audio id="diagnostic-audio" controls hidden></audio>
      </section>
    </div>
    <div id="diagnostic-hardware" class="diagnostic-grid" aria-label="Live arm hardware">
      <section class="diagnostic-card" data-component="claw_current"><h2>Claw INA219</h2>
        <p id="diagnostic-sensor-state" role="status"></p><div id="diagnostic-live-current"></div>
        <div id="diagnostic-servo-power"></div></section>
      <section class="diagnostic-card" data-component="hat"><h2>Servo HAT</h2>
        <div id="diagnostic-live-hat"></div><p id="diagnostic-hat-detail"></p></section>
      <section class="diagnostic-card" data-component="motion_interlock"><h2>Motion interlock</h2>
        <p id="diagnostic-interlock-state" role="status"></p><p id="diagnostic-interlock-reason"></p></section>
      <section class="diagnostic-card" data-component="neutral_reference"><h2>Neutral commissioning reference</h2>
        <p id="diagnostic-neutral-state" role="status"></p>
        <details id="diagnostic-neutral-record" hidden><summary>Recorded neutral reference</summary>
          <dl id="diagnostic-neutral-measurements"></dl></details></section>
    </div>
    <div id="diagnostic-checks" class="diagnostic-grid" aria-live="polite"></div>
    <section id="diagnostic-current-scale" class="diagnostic-card"></section>`;
  const scales = mountCurrentScale(root.querySelector<HTMLElement>("#diagnostic-current-scale")!);
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  const refreshButton = el<HTMLButtonElement>("diagnostic-refresh");
  const downloadButton = el<HTMLButtonElement>("diagnostic-download");
  const recordButton = el<HTMLButtonElement>("diagnostic-record");
  const device = el<HTMLSelectElement>("diagnostic-device");
  const audio = el<HTMLAudioElement>("diagnostic-audio");
  let report: Report | null = null;
  let busy = false;
  let devicesBusy = false;
  let capture: AbortController | null = null;
  let audioUrl: string | null = null;
  let workbench: components["schemas"]["WorkbenchStatus"] | null = null;
  let receivedAt = performance.now();
  let connected = false;
  const hardwareComponents = new Set(["claw_current", "hat", "motion_interlock", "neutral_reference"]);
  function renderHardware() {
    const elapsed = (performance.now() - receivedAt) / 1000;
    const sample = ageCurrent(workbench?.claw_current, elapsed, connected);
    const view = currentPresentation(sample);
    renderCurrent(el("diagnostic-live-current"), sample);
    renderServoPower(el("diagnostic-servo-power"), sample);
    renderHatPresence(el("diagnostic-live-hat"), workbench?.servo_hat, elapsed, connected);
    el("diagnostic-sensor-state").textContent = view.sensorLabel;
    el("diagnostic-hat-detail").textContent = el("diagnostic-live-hat").title;
    el("diagnostic-interlock-state").textContent = view.resetRequired ? "Motion inhibited — reset required" : !sample ? "Interlock status unavailable" : "No latched current-monitor fault";
    el("diagnostic-interlock-reason").textContent = sample?.fault ?? "";
    el("diagnostic-neutral-state").textContent = !view.fresh
      ? "Current readings unavailable; neutral comparison inactive."
      : workbench?.neutral_evidence?.detail ?? "Neutral reference not configured";
    const reference = workbench?.neutral_evidence?.reference;
    el("diagnostic-neutral-record").hidden = !reference;
    const recorded = el("diagnostic-neutral-measurements");
    const key = reference ? `${reference.trial_id}:${reference.samples_sha256}` : "";
    if (recorded.dataset.reference !== key) {
      recorded.replaceChildren(); recorded.dataset.reference = key;
      if (reference) {
        for (const [name, value] of [
          ["Trial", reference.trial_id], ["Samples", reference.sample_count],
          ["Duration (s)", reference.duration_seconds], ["Supply minimum (V)", reference.bus_v.min],
          ["Supply maximum (V)", reference.bus_v.max], ["Shunt minimum (mV)", reference.shunt_mv.min],
          ["Shunt maximum (mV)", reference.shunt_mv.max], ["Recorded scale verified", reference.scale_verified],
          ["Measured circuit", reference.measured_circuit],
        ]) {
          const term = document.createElement("dt"); term.textContent = String(name);
          const detail = document.createElement("dd"); detail.textContent = String(value);
          recorded.append(term, detail);
        }
      }
    }
    root.querySelector<HTMLElement>('[data-component="claw_current"]')!.dataset.state = view.reading;
    root.querySelector<HTMLElement>('[data-component="motion_interlock"]')!.dataset.state = view.resetRequired ? "warning" : !sample ? "unavailable" : "ok";
  }
  const checks = new Map<string, { check: Check; stale: boolean }>();
  const labels: Record<string, string> = {
    pi: "Raspberry Pi", camera: "Camera capture", recognition: "Tool recognition",
    claw_current: "Claw INA219", neutral_reference: "Neutral commissioning reference",
    microphone: "Pi microphone", hat: "Servo HAT", voice: "Speech model", assistant: "Language model",
  };
  const format = (value: unknown): string => {
    if (value === null) return "Unknown / unavailable";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return typeof value === "number" ? String(Number(value.toFixed(4))) : String(value);
  };
  function renderChecks() {
    el("diagnostic-checks").replaceChildren(...[...checks.values()].filter(({ check }) => !hardwareComponents.has(check.component)).map(({ check, stale }) => {
      const card = document.createElement("section");
      card.className = "diagnostic-card";
      const heading = document.createElement("div");
      heading.className = "panel-heading";
      const title = document.createElement("h2");
      title.textContent = labels[check.component] ?? check.component.replace("service:", "Service · ");
      const badge = document.createElement("span");
      badge.className = `badge diagnostic-status ${stale ? "warning" : check.status}`;
      badge.textContent = stale ? "◷ STALE" : `${check.status === "ok" ? "✓" : check.status === "warning" ? "!" : "✕"} ${check.status.toUpperCase()}`;
      heading.append(title, badge);
      const summary = document.createElement("p");
      summary.textContent = check.summary;
      const detail = document.createElement("dl");
      for (const [key, value] of Object.entries(check.measurements ?? {})) {
        const term = document.createElement("dt");
        term.textContent = key.replaceAll("_", " ");
        const description = document.createElement("dd");
        description.textContent = format(value);
        detail.append(term, description);
      }
      const timestamp = document.createElement("small");
      timestamp.textContent = `Checked ${new Date(check.checked_at ?? "").toLocaleTimeString()}`;
      card.append(heading, summary, detail, timestamp);
      return card;
    }));
  }
  async function refreshDevices() {
    if (capture || devicesBusy) return;
    devicesBusy = true;
    try {
      const { data, error } = await api.GET("/coordinator/diagnostics/microphone/devices");
      if (error || !data) throw new Error("Pi microphone service unavailable");
      if (capture) return;
      const selected = device.value;
      device.replaceChildren(...data.map((item) => new Option(item.name, item.id)));
      if (data.some((item) => item.id === selected)) device.value = selected;
      if (!data.length) device.add(new Option("No capture device found", ""));
      recordButton.disabled = !device.value;
    } catch {
      if (capture) return;
      device.replaceChildren(new Option("Pi microphone service unavailable", ""));
      recordButton.disabled = true;
    } finally {
      devicesBusy = false;
    }
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    try {
      const { data, error } = await api.GET("/coordinator/diagnostics", { signal: AbortSignal.timeout(10000) });
      if (error || !data) throw new Error("Diagnostics unavailable");
      report = data;
      for (const value of checks.values()) value.stale = true;
      for (const check of data.checks) checks.set(check.component, { check, stale: false });
      el("diagnostic-mode").textContent = data.mode === "mock" ? "SIMULATED" : "PI HARDWARE";
      el("diagnostic-note").textContent = data.profile === "diagnostics"
        ? "Diagnostics profile · arm motion and retrieval disabled. Live arm status updates continuously; other checks every five seconds."
        : `Workbench profile · ${data.mode === "mock" ? "SIMULATED" : "Pi hardware"} · ${data.retrieval_enabled ? "hardware checks do not establish motion readiness" : "automatic retrieval disabled"}. Live arm status updates continuously; other checks every five seconds.`;
      downloadButton.disabled = false;
    } catch {
      for (const value of checks.values()) value.stale = true;
      el("diagnostic-note").textContent = "Connection lost. Retained readings are stale; retrying automatically.";
    } finally {
      renderChecks();
      busy = false;
      refreshButton.disabled = false;
    }
  }
  refreshButton.onclick = () => { void refresh(); void refreshDevices(); };
  downloadButton.onclick = () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `workbench-diagnostics-${new Date().toISOString().replaceAll(":", "-")}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  el("diagnostic-cancel").onclick = () => capture?.abort();
  recordButton.onclick = async () => {
    if (capture || !device.value) return;
    capture = new AbortController();
    const timeout = setTimeout(() => capture?.abort(), 12000);
    recordButton.disabled = true;
    device.disabled = true;
    el("diagnostic-cancel").hidden = false;
    audio.pause();
    audio.hidden = true;
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioUrl = null;
    el("diagnostic-audio-note").textContent = "Recording on the Pi for five seconds…";
    try {
      const { data, error } = await api.POST("/coordinator/diagnostics/microphone/test", {
        body: { device_id: device.value }, signal: capture.signal,
      });
      if (error || !data) throw new Error(error?.message ?? "Microphone test failed");
      const bytes = Uint8Array.from(atob(data.wav_base64), (char) => char.charCodeAt(0));
      audioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
      audio.src = audioUrl;
      audio.hidden = false;
      el<HTMLMeterElement>("diagnostic-level").value = data.peak;
      el("diagnostic-audio-note").textContent = `${data.simulated ? "SIMULATED · " : ""}${data.duration_seconds.toFixed(1)} seconds · Peak ${(data.peak * 100).toFixed(1)}% · RMS ${(data.rms * 100).toFixed(1)}% · ${data.clipped ? "Clipping detected" : data.peak < 0.001 ? "Very quiet / silent input" : "No clipping"}. Play the recording below.`;
    } catch (error) {
      el("diagnostic-audio-note").textContent = capture.signal.aborted
        ? "Recording cancelled or timed out."
        : error instanceof Error ? error.message : "Microphone test failed";
    } finally {
      clearTimeout(timeout);
      capture = null;
      device.disabled = false;
      recordButton.disabled = !device.value;
      el("diagnostic-cancel").hidden = true;
    }
  };
  window.addEventListener("pagehide", () => { capture?.abort(); if (audioUrl) URL.revokeObjectURL(audioUrl); });
  void refresh();
  void refreshDevices();
  setInterval(() => { if (!root.hidden) { void refresh(); void refreshDevices(); } }, 5000);
  setInterval(() => { if (!root.hidden) renderHardware(); }, 100);
  renderHardware();
  return {
    updateWorkbench(value: components["schemas"]["WorkbenchStatus"] | null) {
      connected = value !== null;
      if (value) { workbench = value; receivedAt = performance.now(); }
      if (!root.hidden) renderHardware();
      scales.update(value?.commissioning ?? null);
    },
  };
}
