import type { components } from "@workbench/contracts";
import { mountGeometry } from "./geometry";
import { mountRobotModel } from "./robot-model";
import { poseGuide, calibrationGuide, nominalText } from "./measurement-guide";
import { currentPresentation } from "./current";
import { armParts, channelLabel, mountArmGuide } from "./arm-guide";

type Bounds = components["schemas"]["CommissionBounds"];
type Status = components["schemas"]["CommissionStatus"];
type Session = components["schemas"]["CommissionSession"];
type Workbench = components["schemas"]["WorkbenchStatus"];
const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];
const label = (id: string) => channelLabel(armParts.find(part => part.joint === id)!.channel);

export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `
    <div class="panel-heading"><h2>Initialization</h2><span id="init-domain" class="caption"></span></div>
    <p>Work through the five steps, or jump to a section. Measurements remain in the form while you navigate; save drafts before leaving or reloading the page.</p>
    <nav class="measurement-navigation" aria-label="Initialization steps">
      <a href="#measurement-pose">1 · Position</a><a href="#measurement-dimensions">2 · Measure</a>
      <a href="#measurement-orientation">3 · Orient</a><a href="#measurement-calibration">4 · Calibrate</a>
      <a href="#measurement-review">5 · Review</a>
    </nav>
    ${poseGuide()}
    <section id="arm-guide" aria-label="Arm part guide"></section>
    <section id="robot-model-panel"></section>
    ${calibrationGuide()}
    <h4>Pulse positioning and optional limits</h4>
    <p class="caption">For capsule measurements, keep channels 1–5 at 1500 µs and enter your measured full-open pulse in the claw row. Move uses the current-monitored positioning flow. A new motion session first initializes all six servos at 1500 µs, then applies the requested pulse. Saving limits does not move the arm.</p>
    <p class="caption">Clear min / Clear max marks a saved endpoint for deletion; Save limits applies it. Undo restores the field before saving. Other empty fields leave saved limits unchanged. Cleared endpoints use the nominal 500 / 2500 µs fallback when Manual loads its configuration; these are not verified safe limits.</p>
    <form id="init-form" novalidate>
      <div class="init-values" role="table" aria-label="Joint pulse values in microseconds">
        <div class="init-row init-head" role="row"><span>Joint</span><span>Pulse μs</span><span>Min μs</span><span>Max μs</span><span></span></div>
        ${joints.map(id => `<div class="init-row" role="row" data-joint="${id}">
          <label for="init-${id}-pulse">${label(id)}</label>
          <input id="init-${id}-pulse" aria-label="${label(id)} pulse" type="number" min="500" max="2500" step="1" value="1500" required />
          <input id="init-${id}-min" aria-label="${label(id)} minimum" type="number" min="500" max="1500" step="1" />
          <input id="init-${id}-max" aria-label="${label(id)} maximum" type="number" min="1500" max="2500" step="1" />
          <button type="button" data-move="${id}">Move</button>
          <div class="limit-clear-actions"><button type="button" class="secondary" data-clear="${id}-min" aria-label="Clear ${label(id)} minimum">Clear min</button>
            <button type="button" class="secondary" data-clear="${id}-max" aria-label="Clear ${label(id)} maximum">Clear max</button></div>
          <output id="init-${id}-nominal" class="nominal-angle" for="init-${id}-pulse" aria-live="polite"></output>
        </div>`).join("")}
      </div>
      <button id="init-save" type="submit">Save limits</button>
    </form>
    <p id="init-error" role="alert" hidden></p>
    <details id="legacy-geometry"><summary>Advanced · Existing supervised capsule guard</summary>
      <p>This optional guard uses fixed table coordinates in millimetres. Its measured calibration is separate from the ROS model above.</p>
      <section id="geometry-panel"></section></details>
    <section id="measurement-review" class="measurement-step" tabindex="-1">
      <h3>5. Review and save</h3>
      <p>Check the reference pose, units, signed offsets, rotation axes and the claw’s complete envelope. Unknown values should remain blank. Save an incomplete draft while measuring; validate only after every required value is measured.</p>
      <p>Model drafts, capsule measurements and pulse limits save separately. Structural validation does not establish physical acceptance or enable retrieval. Manufacturer angle estimates never replace measured calibration.</p>
      <div id="measurement-review-actions"></div>
    </section>`;
  const geometry = mountGeometry(root.querySelector<HTMLElement>("#geometry-panel")!, (name, reveal) => guide.showPart(name, reveal));
  const robotModel = mountRobotModel(root.querySelector<HTMLElement>("#robot-model-panel")!);
  const guide = mountArmGuide(root.querySelector<HTMLElement>("#arm-guide")!, name => {
    const part = armParts.find(part => part.storedName === name);
    return part ? robotModel.selectChannel(part.channel) : false;
  });
  root.querySelector("#measurement-review-actions")!.append(root.querySelector("#robot-model-actions")!);
  root.querySelectorAll<HTMLAnchorElement>(".measurement-navigation a").forEach(link => {
    link.onclick = event => {
      event.preventDefault();
      const target = root.querySelector<HTMLElement>(link.hash)!;
      target.scrollIntoView({ block: "start" });
      target.focus({ preventScroll: true });
      root.querySelectorAll(".measurement-navigation a").forEach(item => item.removeAttribute("aria-current"));
      link.setAttribute("aria-current", "step");
    };
  });
  const input = (id: string, kind: string) => root.querySelector<HTMLInputElement>(`#init-${id}-${kind}`)!;
  const error = root.querySelector<HTMLElement>("#init-error")!;
  const clearedLimits = new Map<string, string>();
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
  let errorMessage = "";
  let powerOff = false;

  function renderError() {
    error.textContent = powerOff && errorMessage.startsWith("Stale INA219 readings")
      ? "Powered off. A previous monitoring interruption requires a reset before motion."
      : errorMessage;
  }

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
    errorMessage = value instanceof Error ? value.message : "Request failed";
    renderError();
    error.hidden = false;
  }
  function render() {
    root.querySelectorAll<HTMLButtonElement>("#init-form button").forEach(button => { button.disabled = pending || !bounds || (!!button.dataset.move && Date.now() < nextMove); });
    root.querySelectorAll<HTMLInputElement>("#init-form input").forEach(field => { field.disabled = pending || !bounds; });
    root.querySelectorAll<HTMLButtonElement>("[data-clear]").forEach(button => {
      const key = button.dataset.clear!;
      const kind = key.endsWith("-min") ? "min" : "max";
      button.textContent = `${clearedLimits.has(key) ? "Undo clear" : "Clear"} ${kind}`;
      button.setAttribute("aria-pressed", String(clearedLimits.has(key)));
      input(key.slice(0, -4), kind).placeholder = clearedLimits.has(key) ? "Delete on save" : "";
    });
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
    const pulse = input(id, "pulse");
    const updateNominal = () => {
      const channel = armParts.find(part => part.joint === id)!.channel;
      root.querySelector<HTMLOutputElement>(`#init-${id}-nominal`)!.textContent = nominalText(channel, pulse.valueAsNumber);
    };
    pulse.addEventListener("input", updateNominal); updateNominal();
    for (const kind of ["min", "max"]) {
      const field = input(id, kind);
      const markDirty = () => {
        dirty = true; input(id, "max").setCustomValidity("");
        root.querySelector<HTMLButtonElement>("#init-save")!.textContent = "Save limits";
        render();
      };
      field.oninput = () => { clearedLimits.delete(`${id}-${kind}`); markDirty(); };
      root.querySelector<HTMLButtonElement>(`[data-clear="${id}-${kind}"]`)!.onclick = () => {
        const key = `${id}-${kind}`;
        if (clearedLimits.has(key)) { field.value = clearedLimits.get(key)!; clearedLimits.delete(key); }
        else { clearedLimits.set(key, field.value); field.value = ""; }
        markDirty();
      };
    }
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
    const changes: Record<string, Partial<components["schemas"]["CommissionLimitValues"]>> = {};
    for (const id of joints) {
      const min = input(id, "min"), max = input(id, "max");
      max.setCustomValidity(min.value && max.value && min.valueAsNumber >= max.valueAsNumber ? "Maximum must be above minimum." : "");
      if (!min.reportValidity() || !max.reportValidity()) return;
      const values: Partial<components["schemas"]["CommissionLimitValues"]> = {};
      if (min.value) values.minimum_us = min.valueAsNumber;
      if (max.value) values.maximum_us = max.valueAsNumber;
      if (clearedLimits.has(`${id}-min`)) values.clear_minimum = true;
      if (clearedLimits.has(`${id}-max`)) values.clear_maximum = true;
      if (Object.keys(values).length) changes[id] = values;
    }
    if (!Object.keys(changes).length) return;
    void action(async () => {
      if (token) await end();
      const value = await request<Bounds>("bounds", {
        assembly_id: bounds!.assembly_id, setup: bounds!.setup,
        joints: changes,
      });
      dirty = false; clearedLimits.clear(); loadLimits(value);
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
    updateCurrent(sample: Workbench["claw_current"]) {
      powerOff = currentPresentation(sample).power === "off";
      renderError();
    },
    setVisible(value: boolean) { visible = value; geometry.setVisible(value); robotModel.setVisible(value); if (!value && token) void end(); else if (value) void poll(); },
    updateWorkbench(value: Workbench | null) { if (!value && token) void end(); },
  };
}
