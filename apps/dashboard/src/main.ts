import createClient from "openapi-fetch";
import type { paths, components } from "@workbench/contracts";
import { record } from "./audio";
import { renderNeutral } from "./neutral";
import { renderCurrent, renderPowerIndicator } from "./current";
import "./style.css";
import { mountDiagnostics } from "./diagnostics";
import { mountCommissioning } from "./commissioning";
import { mountManual } from "./manual";

type Status = components["schemas"]["WorkbenchStatus"];
const api = createClient<paths>({ baseUrl: "/api" });
const root = document.querySelector<HTMLDivElement>("#app")!;
root.innerHTML = `
  <header><a href="/" class="wordmark"><span class="mark">W</span> WORKBENCH</a>
    <div class="header-status"><span id="power-indicator" class="power-indicator" role="status">… Current</span><span id="connection" class="badge" data-state="waiting">Connecting…</span>
      <button id="stop" class="danger">■ Stop arm</button></div></header>
  <nav class="view-navigation" aria-label="Workbench views">
    <button id="workbench-tab" class="secondary" aria-pressed="true">⌂ Workbench</button>
    <button id="pulse-tab" class="secondary" aria-pressed="false">↔ Pulse test</button>
    <button id="jog-tab" class="secondary" aria-pressed="false">✥ Jog</button>
    <button id="diagnostics-tab" aria-label="Diagnostics" class="secondary" aria-pressed="false">⚙ Diagnostics</button>
  </nav>
  <div id="shared-recovery"></div>
  <div id="neutral-reference" hidden></div>
  <main id="workbench-view">
    <div id="tools-view">
    <div id="workbench-current" aria-label="Claw supply monitoring"></div>
    <section class="workspace">
      <div class="camera-panel"><div class="panel-heading"><h2>Workbench view</h2><span id="mode" class="badge">—</span></div>
        <img id="camera" alt="Overhead view of the workbench" />
        <p id="camera-note" class="caption">Connecting to camera…</p>
        <div id="tools" class="tools"></div>
      </div>
      <div class="command-panel"><p class="eyebrow">YOUR NEXT TOOL</p><h2>What do you need?</h2>
        <form id="request-form"><label for="request">Request a tool</label>
          <textarea id="request" rows="2" maxlength="1000" placeholder="Grab me the crescent wrench" required></textarea>
          <div class="actions"><button id="submit" type="submit">Retrieve tool <span>↗</span></button>
            <button id="record" type="button" class="secondary">Speak</button></div></form>
        <p id="voice-note" class="caption">Click Speak, say your request, then finish and review it.</p>
        <div class="operation" aria-live="polite"><span id="operation-state" class="eyebrow">READY</span>
          <p id="operation-message">Waiting for your first request.</p><small id="operation-id"></small></div>
        <p id="error" role="alert"></p>
        <div class="controls"><button id="reset" class="secondary" hidden>Reset mock scene</button></div>
        <div id="recovery" hidden><p id="recovery-message"></p>
          <p id="recovery-error" role="alert"></p>
          <label class="ack"><input type="checkbox" id="ack" /> I have checked the arm and cleared the workbench.</label>
          <button id="recover" class="secondary" disabled>Recover</button></div>
      </div>
    </section>
    </div>
    <section id="commissioning-panel" class="manual-panel"></section>
    <section id="manual-panel" class="manual-panel"></section>
    <footer><span>LOCAL PROCESSING</span><div id="services">Checking applications…</div></footer>
  </main><main id="diagnostics-view" hidden></main>`;

