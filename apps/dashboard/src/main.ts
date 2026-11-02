import createClient from "openapi-fetch";
import type { paths, components } from "@workbench/contracts";
import { record } from "./audio";
import { mountHarness } from "./harness";
import { mountCamera } from "./camera";
import { ageCurrent, renderCurrent, renderHatPresence, renderServoPower } from "./current";
import "./style.css";
import { mountDiagnostics } from "./diagnostics";
import { mountCommissioning } from "./commissioning";
import { mountManual } from "./manual";
import { mountNetwork } from "./network";

type Status = components["schemas"]["WorkbenchStatus"];
const api = createClient<paths>({ baseUrl: "/api" });
const root = document.querySelector<HTMLDivElement>("#app")!;
root.innerHTML = `
  <header><a href="/" class="wordmark"><span class="mark">W</span> WORKBENCH</a>
    <aside id="network-status" class="device-pill" aria-label="Client connection to Pi server"></aside>
    <span id="servo-power" class="device-pill" role="status" aria-label="Servo power"></span>
    <div id="header-current" aria-label="Claw current monitor"></div>
    <div class="header-status"><span id="connection" class="device-pill" aria-label="Servo HAT status" data-state="waiting">Servo HAT · checking</span></div>
  </header>
  <nav class="view-navigation" aria-label="Workbench views">
    <button id="workbench-tab" class="secondary" aria-pressed="true">⌂ Workbench</button>
    <button id="config-tab" class="secondary" aria-pressed="false">Config</button>
    <button id="model-tab" class="secondary" aria-pressed="false">Model</button>
    <button id="jog-tab" class="secondary" aria-pressed="false">✥ Manual</button>
    <button id="diagnostics-tab" aria-label="Diagnostics" class="secondary" aria-pressed="false">⚙ Diagnostics</button>
    <button id="stop" class="danger">■ Stop arm</button>
  </nav>
  <p id="startup-status" role="status">Connecting…</p>
  <main id="workbench-view" hidden>
    <div class="workbench-layout">
      <div class="camera-panel"><div class="panel-heading"><h2>Workbench view</h2></div>
        <div class="camera-view"><video id="camera" hidden autoplay muted playsinline aria-label="Overhead view of the workbench"></video></div>
        <p id="camera-status" role="status">Connecting…</p>
        <div class="camera-footer"><details class="camera-details"><summary>Camera diagnostics</summary><p id="camera-timing">Waiting for video…</p></details><button id="camera-record" class="camera-record" aria-label="Record camera video" title="Record camera video" aria-pressed="false" disabled></button></div>
        <p id="recording-status" class="caption" role="status"></p>
        <p id="camera-note" class="caption" role="status">Connecting to camera…</p>
      </div>
      <div class="tab-content">
      <div id="tools-view" class="command-panel"><p class="eyebrow">YOUR NEXT TOOL</p><h2>What do you need?</h2>
        <form id="request-form"><label for="request">Request a tool</label>
          <textarea id="request" rows="2" maxlength="1000" placeholder="Grab me the crescent wrench" required></textarea>
          <div class="actions"><button id="submit" type="submit">Retrieve tool <span>↗</span></button>
            <button id="record" type="button" class="secondary">Speak</button></div></form>
        <p id="voice-note" class="caption">Click Speak, say your request, then finish and review it.</p>
        <div class="operation" aria-live="polite"><span id="operation-state" class="eyebrow">READY</span>
          <p id="operation-message">Waiting for your first request.</p><small id="operation-id"></small></div>
        <p id="error" role="alert"></p>
        <div class="controls"><button id="reset" class="secondary" hidden>Reset mock scene</button></div>
        <section id="harness-panel" class="harness-panel"></section>
      </div>
    <section id="config-view" hidden><h1>Config</h1><p>DF Robot v2 · Base 5 → Claw 0 · INA219 0x41 · Shunt 0.1 Ω · Software trip at |current| ≥ 2.5 A</p><p>Independent bounds (µs): Base 500–2500; Shoulder 600–2100; Elbow 775–2500; Wrist Bend 500–2500; Wrist Rotation 500–2500; Claw 850–1600.</p><section id="commissioning-panel" class="manual-panel"></section><section id="llm-config-panel"></section></section>
    <section id="manual-panel" class="manual-panel"></section>
    <section id="diagnostics-view" hidden></section>
    <section id="model-view" hidden><h1>Model workspace</h1><p>Build a measured model of the fixed DF Robot v2 arm. Preview geometry, inspect collisions and compare reference poses. Physical execution remains gated.</p><section id="model-panel"></section></section>
      </div>
    </div>
    <footer><span>LOCAL PROCESSING</span><div id="services">Checking applications…</div></footer>
  </main>`;

