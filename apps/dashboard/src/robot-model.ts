import type { components } from "@workbench/contracts";
import { channelLabel } from "./arm-guide";

type State = components["schemas"]["RobotModelMeasurements"];
type Vector = (number | null)[] | null;
type Link = { name: string; size?: Vector; xyz?: Vector };
type Joint = { name: string; type: string; parent: string; child: string; channel?: number;
  xyz: Vector; rpy: Vector; axis?: Vector; lower?: number | null; upper?: number | null;
  velocity?: number | null; effort?: number | null };
type Model = { revision: string; complete: boolean; domain: string; links: Link[]; joints: Joint[] };

export function mountRobotModel(root: HTMLElement) {
  root.innerHTML = `
    <h3>ROS robot model</h3>
    <p>Define the arm as connected links and joint axes. MoveIt uses this model for table, obstacle, and self-collision checks.</p>
    <p class="caption">Use metres and radians. Each joint origin is measured in its parent link's frame; each collision box is measured in its own link's frame. The axis is a unit vector in the joint frame. Values from the photo or the simulated model are not physical measurements.</p>
    <p id="robot-model-status" role="status">Loading robot model…</p>
    <form id="robot-model-form" novalidate>
      <label>Model revision<input id="robot-model-revision" type="text" /></label>
      <h4>Link collision boxes</h4><div id="robot-model-links"></div>
      <h4>Joint origins and limits</h4><div id="robot-model-joints"></div>
      <p class="caption">tool0 is the tool datum, with no collision box. Include the moving claw's full envelope in its collision geometry. Joint angles and PWM pulses need a separate measured calibration before physical execution.</p>
      <div class="actions"><button type="submit">Save model draft</button>
        <button id="robot-model-validate" type="button" class="secondary">Validate complete model</button>
        <button id="robot-model-export" type="button" class="secondary">Export saved model</button>
        <button id="robot-model-reload" type="button" class="secondary">Reload saved model</button></div>
    </form>
    <p class="caption">Saving and validation never move the arm or enable physical retrieval. Physical calibration, uncertainty measurements, and commissioning are still required.</p>
    <p id="robot-model-error" role="alert" hidden></p>`;
  const el = <T extends HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  let state: State | null = null;
  let model: Model | null = null;
  let pending = false;
  let dirty = false;
  const readers: (() => void)[] = [];

  function scalar(parent: HTMLElement, label: string, value: number | null | undefined, save: (value: number | null) => void, accessibleLabel = label) {
    const field = document.createElement("label"); field.textContent = label;
    const input = document.createElement("input"); input.type = "number"; input.step = "any";
    input.setAttribute("aria-label", accessibleLabel); input.value = value == null ? "" : String(value);
    field.append(input); parent.append(field);
    readers.push(() => {
      const value = input.value.trim() === "" ? null : Number(input.value);
      if (input.validity.badInput || (value !== null && !Number.isFinite(value))) throw new Error(`${label}: enter a finite number`);
      save(value);
    });
  }
  function vector(parent: HTMLElement, label: string, values: Vector | undefined, save: (value: Vector) => void) {
    const group = document.createElement("fieldset"); group.className = "geometry-vector";
    const legend = document.createElement("legend"); legend.textContent = label; group.append(legend);
    const result = [...(values ?? [null, null, null])];
    ["X", "Y", "Z"].forEach((axis, index) => scalar(group, axis, result[index], value => { result[index] = value; }, `${label} ${axis}`));
    readers.push(() => save([...result])); parent.append(group);
  }
  function render() {
    root.querySelectorAll<HTMLButtonElement>("button").forEach(button => { button.disabled = pending || !state; });
    if (!state) return;
    el("robot-model-status").textContent = `${state.domain === "simulated" ? "Simulated model" : "Physical measurements"} · saved revision ${state.revision}. ${dirty ? "Unsaved changes." : state.valid ? "Structurally valid; physical execution remains gated." : state.validation_error}`;
  }
  function fields() {
    if (!model) return;
    readers.length = 0;
    el<HTMLInputElement>("robot-model-revision").value = model.revision;
    el("robot-model-links").replaceChildren(); el("robot-model-joints").replaceChildren();
    model.links.filter(link => link.name !== "tool0").forEach(link => {
      const card = document.createElement("details"); card.className = "geometry-card";
      const title = document.createElement("summary"); title.textContent = link.name; card.append(title);
      vector(card, `${link.name} box size (m)`, link.size, value => { link.size = value; });
      vector(card, `${link.name} box centre (m)`, link.xyz, value => { link.xyz = value; });
      el("robot-model-links").append(card);
    });
    model.joints.forEach(joint => {
      const card = document.createElement("details"); card.className = "geometry-card";
      const title = document.createElement("summary"); title.textContent = joint.channel == null ? joint.name : `${channelLabel(joint.channel)} · ${joint.name}`; card.append(title);
      const chain = document.createElement("p"); chain.textContent = `${joint.parent} → ${joint.child}`; card.append(chain);
      vector(card, `${joint.name} origin (m)`, joint.xyz, value => { joint.xyz = value; });
      vector(card, `${joint.name} roll / pitch / yaw (rad)`, joint.rpy, value => { joint.rpy = value; });
      if (joint.type !== "fixed") {
        vector(card, `${joint.name} axis`, joint.axis, value => { joint.axis = value; });
        scalar(card, `${joint.name} lower limit (rad)`, joint.lower, value => { joint.lower = value; });
        scalar(card, `${joint.name} upper limit (rad)`, joint.upper, value => { joint.upper = value; });
        scalar(card, `${joint.name} velocity limit (rad/s)`, joint.velocity, value => { joint.velocity = value; });
        scalar(card, `${joint.name} effort limit (N m)`, joint.effort, value => { joint.effort = value; });
      }
      el("robot-model-joints").append(card);
    });
  }
  function fail(error: unknown) {
    el("robot-model-error").textContent = error instanceof Error ? error.message : "Robot model request failed";
    el("robot-model-error").hidden = false;
  }
  async function request(body?: unknown): Promise<State> {
    const response = await fetch("/api/coordinator/commissioning/robot-model", {
      method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message ?? "Robot model request failed");
    return result;
  }
  function load(value: State) { state = value; model = structuredClone(value.model) as unknown as Model; dirty = false; fields(); render(); }
  async function refresh() {
    if (pending || dirty) return;
    pending = true;
    try { load(await request()); } catch (error) { fail(error); }
    finally { pending = false; render(); }
  }
  async function save(complete: boolean) {
    if (pending || !state || !model) return;
    pending = true; render(); el("robot-model-error").hidden = true;
    try {
      readers.forEach(read => read()); model.revision = el<HTMLInputElement>("robot-model-revision").value.trim(); model.complete = complete;
      load(await request({ context_key: state.context_key, revision: state.revision, model }));
    } catch (error) { fail(error); }
    finally { pending = false; render(); }
  }
  el("robot-model-form").addEventListener("input", () => { dirty = true; render(); });
  el<HTMLFormElement>("robot-model-form").onsubmit = event => { event.preventDefault(); void save(false); };
  el<HTMLButtonElement>("robot-model-validate").onclick = () => { void save(true); };
  el<HTMLButtonElement>("robot-model-reload").onclick = () => { dirty = false; el("robot-model-error").hidden = true; void refresh(); };
  el<HTMLButtonElement>("robot-model-export").onclick = () => {
    if (!state) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.model, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `workbench-robot-model-${state.revision}.json`; link.click(); URL.revokeObjectURL(url);
  };
  render();
  return { setVisible(value: boolean) { if (value && !state) void refresh(); } };
}