const el = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id)! as T;
const button = (id: string) => el<HTMLButtonElement>(id);
const terminal = new Set([
  "completed",
  "clarification",
  "rejected",
  "failed",
  "stopped",
  "status",
]);
const diagnosticsView = el("diagnostics-view");
mountDiagnostics(diagnosticsView);
const commissioning = mountCommissioning(el("commissioning-panel"));
const manual = mountManual(el("manual-panel"), () => button("stop").click());
let selectedView = false;
type View = "workbench" | "pulse" | "jog" | "diagnostics";
function showView(view: View) {
  manual.setVisible(view === "jog");
  commissioning.setVisible(view === "pulse");
  diagnosticsView.hidden = view !== "diagnostics";
  el("workbench-view").hidden = view === "diagnostics";
  el("tools-view").hidden = view !== "workbench";
  el("commissioning-panel").hidden = view !== "pulse";
  el("manual-panel").hidden = view !== "jog";
  for (const name of ["workbench", "pulse", "jog", "diagnostics"] as const) {
    button(`${name}-tab`).setAttribute("aria-pressed", String(name === view));
  }
}
for (const view of ["workbench", "pulse", "jog", "diagnostics"] as const) {
  button(`${view}-tab`).onclick = () => { selectedView = true; showView(view); };
}
el("shared-recovery").append(el("recovery"));
showView("workbench");
let connected = false;
let latest: Status | null = null;
let currentReceivedAt = performance.now();
let submitting = false;
let microphoneBusy = false;
let finishRecording: (() => Promise<Blob>) | null = null;
let recordingTimer: ReturnType<typeof setTimeout> | undefined;

function failure(error: unknown) {
  el("error").textContent =
    error instanceof Error
      ? error.message
      : "Request failed. Check application status.";
}

function updateControls() {
  const active = !!latest?.operation && !terminal.has(latest.operation.state);
  button("submit").disabled =
    !connected || active || submitting || !!latest?.recovery_required || latest?.retrieval_enabled === false;
  button("reset").disabled = active;
  button("record").disabled = microphoneBusy;
}

function render(status: Status) {
  latest = status;
  currentReceivedAt = performance.now();
  refreshCurrent();
  manual.updateWorkbench(status);
  commissioning.updateWorkbench(status);
  if (!selectedView && status.profile === "diagnostics") showView("diagnostics");
  el("mode").textContent = status.mode === "mock" ? "SIMULATED" : "HARDWARE";
  button("reset").hidden = status.mode !== "mock";
  el("voice-note").textContent =
    status.mode === "mock"
      ? "Mock voice · review the transcript."
      : "Click Speak, say your request, then finish and review it.";
  const operation = status.operation;
  if (operation) {
    el("operation-state").textContent = operation.state.toUpperCase();
    el("operation-message").textContent = operation.message;
    el("operation-id").textContent = `REQUEST ${operation.id.slice(0, 8)}`;
  }
  el("recovery").hidden = !status.recovery_required;
  el("recovery-message").textContent =
    status.message ||
    "Inspection and recovery required before another request.";
  updateControls();
}

el("request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (button("submit").disabled) return;
  el("error").textContent = "";
  submitting = true;
  updateControls();
  try {
    const { data, error } = await api.POST("/coordinator/requests", {
      body: {
        text: el<HTMLTextAreaElement>("request").value.trim(),
        operation_id: crypto.randomUUID(),
      },
    });
    if (error) throw new Error(error.message);
    if (data && latest) render({ ...latest, operation: data });
  } catch (error) {
    failure(error);
  } finally {
    submitting = false;
    updateControls();
  }
});

button("stop").onclick = async () => {
  manual.end();
  void commissioning.end();
  try {
    const { data, error } = await api.POST("/coordinator/stop");
    if (error) throw new Error(error.message);
    if (data) render(data);
  } catch {
    failure(
      new Error("Stop could not be confirmed. Use the physical power cutoff."),
    );
  }
};

el<HTMLInputElement>("ack").onchange = () => {
  button("recover").disabled = !el<HTMLInputElement>("ack").checked;
};
button("recover").onclick = async () => {
  el("recovery-error").textContent = "";
  try {
    const { data, error } = await api.POST("/coordinator/recover", {
      body: { acknowledged: true },
    });
    if (error) throw new Error(error.message);
    if (data) render(data);
    el<HTMLInputElement>("ack").checked = false;
    button("recover").disabled = true;
    el("error").textContent = "";
  } catch (error) {
    el("recovery-error").textContent = error instanceof Error ? error.message : "Recovery failed";
  }
};
button("reset").onclick = async () => {
  try {
    const { error } = await api.POST("/coordinator/mock/reset");
    if (error) throw new Error(error.message);
  } catch (error) {
    failure(error);
  }
};

