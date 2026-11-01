import type { components } from "@workbench/contracts";
import { illustration, measurementDiagram, orientationGuide } from "./measurement-guide";
import { mountModelPreview } from "./model-preview";

type State = components["schemas"]["RobotModelMeasurements"];
type Vector = (number | null)[] | null;
type Box = { name: string; size: Vector; xyz: Vector; rpy: Vector; margin_m: number | null; kind?: "table" | "obstacle" };
type Link = { boxes?: Box[]; name: string; size?: Vector; xyz?: Vector; rpy?: Vector };
type Joint = { name: string; type: string; parent: string; child: string; channel?: number;
  xyz: Vector; rpy: Vector; axis?: Vector; lower?: number | null; upper?: number | null;
  velocity?: number | null; effort?: number | null };
type ReferencePose = { name: string; joint_positions: Record<string, number>; observed_xyz: Vector; observed_rpy: Vector; position_tolerance_m: number | null; orientation_tolerance_rad: number | null };
type Model = { source_notes?: { reference_polyline_m?: number[][]; reference_polyline_label?: string; status?: string; unresolved?: string[] }; reference_poses?: ReferencePose[]; collision_exclusions?: { link1: string; link2: string; reason: string }[]; schema_version: number; scene?: Box[]; claw_envelope?: Box[]; revision: string; complete: boolean; domain: string; links: Link[]; joints: Joint[] };

