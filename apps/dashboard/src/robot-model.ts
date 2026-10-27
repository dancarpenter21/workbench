import type { components } from "@workbench/contracts";
import { illustration, measurementDiagram, orientationGuide } from "./measurement-guide";
import { channelLabel } from "./arm-guide";

type State = components["schemas"]["RobotModelMeasurements"];
type Vector = (number | null)[] | null;
type Link = { name: string; size?: Vector; xyz?: Vector; rpy?: Vector };
type Joint = { name: string; type: string; parent: string; child: string; channel?: number;
  xyz: Vector; rpy: Vector; axis?: Vector; lower?: number | null; upper?: number | null;
  velocity?: number | null; effort?: number | null };
type Model = { revision: string; complete: boolean; domain: string; links: Link[]; joints: Joint[] };

export function mountRobotModel(root: HTMLElement) {
  root.innerHTML = `
    <h3>ROS robot model</h3>
    <p>Define the arm as connected links and joint axes. MoveIt uses this model for table, obstacle, and self-collision checks.</p>
    <p class="caption">Enter lengths in inches and angles in degrees. Values are converted to metres and radians internally; saved model exports use those internal units. Each joint origin is measured in its parent link's frame; each collision box has its own dimensions and a pose relative to its link. The axis is a unit vector in the joint frame. Values from the photo or the simulated model are not physical measurements.</p>
    <p id="robot-model-status" role="status">Loading robot model…</p>
    <form id="robot-model-form" novalidate>
      <label>Model revision<input id="robot-model-revision" type="text" /></label>
      <section id="measurement-dimensions" class="measurement-step" tabindex="-1">
        <h3>2. Measure each link’s collision box</h3>
        <p>At your recorded zero pose, align a box with each component: box X along its length, box Y across its width, and box Z through its thickness. Measure the full extent along these box axes, including its servo and brackets. A component tilted upward can have a long, narrow box tilted with it; you do not need a large table-aligned box.</p>
        ${illustration("box")}${measurementDiagram("box")}${measurementDiagram("oriented-box")}
        <p><strong>Three different measurements:</strong> size is length/width/thickness along the box’s own axes; centre is a signed X/Y/Z offset from the link origin expressed in the link’s axes; box roll/pitch/yaw rotates the box axes relative to the link. This matches URDF’s collision origin and box size. A zero box rotation aligns it with the link, not necessarily with the table.</p>
        <details><summary>Worked example: a box following a tilted component</summary>
          <p>Example only: a 10 × 2 × 1 inch component slopes 30° upward from link +X in the X/Z plane. Put box X along its length and box Y along link +Y. Set box roll 0°, pitch −30°, yaw 0°. If the link origin is at the centre of its starting end, the box centre is (5 cos 30°, 0, 5 sin 30°) ≈ (4.3301, 0, 2.5) inches. Its size remains (10, 2, 1), not its projection onto the table.</p>
          <p>For any orientation, <code>p_link = box_centre + R_box × p_box</code>. If measuring edge minima/maxima in box coordinates, size = max − min, and the midpoint must be rotated into link coordinates before entering the centre. Only aligned frames let you copy those midpoint coordinates directly.</p>
          <p>Box RPY uses the same rotation convention explained in step 3, but changes only the shape’s orientation. It does not change the joint axis or child-link frame. See the <a href="https://github.com/ros/urdf_tutorial/blob/ros2/urdf/07-physics.urdf" target="_blank" rel="noreferrer">ROS collision-origin example</a>.</p>
        </details>
        ${illustration("claw")}
        <p>The claw box must conservatively enclose the fingers and mechanism throughout opening and closing. Record its envelope relative to the claw link at zero; do not copy jaw gap as box width. tool0 is your gripping datum and has no box.</p>
        <div id="robot-model-links"></div>
      </section>
      <section id="measurement-orientation" class="measurement-step" tabindex="-1">
        <h3>3. Locate pivots and orient the joint frames</h3>
        ${orientationGuide()}<div id="robot-model-joints"></div>
      </section>
      <p class="caption">tool0 is the tool datum, with no collision box. Include the moving claw's full envelope in its collision geometry. Joint angles and PWM pulses need a separate measured calibration before physical execution.</p>
      <div id="robot-model-actions" class="actions"><button type="submit" form="robot-model-form">Save model draft</button>
        <button id="robot-model-validate" type="button" class="secondary">Validate complete model</button>
        <button id="robot-model-export" type="button" class="secondary">Export saved model</button>
        <button id="robot-model-reload" type="button" class="secondary">Reload saved model</button></div>
    </form>
    <p class="caption">Saving and validation never move the arm or enable physical retrieval. Physical calibration, uncertainty measurements, and commissioning are still required.</p>
    <p id="robot-model-error" role="alert" hidden></p>`;
  const el = <T extends HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  const actionButtons = [...root.querySelectorAll<HTMLButtonElement>("#robot-model-actions button")];
  let state: State | null = null;
  let model: Model | null = null;
  let pending = false;
  let dirty = false;
  const readers: (() => void)[] = [];

  function scalar(parent: HTMLElement, label: string, value: number | null | undefined, save: (value: number | null) => void, accessibleLabel = label, unitsToInternal = 1) {
    const field = document.createElement("label"); field.textContent = label;
    const input = document.createElement("input"); input.type = "number"; input.step = "any";
    input.setAttribute("aria-label", accessibleLabel); input.value = value == null ? "" : String(value / unitsToInternal);
    const originalInput = input.value;
    const originalValue = value ?? null;
    field.append(input); parent.append(field);
    readers.push(() => {
      const value = input.value.trim() === "" ? null : Number(input.value);
      if (input.validity.badInput || (value !== null && !Number.isFinite(value))) throw new Error(`${label}: enter a finite number`);
      save(input.value === originalInput ? originalValue : value === null ? null : value * unitsToInternal);
    });
  }
  function vector(parent: HTMLElement, label: string, values: Vector | undefined, save: (value: Vector) => void, unitsToInternal = 1, axes = ["X", "Y", "Z"]) {
    const group = document.createElement("fieldset"); group.className = "geometry-vector";
    const legend = document.createElement("legend"); legend.textContent = label; group.append(legend);
    const result = [...(values ?? [null, null, null])];
    axes.forEach((axis, index) => scalar(group, axis, result[index], value => { result[index] = value; }, `${label} ${axis}`, unitsToInternal));
    readers.push(() => save([...result])); parent.append(group);
  }
  function render() {
    actionButtons.forEach(button => { button.disabled = pending || !state; });
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
      const help = document.createElement("p"); help.className = "caption";
      const attachment = model!.joints.find(joint => joint.child === link.name);
      help.textContent = `${link.name === "base_link" ? "Origin: base axis at the table surface." : `Origin: ${attachment?.name ?? "attaching joint"} pivot at zero.`} Size: measure along the box’s own length/width/thickness axes. Centre: measure from this origin along the link axes, in inches. Box rotation: orient the box relative to the link in degrees. Include brackets and servo bodies.${link.name === "claw_link" ? " Include the full finger sweep, not just the open gap." : ""}`;
      card.append(help);
      vector(card, `${link.name} box size (inches)`, link.size, value => { link.size = value; }, 0.0254);
      vector(card, `${link.name} box centre (inches)`, link.xyz, value => { link.xyz = value; }, 0.0254);
      const originalRotation = link.rpy;
      vector(card, `${link.name} box rotation (degrees)`, link.rpy === undefined ? [0, 0, 0] : link.rpy, value => {
        // Preserve omitted legacy rotations when the operator leaves them unchanged.
        if (originalRotation === undefined && value?.every(component => component === 0)) delete link.rpy;
        else link.rpy = value;
      }, Math.PI / 180, ["Roll about X", "Pitch about Y", "Yaw about Z"]);
      if (originalRotation === undefined) {
        const legacy = document.createElement("p"); legacy.className = "caption";
        legacy.textContent = "Saved box has no rotation field: 0° / 0° / 0° preserves its existing link-aligned orientation.";
        card.append(legacy);
      }
      el("robot-model-links").append(card);
    });
    model.joints.forEach(joint => {
      const card = document.createElement("details"); card.className = "geometry-card";
      const title = document.createElement("summary"); title.textContent = joint.channel == null ? joint.name : `${channelLabel(joint.channel)} · ${joint.name}`; card.append(title);
      if (joint.channel != null) card.dataset.modelChannel = String(joint.channel);
      const chain = document.createElement("p");
      chain.textContent = `${joint.parent} → ${joint.child}. Start at ${joint.parent}’s marked origin; measure to ${joint.type === "fixed" ? "the tool datum" : "this pivot centre"} along the parent’s axes at zero. Enter signed offsets in inches. Rotation describes the joint frame at zero, not servo position.`;
      card.append(chain);
      vector(card, `${joint.name} origin (inches)`, joint.xyz, value => { joint.xyz = value; }, 0.0254);
      vector(card, `${joint.name} roll / pitch / yaw (degrees)`, joint.rpy, value => { joint.rpy = value; }, Math.PI / 180, ["Roll about X", "Pitch about Y", "Yaw about Z"]);
      if (joint.type !== "fixed") {
        const help = document.createElement("p"); help.className = "caption";
        help.textContent = "Axis: follow the rotation shaft in the joint frame and use the right-hand rule. Limits: measured signed travel relative to zero, not absolute shaft degrees. Velocity and effort require verified limits for this assembly; leave unknown values blank.";
        card.append(help);
        vector(card, `${joint.name} axis`, joint.axis, value => { joint.axis = value; });
        scalar(card, `${joint.name} lower limit (degrees)`, joint.lower, value => { joint.lower = value; }, undefined, Math.PI / 180);
        scalar(card, `${joint.name} upper limit (degrees)`, joint.upper, value => { joint.upper = value; }, undefined, Math.PI / 180);
        scalar(card, `${joint.name} velocity limit (degrees/s)`, joint.velocity, value => { joint.velocity = value; }, undefined, Math.PI / 180);
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
  return {
    selectChannel(channel: number) {
      const card = root.querySelector<HTMLDetailsElement>(`[data-model-channel="${channel}"]`);
      if (!card) return false;
      root.closest("#commissioning-panel")?.querySelectorAll(".arm-part-selected").forEach(item => item.classList.remove("arm-part-selected"));
      card.open = true; card.classList.add("arm-part-selected");
      card.scrollIntoView({ block: "start" }); card.querySelector("summary")!.focus({ preventScroll: true });
      return true;
    },
    setVisible(value: boolean) { if (value && !state) void refresh(); },
  };
}