const headerObserver = new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty("--header-height", `${entry.target.getBoundingClientRect().height}px`);
});
headerObserver.observe(document.querySelector("header")!);

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
const diagnostics = mountDiagnostics(diagnosticsView);
const manual = mountManual(el("manual-panel"), () => button("stop").click());
mountCommissioning(el("commissioning-panel"));
let robotModel: ReturnType<typeof import("./robot-model").mountRobotModel> | null = null;
let loadingModel: Promise<void> | null = null;
let selectedView = false;
type View = "workbench" | "config" | "jog" | "diagnostics" | "model";
function showView(view: View) {
  el("workbench-view").classList.toggle("model-active", view === "model");
  el("model-view").hidden = view !== "model";
  if (view === "model" && !robotModel && !loadingModel) {
    el("model-panel").textContent = "Loading model editor…";
    loadingModel = import("./robot-model").then(module => {
      robotModel = module.mountRobotModel(el("model-panel"));
      robotModel.setVisible(!el("model-view").hidden);
    }).catch(() => {
      el("model-panel").textContent = "Model editor could not load. Select Model again to retry.";
    }).finally(() => { loadingModel = null; });
  }
  robotModel?.setVisible(view === "model");
  manual.setVisible(view === "jog");
  diagnosticsView.hidden = view !== "diagnostics";
  el("tools-view").hidden = view !== "workbench";
  el("config-view").hidden = view !== "config";
  el("manual-panel").hidden = view !== "jog";
  for (const name of ["workbench", "config", "jog", "diagnostics", "model"] as const) {
    button(`${name}-tab`).setAttribute("aria-pressed", String(name === view));
  }
}
for (const view of ["workbench", "config", "jog", "diagnostics", "model"] as const) {
  button(`${view}-tab`).onclick = () => { selectedView = true; showView(view); };
}
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
  diagnostics.updateWorkbench(status);
  if (!selectedView) {
    showView("workbench");
    if (status.commissioning_ready !== null && status.commissioning_ready !== undefined) {
      selectedView = true;
    }
  }
  el("startup-status").hidden = true;
  el("workbench-view").hidden = false;
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
  const elapsed = (performance.now() - currentReceivedAt) / 1000;
  const sample = ageCurrent(latest?.claw_current, elapsed, connected);
  renderCurrent(el("header-current"), sample);
  renderServoPower(el("servo-power"), sample);
  renderHatPresence(el("connection"), connected ? latest?.servo_hat : null, elapsed, connected);
}
refreshCurrent();
setInterval(refreshCurrent, 100);

let events: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let lastEventAt = performance.now();
let lastStatusAt: number | null = null;
mountNetwork(el("network-status"), () => lastStatusAt === null ? null : (performance.now() - lastStatusAt) / 1000);

function reconnectEvents() {
  const previous = events;
  events = null;
  previous?.close?.();
  manual.updateWorkbench(null);
  diagnostics.updateWorkbench(null);
  connected = false;
  refreshCurrent();
  updateControls();
  if (reconnectTimer === null) {
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connectEvents(); }, 1000);
  }
}
function connectEvents() {
  const address = new URL("/api/coordinator/ws/status", window.location.href);
  address.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const source = new WebSocket(address);
  events = source;
  lastEventAt = performance.now();
  source.onmessage = event => {
    if (events !== source) return;
    let status: Status;
    try { status = JSON.parse(event.data) as Status; }
    catch { reconnectEvents(); return; }
    lastEventAt = performance.now();
    lastStatusAt = lastEventAt;
    connected = true;
    render(status);
    if (source.readyState === WebSocket.OPEN) source.send("ack");
  };
  source.onclose = () => { if (events === source) reconnectEvents(); };
  source.onerror = () => { if (events === source) reconnectEvents(); };
}
connectEvents();
setInterval(() => {
  if (events && performance.now() - lastEventAt >= 3000) reconnectEvents();
}, 1000);

mountCamera();
mountHarness(el("harness-panel"), () => button("stop").click());

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

  } catch {
    el("services").textContent = "Applications unreachable";
  }
}
void refreshServices();
setInterval(() => void refreshServices(), 5000);
updateControls();