export function mountRobotModel(root: HTMLElement) {
  root.innerHTML = `
    <h3>ROS robot model</h3>
    <p>Define measured links and joint axes for ROS export. Saving checks structure; use Check collisions to inspect the preview pose with MoveIt.</p>
    <p class="caption">Choose inches or millimetres for lengths; angles use degrees. Values are converted to metres and radians internally; saved model exports use those internal units. Each joint origin is measured in its parent link's frame; each collision box has its own dimensions and a pose relative to its link. The axis is a unit vector in the joint frame. Values from the photo or the simulated model are not physical measurements.</p>
    <p id="robot-model-status" role="status">Loading robot model…</p>
    <label>Length units<select id="robot-model-units"><option value="inches">Inches</option><option value="mm">Millimetres</option></select></label>
    <label>Import model draft<input id="robot-model-import" type="file" accept="application/json,.json" /></label>
    <section id="robot-model-preview"></section>
    <form id="robot-model-form" novalidate>
      <button id="robot-model-upgrade" type="button">Add measured scene and multiple boxes</button>
      <p id="robot-model-source" class="caption"></p>
      <label>Model revision<input id="robot-model-revision" type="text" /></label>
      <section id="measurement-dimensions" class="measurement-step" tabindex="-1">
        <h3>1. Measure the five physical assemblies</h3>
        <p>Use the all-servos-neutral pose as the measurement reference. Measure the base, base-to-shoulder connector, upper arm, forearm, and claw assembly. The turntable belongs to the base mechanism; its rotating output carries the connector. The last assembly contains channels 1 and 0; its subparts move differently, so their geometry stays separate inside one card.</p>
        <details><summary>Identify the five colored components</summary>
          <img src="/images/dfrobot-v2-components-marked.jpg" width="600" height="600" style="max-width:100%;height:auto" alt="Operator-marked DFRobot arm: orange base, yellow base-to-shoulder connector above the turntable, green upper arm, blue forearm, red claw assembly." />
          <p>Orange: base housing with channel 5 inside. Yellow: rotating connector from the base turntable to the shoulder. Green: upper arm. Blue: forearm. Red: claw assembly. The photo identifies components; it is not a measured neutral pose or a set of collision boxes.</p>
        </details>
        <details><summary>Measurement guide: dimensions, offsets and box rotation</summary>
        <p>At your recorded zero pose, align a box with each component: box X along its length, box Y across its width, and box Z through its thickness. Measure the full extent along these box axes, including its servo and brackets. A component tilted upward can have a long, narrow box tilted with it; you do not need a large table-aligned box.</p>
        ${illustration("box")}${measurementDiagram("box")}${measurementDiagram("oriented-box")}
        <p><strong>Three different measurements:</strong> size is length/width/thickness along the box’s own axes; centre is a signed X/Y/Z offset from the link origin expressed in the link’s axes; box roll/pitch/yaw rotates the box axes relative to the link. This matches URDF’s collision origin and box size. A zero box rotation aligns it with the link, not necessarily with the table.</p>
        <details><summary>Worked example: a box following a tilted component</summary>
          <p>Example only: a 10 × 2 × 1 inch component slopes 30° upward from link +X in the X/Z plane. Put box X along its length and box Y along link +Y. Set box roll 0°, pitch −30°, yaw 0°. If the link origin is at the centre of its starting end, the box centre is (5 cos 30°, 0, 5 sin 30°) ≈ (4.3301, 0, 2.5) inches. Its size remains (10, 2, 1), not its projection onto the table.</p>
          <p>For any orientation, <code>p_link = box_centre + R_box × p_box</code>. If measuring edge minima/maxima in box coordinates, size = max − min, and the midpoint must be rotated into link coordinates before entering the centre. Only aligned frames let you copy those midpoint coordinates directly.</p>
          <p>Box RPY uses the same rotation convention explained in step 3, but changes only the shape’s orientation. It does not change the joint axis or child-link frame. See the <a href="https://github.com/ros/urdf_tutorial/blob/ros2/urdf/07-physics.urdf" target="_blank" rel="noreferrer">ROS collision-origin example</a>.</p>
        </details>
        ${illustration("claw")}
        <p>The claw envelope must conservatively enclose the fingers and mechanism throughout opening and closing. In schema version 2, record the full sweep relative to the rotating claw assembly; do not copy jaw gap as box width. The gripping reference point has no physical box.</p>
        </details><div id="robot-model-links"></div>
      </section>
      <section id="measurement-orientation" class="measurement-step" tabindex="-1">
        <h3>2. Locate pivots and orient joint frames</h3>
        <details><summary>Measurement guide: joint frames and axes</summary>${orientationGuide()}</details><div id="robot-model-joints"></div>
      </section>
      <section id="robot-model-environment"></section>
      <p class="caption">The gripping reference point belongs to the claw assembly; it is not a separate mount. Include the moving claw's full envelope in its collision geometry. Joint angles and PWM pulses need a separate measured calibration before physical execution.</p>
      <div id="robot-model-actions" class="actions"><button type="submit" form="robot-model-form">Save model draft</button>
        <button id="robot-model-validate" type="button" class="secondary">Check model structure</button>
        <button id="robot-model-inspect" type="button" class="secondary">Check collisions at preview pose</button>
        <button id="robot-model-bundle" type="button" class="secondary">Export ROS bundle</button>
        <button id="robot-model-export" type="button" class="secondary">Export saved model</button>
        <button id="robot-model-reload" type="button" class="secondary">Reload saved model</button></div>
    </form>
    <p class="caption">Saving and validation never move the arm or enable physical retrieval. Physical calibration, uncertainty measurements, and commissioning are still required.</p>
    <p id="robot-model-inspection" role="status">Pose not checked. No physical execution is authorized.</p>
    <p id="robot-model-error" role="alert" hidden></p>`;
  const el = <T extends HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  const actionButtons = [...root.querySelectorAll<HTMLButtonElement>("#robot-model-actions button")];
  let poseSequence = 0;
  const preview = mountModelPreview(el("robot-model-preview"), () => { poseSequence++; el("robot-model-inspection").textContent = "Preview pose changed; check collisions again."; }, name => {
    const part = root.querySelector<HTMLElement>(`[data-model-link="${name}"]`);
    const card = part?.closest<HTMLDetailsElement>("details[data-model-assembly]");
    if (!card) return;
    root.querySelectorAll(".arm-part-selected").forEach(item => item.classList.remove("arm-part-selected"));
    card.open = true; card.classList.add("arm-part-selected"); card.scrollIntoView({ block: "nearest" });
  });
  let state: State | null = null;
  let model: Model | null = null;
  let pending = false;
  let dirty = false;
  let lengthUnit = "inches";
  let lengthScale = 0.0254;
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
    root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("#robot-model-form input, #robot-model-form select, #robot-model-form button, #robot-model-import, #robot-model-units").forEach(input => { input.disabled = pending || !state; });
    if (!state) return;
    el("robot-model-status").textContent = `${state.domain === "simulated" ? "Simulated model" : "Physical measurements"} · saved revision ${state.revision}. ${dirty ? "Unsaved changes." : state.valid ? "Structurally valid; physical execution remains gated." : state.validation_error}`;
  }
  function collect() {
    readers.forEach(read => read());
    if (model) model.revision = el<HTMLInputElement>("robot-model-revision").value;
  }
  function editStructure(action: () => void) {
    if (pending) return;
    try { collect(); action(); dirty = true; previewSequence++; el("robot-model-inspection").textContent = "Measurements changed; inspect again."; fields(); render(); void updatePreview(); }
    catch (error) { fail(error); }
  }
  function boxFields(parent: HTMLElement, boxes: Box[], group: string) {
    const labelGroup = group === "hand_link" ? "Claw assembly" : group;
    boxes.forEach((box, index) => {
      const card = document.createElement("fieldset"); card.className = "geometry-card";
      const legend = document.createElement("legend"); legend.textContent = `${labelGroup} · ${box.name}`; card.append(legend);
      const label = document.createElement("label"); label.textContent = "Box name";
      const input = document.createElement("input"); input.value = box.name; label.append(input); card.append(label);
      readers.push(() => { box.name = input.value.trim(); });
      if (group === "scene") {
        const kindLabel = document.createElement("label"); kindLabel.textContent = "Object type";
        const kind = document.createElement("select"); kind.innerHTML = '<option value="table">Table</option><option value="obstacle">Obstacle</option>'; kind.value = box.kind ?? "obstacle";
        kindLabel.append(kind); card.append(kindLabel); readers.push(() => { box.kind = kind.value as "table" | "obstacle"; });
      }
      vector(card, `${labelGroup} ${index + 1} size (${lengthUnit})`, box.size, value => { box.size = value; }, lengthScale);
      vector(card, `${labelGroup} ${index + 1} centre (${lengthUnit})`, box.xyz, value => { box.xyz = value; }, lengthScale);
      vector(card, `${labelGroup} ${index + 1} rotation (degrees)`, box.rpy, value => { box.rpy = value; }, Math.PI / 180);
      scalar(card, `${labelGroup} ${index + 1} allowance (${lengthUnit})`, box.margin_m, value => { box.margin_m = value; }, undefined, lengthScale);
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove box";
      remove.onclick = () => editStructure(() => { boxes.splice(index, 1); }); card.append(remove); parent.append(card);
    });
    const add = document.createElement("button"); add.type = "button"; add.textContent = `Add ${group === "scene" ? "table / obstacle" : group === "claw_envelope" ? "claw envelope box" : "part box"}`;
    add.onclick = () => editStructure(() => {
      let serial = 1; while (boxes.some(box => box.name === `box_${serial}`)) serial++;
      boxes.push({ name: `box_${serial}`, size: null, xyz: null, rpy: null, margin_m: null, ...(group === "scene" ? { kind: "obstacle" as const } : {}) });
    }); parent.append(add);
  }
  function fields() {
    if (!model) return;
    readers.length = 0;
    const notes = model.source_notes;
    el("robot-model-source").textContent = notes ? `${typeof notes.status === "string" ? notes.status : "Imported measurement notes"}. ${Array.isArray(notes.unresolved) ? notes.unresolved.filter(value => typeof value === "string").join("; ") : ""}` : "";
    el<HTMLInputElement>("robot-model-revision").value = model.revision;
    el<HTMLButtonElement>("robot-model-upgrade").hidden = model.schema_version === 2;
    el("robot-model-environment").replaceChildren();
    el("robot-model-links").replaceChildren(); el("robot-model-joints").replaceChildren();
    const assemblies = [
      { name: "Base · orange", color: "#f5a623", links: ["base_link"], help: "The base bolts to the table and contains the channel 5 servo and turntable mechanism. Enter the stationary housing here. Parts that turn with the turntable output belong with the yellow connector below so their collision geometry rotates correctly." },
      { name: "Base-to-shoulder connector · yellow", color: "#ffdf00", links: ["turntable_link"], help: "The yellow bracket connects the base turntable output to the shoulder pivot. It turns with channel 5 and supports the upper arm at channel 4. Include the rotating plate, connector and any servo housing fixed to them. Measure its full dimensions; the turntable-axis-to-shoulder-pivot offset belongs in the Shoulder pivot card. Do not assume this connector is negligible." },
      { name: "Upper arm · green · channel 4", color: "#29b65a", links: ["upper_arm_link"], help: "The section from the shoulder pivot to the elbow pivot. The shoulder rotates about Y; confirmed pulse range: 600–2100 µs." },
      { name: "Forearm · blue · channel 3", color: "#19a8e0", links: ["forearm_link"], help: "The section from the elbow pivot to the wrist-bend pivot. Increasing elbow pulse width rotates about −Y; confirmed pulse range: 775–2500 µs." },
      { name: "Claw assembly · red · channels 2, 1 and 0", color: "#ed303b", links: ["wrist_link", "hand_link", "claw_link"], help: "Channel 2 bends the entire assembly at the forearm about +Y with increasing pulse width (500–2500 µs). The assembly is about 7 inches long and contains channel 1 for claw rotation about X and channel 0 for opening/closing. Measure the portions below according to what moves together; do not enter the full assembly length for each portion." },
    ];
    const labels: Record<string, string> = {
      base_link: "Base housing", turntable_link: "Base-to-shoulder connector", upper_arm_link: "Upper arm", forearm_link: "Forearm",
      wrist_link: "Housing before claw rotation", hand_link: "Claw assembly · rotating body (channel 1)", claw_link: "Fingers and opening mechanism",
    };
    const containers = new Map<string, HTMLElement>();
    for (const assembly of assemblies) {
      const card = document.createElement("details"); card.className = "geometry-card"; card.dataset.modelAssembly = assembly.links[0]; card.style.borderLeft = `4px solid ${assembly.color}`;
      const title = document.createElement("summary"); title.textContent = assembly.name; card.append(title);
      const help = document.createElement("p"); help.textContent = assembly.help; card.append(help);
      el("robot-model-links").append(card);
      for (const name of assembly.links) containers.set(name, card);
    }
    model.links.filter(link => link.name !== "tool0").forEach(link => {
      const card = document.createElement("section"); card.dataset.modelLink = link.name;
      const title = document.createElement("h4"); title.textContent = labels[link.name]; card.append(title);
      const help = document.createElement("p"); help.className = "caption";
      const attachment = model!.joints.find(joint => joint.child === link.name);
      help.textContent = `${link.name === "base_link" ? "Origin: base axis at the table surface." : `Origin: ${attachment?.name ?? "attaching joint"} pivot at zero.`} Size: measure along the box’s own length/width/thickness axes. Centre: measure from this origin along the link axes, in ${lengthUnit}. Box rotation: orient the box relative to the link in degrees. Include brackets and servo bodies.${link.name === "claw_link" ? " Include the full finger sweep, not just the open gap." : ""}`;
      card.append(help);
      if (model!.schema_version === 2) {
        if (link.name === "claw_link") help.textContent = "Measure one envelope enclosing the full opening/closing sweep, relative to the rotating claw assembly. This is part of the same claw assembly. The old rotating box is retained in JSON only.";
        else boxFields(card, link.boxes!, link.name);
        containers.get(link.name)!.append(card);
        if (link.name === "claw_link") boxFields(card, model!.claw_envelope!, "claw_envelope");
        return;
      }
      vector(card, `${labels[link.name] === "Claw assembly · rotating body (channel 1)" ? "Claw assembly" : link.name} box size (${lengthUnit})`, link.size, value => { link.size = value; }, lengthScale);
      vector(card, `${labels[link.name] === "Claw assembly · rotating body (channel 1)" ? "Claw assembly" : link.name} box centre (${lengthUnit})`, link.xyz, value => { link.xyz = value; }, lengthScale);
      const originalRotation = link.rpy;
      vector(card, `${labels[link.name] === "Claw assembly · rotating body (channel 1)" ? "Claw assembly" : link.name} box rotation (degrees)`, link.rpy === undefined ? [0, 0, 0] : link.rpy, value => {
        // Preserve omitted legacy rotations when the operator leaves them unchanged.
        if (originalRotation === undefined && value?.every(component => component === 0)) delete link.rpy;
        else link.rpy = value;
      }, Math.PI / 180, ["Roll about X", "Pitch about Y", "Yaw about Z"]);
      if (originalRotation === undefined) {
        const legacy = document.createElement("p"); legacy.className = "caption";
        legacy.textContent = "Saved box has no rotation field: 0° / 0° / 0° preserves its existing link-aligned orientation.";
        card.append(legacy);
      }
      containers.get(link.name)!.append(card);
    });
    if (model.schema_version === 2) {
      const environment = el("robot-model-environment");
      const heading = document.createElement("h3"); heading.textContent = "Table and obstacles"; environment.append(heading);
      const help = document.createElement("p"); help.textContent = "Measure table and obstacle centres and rotations relative to the fixed base. Allowances expand each face; unknown allowances remain blank. The claw sweep is entered inside the wrist / claw assembly above."; environment.append(help);
      boxFields(environment, model.scene!, "scene");
      const exclusions = document.createElement("details"); const title = document.createElement("summary"); title.textContent = "Review adjacent collision exclusions"; exclusions.append(title);
      const note = document.createElement("p"); note.textContent = "Every pair is checked by default. Exclude a pair only after reviewing its full travel and recording why contact at the mounting interface is intentional. Excluding adjacent links can also hide collisions away from their pivots."; exclusions.append(note);
      model.joints.filter(joint => joint.child !== "tool0").forEach(joint => {
        const row = document.createElement("label"); row.textContent = `${labels[joint.parent]} / ${labels[joint.child]}`;
        const enabled = document.createElement("input"); enabled.type = "checkbox";
        const matches = (pair: { link1: string; link2: string }) =>
          (pair.link1 === joint.parent && pair.link2 === joint.child) ||
          (pair.link2 === joint.parent && pair.link1 === joint.child);
        const existing = model!.collision_exclusions?.find(matches);
        enabled.checked = !!existing;
        const reason = document.createElement("input"); reason.placeholder = "Review reason required to exclude"; reason.setAttribute("aria-label", `${joint.name} collision exclusion reason`); reason.value = existing?.reason ?? "";
        row.append(enabled, reason); exclusions.append(row);
        readers.push(() => {
          model!.collision_exclusions = (model!.collision_exclusions ?? []).filter(pair => !matches(pair));
          if (enabled.checked) model!.collision_exclusions.push({ link1: joint.parent, link2: joint.child, reason: reason.value.trim() });
        });
      });
      environment.append(exclusions);
      const references = document.createElement("section"); const referenceTitle = document.createElement("h3"); referenceTitle.textContent = "Measured reference poses"; references.append(referenceTitle);
      const instructions = document.createElement("p"); instructions.textContent = "Set the simulated sliders to the independently measured joint angles, then capture the pose. Record tolerances before comparing results. Enter the observed tool position in the base frame and its roll/pitch/yaw. Commanded pulses alone are not angle measurements."; references.append(instructions);
      (model.reference_poses ?? []).forEach((pose, index) => {
        const card = document.createElement("fieldset"); card.className = "geometry-card";
        const label = document.createElement("label"); label.textContent = "Pose name"; const input = document.createElement("input"); input.value = pose.name; label.append(input); card.append(label); readers.push(() => { pose.name = input.value.trim(); });
        const angles = document.createElement("p"); angles.textContent = Object.entries(pose.joint_positions).map(([name, angle]) => `${name}: ${(angle * 180 / Math.PI).toFixed(1)}°`).join(" · "); card.append(angles);
        vector(card, `${pose.name} observed tool position (${lengthUnit})`, pose.observed_xyz, value => { pose.observed_xyz = value; }, lengthScale);
        vector(card, `${pose.name} observed tool RPY (degrees)`, pose.observed_rpy, value => { pose.observed_rpy = value; }, Math.PI / 180);
        scalar(card, `${pose.name} position tolerance (${lengthUnit})`, pose.position_tolerance_m, value => { pose.position_tolerance_m = value; }, undefined, lengthScale);
        scalar(card, `${pose.name} orientation tolerance (degrees)`, pose.orientation_tolerance_rad, value => { pose.orientation_tolerance_rad = value; }, undefined, Math.PI / 180);
        const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove reference pose"; remove.onclick = () => editStructure(() => { model!.reference_poses!.splice(index, 1); }); card.append(remove); references.append(card);
      });
      const capture = document.createElement("button"); capture.type = "button"; capture.textContent = "Capture preview angles as reference pose";
      capture.onclick = () => editStructure(() => {
        const positions = preview.positions();
        if (Object.keys(positions).length !== 6) throw new Error("Complete all joint frames and limits before capturing a pose");
        model!.reference_poses ??= [];
        let serial = 1; while (model!.reference_poses.some(pose => pose.name === `Pose ${serial}`)) serial++;
        model!.reference_poses.push({ name: `Pose ${serial}`, joint_positions: positions, observed_xyz: null, observed_rpy: null, position_tolerance_m: null, orientation_tolerance_rad: null });
      }); references.append(capture); environment.append(references);
    }
    const grip = model.joints.find(joint => joint.child === "tool0");
    if (grip) {
      const reference = document.createElement("details");
      const title = document.createElement("summary"); title.textContent = "Gripping reference point"; reference.append(title);
      const help = document.createElement("p"); help.textContent = "Choose the point between the fingers used to report claw position. Measure its offset from the claw-rotation pivot in the rotating assembly’s axes at neutral. This is a reference point, not another part or mount."; reference.append(help);
      vector(reference, `Gripping point offset (${lengthUnit})`, grip.xyz, value => { grip.xyz = value; }, lengthScale);
      vector(reference, "Gripping reference orientation (degrees)", grip.rpy, value => { grip.rpy = value; }, Math.PI / 180);
      containers.get("hand_link")!.append(reference);
    }
    const jointLabels: Record<number, string> = { 5: "Base turntable", 4: "Shoulder", 3: "Elbow", 2: "Claw assembly bend", 1: "Claw assembly rotation", 0: "Claw opening / closing" };
    model.joints.filter(joint => joint.child !== "tool0").forEach(joint => {
      const card = document.createElement("details"); card.className = "geometry-card";
      const title = document.createElement("summary"); title.textContent = joint.channel == null ? joint.name : `${jointLabels[joint.channel]} · channel ${joint.channel}`; card.append(title);
      if (joint.channel != null) card.dataset.modelChannel = String(joint.channel);
      const chain = document.createElement("p");
      chain.textContent = `${labels[joint.parent]} → ${labels[joint.child]}. Start at the ${labels[joint.parent].toLowerCase()}’s marked origin; measure to ${joint.type === "fixed" ? "the tool datum" : "this pivot centre"} along the parent’s axes at zero. Enter signed offsets in ${lengthUnit}. Rotation describes the joint frame at zero, not servo position.`;
      card.append(chain);
      if (joint.channel === 4) {
        const connector = document.createElement("p"); connector.textContent = "Yellow connector: measure from the base turntable’s rotation origin to the shoulder axle at neutral. This offset describes the connector’s position; its physical size is entered in the yellow geometry card."; card.append(connector);
      }
      vector(card, `${joint.name} origin (${lengthUnit})`, joint.xyz, value => { joint.xyz = value; }, lengthScale);
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
  let previewSequence = 0;
  async function updatePreview() {
    const sequence = ++previewSequence;
    if (!state || !model) return;
    const snapshot = structuredClone(model);
    try {
      const response = await fetch("/api/coordinator/commissioning/robot-model/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ context_key: state.context_key, revision: state.revision, model: snapshot }),
        signal: AbortSignal.timeout(5000),
      });
      const result = await response.json();
      if (sequence !== previewSequence) return;
      if (!response.ok) throw new Error(result.message ?? result.detail ?? "Preview unavailable");
      preview.update(snapshot, result);
    } catch (error) { if (sequence === previewSequence) fail(error); }
  }
  function load(value: State) { clearTimeout(previewTimer); el("robot-model-inspection").textContent = "Model loaded; inspect the preview pose again."; state = value; model = structuredClone(value.model) as unknown as Model; dirty = false; fields(); render(); void updatePreview(); }
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
  let previewTimer: ReturnType<typeof setTimeout>;
  el("robot-model-form").addEventListener("input", () => {
    dirty = true; previewSequence++; el("robot-model-inspection").textContent = "Measurements changed; inspect again."; render(); clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      try { readers.forEach(read => read()); if (model) void updatePreview(); }
      catch (error) { fail(error); }
    }, 200);
  });
  el<HTMLFormElement>("robot-model-form").onsubmit = event => { event.preventDefault(); void save(false); };
  el<HTMLButtonElement>("robot-model-validate").onclick = () => { void save(true); };
  el<HTMLButtonElement>("robot-model-reload").onclick = () => {
    if (dirty && !window.confirm("Discard unsaved model measurements and reload?")) return;
    dirty = false; el("robot-model-error").hidden = true; void refresh();
  };
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  el<HTMLButtonElement>("robot-model-export").onclick = () => {
    if (!state) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.model, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `workbench-robot-model-${state.revision}.json`; link.click(); URL.revokeObjectURL(url);
  };
  async function inspect(exportOnly: boolean) {
    if (!state || !model || pending) return;
    try { collect(); } catch (error) { fail(error); return; }
    const sequence = previewSequence;
    const inspectedPoseSequence = poseSequence;
    const positions = preview.positions();
    pending = true; render();
    el("robot-model-inspection").textContent = exportOnly ? "Preparing ROS bundle…" : "Checking pose with MoveIt…";
    try {
      const response = await fetch("/api/coordinator/commissioning/robot-model/inspect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ context_key: state.context_key, revision: state.revision, model, joint_positions: positions, check_collisions: !exportOnly }),
        signal: AbortSignal.timeout(20000),
      });
      const result = await response.json();
      if (sequence !== previewSequence || inspectedPoseSequence !== poseSequence) {
        el("robot-model-inspection").textContent = "Model or pose changed; inspect again."; return;
      }
      if (!response.ok) throw new Error(result.message ?? result.detail ?? "Inspection failed");
      if (result.errors.length) { el("robot-model-inspection").textContent = result.errors.join("; "); return; }
      if (exportOnly) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(result.bundle, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `workbench-ros-${result.model_hash.slice(0, 12)}.json`; link.click(); URL.revokeObjectURL(url);
        el("robot-model-inspection").textContent = "Exported measured model, URDF, scene and collision exclusions. Physical execution remains disabled.";
      } else if (result.available) {
        el("robot-model-inspection").textContent = `${result.in_bounds ? "Within joint limits" : "Outside joint limits"} · ${result.collision ? "Collision: " + result.colliding_pairs.map((pair: string[]) => pair.join(" / ")).join(", ") : "No collision at this pose"}. Tool XYZ (m): ${result.tool_xyz.map((v: number) => v.toFixed(4)).join(", ")}. This is not trajectory or physical approval. ${(result.bundle.reference_results ?? []).map((pose: { name: string; status: string; position_error_m?: number; orientation_error_rad?: number }) => `${pose.name}: ${pose.status}${pose.position_error_m == null ? "" : ` (${(pose.position_error_m * 1000).toFixed(2)} mm, ${(pose.orientation_error_rad! * 180 / Math.PI).toFixed(2)}°)`}`).join("; ")}`;
      } else { el("robot-model-inspection").textContent = "Collision validation unavailable."; }
    } catch (error) { fail(error); el("robot-model-inspection").textContent = "Inspection unavailable; pose not validated."; }
    finally { pending = false; render(); }
  }
  el<HTMLButtonElement>("robot-model-inspect").onclick = () => { void inspect(false); };
  el<HTMLButtonElement>("robot-model-bundle").onclick = () => { void inspect(true); };
  el<HTMLButtonElement>("robot-model-upgrade").onclick = () => editStructure(() => {
    if (!model || model.schema_version === 2) return;
    model.schema_version = 2; model.complete = false; model.scene = []; model.claw_envelope = []; model.collision_exclusions = [];
    model.links.forEach(link => {
      link.boxes = ["tool0", "claw_link"].includes(link.name) ? [] : [{ name: "body", size: link.size ?? null, xyz: link.xyz ?? null, rpy: link.rpy === undefined ? [0, 0, 0] : link.rpy, margin_m: null }];
    });
  });
  el<HTMLSelectElement>("robot-model-units").onchange = event => {
    try {
      readers.forEach(read => read());
      if (model) model.revision = el<HTMLInputElement>("robot-model-revision").value;
      lengthUnit = (event.target as HTMLSelectElement).value;
      lengthScale = lengthUnit === "mm" ? 0.001 : 0.0254;
      fields();
    } catch (error) {
      el<HTMLSelectElement>("robot-model-units").value = lengthUnit;
      fail(error);
    }
  };
  el<HTMLInputElement>("robot-model-import").onchange = async event => {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file || !state || pending) return;
    if (dirty && !window.confirm("Replace unsaved measurements with the imported draft?")) { input.value = ""; return; }
    pending = true; render();
    try {
      if (file.size > 1_000_000) throw new Error("Model file must be smaller than 1 MB");
      const candidate = JSON.parse(await file.text());
      const response = await fetch("/api/coordinator/commissioning/robot-model/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ context_key: state.context_key, revision: state.revision, model: candidate }),
        signal: AbortSignal.timeout(5000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message ?? result.detail ?? "Invalid model file");
      previewSequence++; clearTimeout(previewTimer);
      el("robot-model-inspection").textContent = "Model imported; inspect the preview pose again.";
      model = candidate as Model; model.complete = false; dirty = true;
      fields(); preview.update(model, result);
    } catch (error) { fail(error); }
    finally { pending = false; input.value = ""; render(); }
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
    setVisible(value: boolean) { preview.setVisible(value); if (value && !state) void refresh(); },
  };
}
