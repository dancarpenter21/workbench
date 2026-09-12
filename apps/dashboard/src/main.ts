import createClient from "openapi-fetch";
import type { paths, components } from "@workbench/contracts";
import { record } from "./audio";
import "./style.css";

type Status = components["schemas"]["WorkbenchStatus"];
const api = createClient<paths>({ baseUrl: "/api" });
const root = document.querySelector<HTMLDivElement>("#app")!;
root.innerHTML = `
  <header><a href="/" class="wordmark"><span class="mark">W</span> WORKBENCH</a>
    <span id="connection" class="badge">Connecting…</span></header>
  <main>
    <div class="intro"><p class="eyebrow">ROBOTIC ASSISTANT / 01</p>
      <h1>A little help<br>at the bench.</h1>
      <p>Choose a tool. Your assistant will bring it to the delivery tray.</p></div>
    <section class="workspace">
      <div class="camera-panel"><div class="panel-heading"><h2>Workbench view</h2><span id="mode" class="badge">—</span></div>
        <img id="camera" alt="Overhead view of the workbench" />
        <p id="camera-note" class="caption">Connecting to camera…</p>
        <div id="tools" class="tools"></div>
      </div>
      <div class="command-panel"><p class="eyebrow">YOUR NEXT TOOL</p><h2>What do you need?</h2>
        <form id="request-form"><label for="request">Request a tool</label>
          <textarea id="request" rows="3" maxlength="1000" placeholder="Grab me the crescent wrench" required></textarea>
          <div class="actions"><button id="submit" type="submit">Retrieve tool <span>↗</span></button>
            <button id="record" type="button" class="secondary">Speak</button></div></form>
        <p id="voice-note" class="caption">Click Speak, say your request, then finish and review it.</p>
        <div class="operation" aria-live="polite"><span id="operation-state" class="eyebrow">READY</span>
          <p id="operation-message">Waiting for your first request.</p><small id="operation-id"></small></div>
        <p id="error" role="alert"></p>
        <div class="controls"><button id="stop" class="danger">Stop arm</button>
          <button id="reset" class="secondary" hidden>Reset mock scene</button></div>
        <div id="recovery" hidden><p id="recovery-message"></p>
          <label class="ack"><input type="checkbox" id="ack" /> I have checked the arm and cleared the workbench.</label>
          <button id="recover" class="secondary" disabled>Recover</button></div>
      </div>
    </section>
    <footer><span>LOCAL PROCESSING</span><div id="services">Checking applications…</div></footer>
  </main>`;

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
let connected = false;
let latest: Status | null = null;
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
    !connected || active || submitting || !!latest?.recovery_required;
  button("reset").disabled = active;
  button("record").disabled = microphoneBusy;
}

function render(status: Status) {
  latest = status;
  el("mode").textContent = status.mode === "mock" ? "SIMULATED" : "LIVE CAMERA";
  button("reset").hidden = status.mode !== "mock";
  el("voice-note").textContent =
    status.mode === "mock"
      ? "Mock voice returns “Grab me the screwdriver”. Review the text before sending."
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
    failure(error);
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

const events = new EventSource("/api/coordinator/events");
events.onmessage = (event) => {
  connected = true;
  el("connection").textContent = "CONNECTED";
  render(JSON.parse(event.data) as Status);
};
events.onerror = () => {
  connected = false;
  el("connection").textContent = "RECONNECTING";
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
setInterval(() => {
  camera.src = `/api/coordinator/camera/preview?t=${Date.now()}`;
}, 1000);

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
