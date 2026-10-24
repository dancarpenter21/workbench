import type { components } from "@workbench/contracts";

type Bounds = components["schemas"]["CommissionBounds"];
type Status = components["schemas"]["CommissionStatus"];
type Session = components["schemas"]["CommissionSession"];
type Workbench = components["schemas"]["WorkbenchStatus"];
const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];
const label = (id: string) => id === "base" ? "Base" : id === "claw" ? "Claw" : id.replace("joint_", "Joint ");

export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `
    <div class="panel-heading"><h2>Initialization</h2><span id="init-domain" class="caption"></span></div>
    <form id="init-form" novalidate>
      <div class="init-values" role="table" aria-label="Joint pulse values in microseconds">
        <div class="init-row init-head" role="row"><span>Joint</span><span>Pulse μs</span><span>Min μs</span><span>Max μs</span><span></span></div>
        ${joints.map(id => `<div class="init-row" role="row" data-joint="${id}">
          <label for="init-${id}-pulse">${label(id)}</label>
          <input id="init-${id}-pulse" aria-label="${label(id)} pulse" type="number" min="500" max="2500" step="1" value="1500" required />
          <input id="init-${id}-min" aria-label="${label(id)} minimum" type="number" min="500" max="1500" step="1" />
          <input id="init-${id}-max" aria-label="${label(id)} maximum" type="number" min="1500" max="2500" step="1" />
          <button type="button" data-move="${id}">Move</button>
        </div>`).join("")}
      </div>
      <button id="init-save" type="submit">Save limits</button>
    </form>
    <p id="init-error" role="alert" hidden></p>`;
  const input = (id: string, kind: string) => root.querySelector<HTMLInputElement>(`#init-${id}-${kind}`)!;
  const error = root.querySelector<HTMLElement>("#init-error")!;
  let bounds: Bounds | null = null;
  let status: Status | null = null;
  let token: string | null = null;
  let sequence = 0;
  let visible = false;
  let dirty = false;
  let pending = false;
  let polling = false;
  let generation = 0;
  let nextMove = 0;
  let heartbeat: Promise<void> | null = null;

  async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`/api/coordinator/commissioning/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(5000), keepalive: path === "stop",
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Request failed");
    return value as T;
  }
  function fail(value: unknown) {
    error.textContent = value instanceof Error ? value.message : "Request failed";
    error.hidden = false;
  }
  function render() {
    root.querySelectorAll<HTMLButtonElement>("button").forEach(button => { button.disabled = pending || !bounds || (!!button.dataset.move && Date.now() < nextMove); });
    root.querySelector<HTMLElement>("#init-domain")!.textContent = bounds?.domain === "simulated" ? "SIMULATED" : "";
  }
  function loadLimits(value: Bounds) {
    bounds = value;
    if (!dirty) for (const id of joints) {
      input(id, "min").value = String(value.joints[id]?.minimum?.requested_us ?? "");
      input(id, "max").value = String(value.joints[id]?.maximum?.requested_us ?? "");
    }
    render();
  }
  async function end() {
    generation++;
    token = null;
    if (status?.reserved && status.stage !== "awaiting_power_off") {
      try { status = await request<Status>("stop", { trial_id: status.trial_id }); }
      catch (value) { fail(value); }
    }
  }
  async function action(work: () => Promise<void>) {
    if (pending) return;
    pending = true; error.hidden = true; render();
    try { await heartbeat; await work(); }
    catch (value) { fail(value); await end(); }
    finally { pending = false; render(); }
  }
  async function command(path: string, body: object = {}) {
    const epoch = generation;
    const result = await request<Status>(path, { token, sequence: ++sequence, ...body });
    if (epoch === generation) status = result;
  }
  async function acquire(id: string) {
    const epoch = ++generation;
    status = await request<Status>("status");
    let result: Session;
    if (status.reserved && status.can_resume_powered) {
      result = await request<Session>("resume", { trial_id: status.trial_id });
    } else {
      const draft = await request<components["schemas"]["CommissionDraft"]>("drafts", {
        assembly_id: bounds!.assembly_id, observer: "Operator",
      });
      result = await request<Session>("initialize", { draft_id: draft.id, joint_id: id,
        conditions: "Direct pulse entry" });
    }
    status = result.status;
    if (epoch !== generation || !visible || document.hidden || !document.hasFocus()) {
      await request("stop", { trial_id: result.status.trial_id });
      return false;
    }
    token = result.token; sequence = 0;
    return true;
  }
  for (const id of joints) {
    for (const kind of ["min", "max"]) input(id, kind).oninput = () => {
      dirty = true; input(id, "max").setCustomValidity("");
      root.querySelector<HTMLButtonElement>("#init-save")!.textContent = "Save limits";
    };
    root.querySelector<HTMLButtonElement>(`[data-move="${id}"]`)!.onclick = () => {
      const field = input(id, "pulse");
      if (!field.reportValidity()) return;
      const target = field.valueAsNumber;
      void action(async () => {
        if (!token && !await acquire(id)) return;
        await command("position", { joint_id: id, pulse_us: target, assume_ok: true });
        nextMove = Date.now() + 260; setTimeout(render, 270);
      });
    };
  }
  root.querySelector<HTMLFormElement>("#init-form")!.onsubmit = event => {
    event.preventDefault();
    const changes: Record<string, { minimum_us?: number; maximum_us?: number }> = {};
    for (const id of joints) {
      const min = input(id, "min"), max = input(id, "max");
      max.setCustomValidity(min.value && max.value && min.valueAsNumber >= max.valueAsNumber ? "Maximum must be above minimum." : "");
      if (!min.reportValidity() || !max.reportValidity()) return;
      const values: { minimum_us?: number; maximum_us?: number } = {};
      if (min.value) values.minimum_us = min.valueAsNumber;
      if (max.value) values.maximum_us = max.valueAsNumber;
      if (Object.keys(values).length) changes[id] = values;
    }
    if (!Object.keys(changes).length) return;
    void action(async () => {
      if (token) await end();
      const value = await request<Bounds>("bounds", {
        assembly_id: bounds!.assembly_id, setup: bounds!.setup,
        joints: changes,
      });
      dirty = false; loadLimits(value);
      root.querySelector<HTMLButtonElement>("#init-save")!.textContent = "Saved";
    });
  };
  async function poll() {
    if (!visible || polling || pending) return;
    polling = true;
    try { loadLimits(await request<Bounds>("bounds")); }
    catch (value) { fail(value); }
    finally { polling = false; }
  }
  setInterval(() => {
    if (token && visible && !pending && !heartbeat) heartbeat = (async () => {
      try { await command("heartbeat"); }
      catch (value) { fail(value); await end(); }
      finally { heartbeat = null; }
    })();
  }, 100);
  setInterval(() => { void poll(); }, 1500);
  window.addEventListener("blur", () => { if (token) void end(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && token) void end(); });
  window.addEventListener("pagehide", () => { if (token) void end(); });
  render();
  return {
    end,
    setVisible(value: boolean) { visible = value; if (!value && token) void end(); else if (value) void poll(); },
    updateWorkbench(value: Workbench | null) { if (!value && token) void end(); },
  };
}