button("record").onclick = async () => {
  microphoneBusy = true;
  updateControls();
  try {
    if (!finishRecording) {
      finishRecording = await record();
      button("record").textContent = "Finish recording";
      recordingTimer = setTimeout(() => button("record").click(), 29_000);
    } else {
      clearTimeout(recordingTimer);
      const finish = finishRecording;
      finishRecording = null;
      button("record").textContent = "Transcribing…";
      const audio = await finish();
      const response = await fetch("/api/coordinator/transcriptions", {
        method: "POST",
        headers: { "Content-Type": "audio/wav" },
        body: audio,
      });
      if (!response.ok)
        throw new Error("Transcription failed. Check the voice application.");
      const data =
        (await response.json()) as components["schemas"]["Transcript"];
      el<HTMLTextAreaElement>("request").value = data.text;
      button("record").textContent = "Speak";
    }
  } catch (error) {
    failure(error);
    button("record").textContent = "Speak";
  } finally {
    microphoneBusy = false;
    updateControls();
  }
};

function refreshCurrent() {
  const source = connected ? latest?.claw_current : null;
  const elapsed = (performance.now() - currentReceivedAt) / 1000;
  const sample = source ? { ...source, age_seconds: (source.age_seconds ?? 0.3) + elapsed } : null;
  renderCurrent(el("workbench-current"), sample);
  renderPowerIndicator(el("power-indicator"), sample);
  renderNeutral(el("neutral-reference"), connected ? latest?.neutral_evidence : null, sample);
}
refreshCurrent();
setInterval(refreshCurrent, 100);

const events = new EventSource("/api/coordinator/events");
events.onmessage = (event) => {
  connected = true;
  el("connection").textContent = "CONNECTED";
  el("connection").dataset.state = "ok";
  render(JSON.parse(event.data) as Status);
};
events.onerror = () => {
  manual.updateWorkbench(null);
  commissioning.updateWorkbench(null);
  connected = false;
  refreshCurrent();
  el("connection").textContent = "RECONNECTING";
  el("connection").dataset.state = "error";
  updateControls();
};

const camera = el<HTMLImageElement>("camera");
camera.onload = () => {
  el("camera-note").textContent =
    latest?.mode === "mock"
      ? "Simulated scene · reset after each delivery"
      : "Fixed overhead view · keep the workspace clear";
};
camera.onerror = () => {
  el("camera-note").textContent =
    "Camera unavailable — check the vision application.";
};
function refreshCamera() {
  if (el("workbench-view").hidden || el("tools-view").hidden) return;
  camera.src = `/api/coordinator/camera/preview?t=${Date.now()}`;
}
refreshCamera();
setInterval(refreshCamera, 1000);

async function refreshServices() {
  try {
    const { data } = await api.GET("/coordinator/services");
    el("services").replaceChildren(
      ...(data ?? []).map((service) => {
        const span = document.createElement("span");
        span.className = service.ready ? "service ready" : "service";
        span.textContent = service.service;
        span.title = service.detail;
        return span;
      }),
    );
    const { data: tools } = await api.GET("/coordinator/tools");
    el("tools").replaceChildren(
      ...(tools ?? []).map((tool) => {
        const chip = document.createElement("button");
        chip.className = "tool";
        chip.textContent = tool.name;
        chip.onclick = () => {
          el<HTMLTextAreaElement>("request").value =
            `Grab me the ${tool.aliases[0]}`;
        };
        return chip;
      }),
    );
  } catch {
    el("services").textContent = "Applications unreachable";
  }
}
void refreshServices();
setInterval(() => void refreshServices(), 5000);
updateControls();
