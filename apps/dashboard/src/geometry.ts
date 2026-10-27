import type { components } from "@workbench/contracts";
import { illustration, measurementDiagram, nominalText } from "./measurement-guide";
import { channelLabel, knownPart } from "./arm-guide";

type State = components["schemas"]["GeometryMeasurements"];
type Vec = (number | null)[] | null;
type Capsule = { name: string; parent_channel: number; a_mm: Vec; b_mm: Vec; radius_mm: number | null };
type Joint = { channel: number; origin_mm: Vec; axis: Vec; pulse_angle_deg: (number | null)[][]; error_deg: number | null };
type Geometry = { schema_version: number; revision: string; domain: string; calibrated: boolean;
  reference_pulse_us: number; base_axis_perpendicular_to_table: boolean; claw_envelope: string;
  clearance_mm: number | null; geometry_error_mm: number | null; max_boxes: number;
  capsules: Capsule[]; joints: Joint[] };

export function mountGeometry(root: HTMLElement, showPart: (name: string, reveal: boolean) => void) {
  root.innerHTML = `
    <h3>Capsule measurements · fixed table frame</h3>
    ${measurementDiagram("capsule")}
    <p>Choose A and B along an imaginary centre line through the part. They are the centres of the rounded caps, not the outermost tips. Measure their X/Y offsets from the base axis with a square and their Z heights from the tabletop, all in mm. Choose a radius covering the farthest bracket, servo or finger from the line, including beyond its ends.</p>
    ${illustration("claw")}
    <p>Match each card to the same channel and servo on the photo. Use Show on image to find it above. Photo labels open the primary ROS joint cards; capsule coordinates here use the fixed table frame.</p>
    <p class="caption">Base (servo 6 / channel 5) has no capsule here because its rotation does not change height. The claw (servo 1 / channel 0) has its own capsule; it follows the claw mount on channel 1 and must cover every claw opening.</p>
    <p>Measurement pose: keep servos 2–6 (channels 1–5) at 1500 µs. For the claw, use your measured full-open pulse for servo 1 (channel 0) in the pulse controls below; 1500 µs is not assumed to mean fully open.</p>
    <p class="caption">Measure the claw fully open, then ensure its capsule also encloses the fingers and mechanism throughout closing. Each capsule is a line segment with a radius large enough to enclose the part.</p>
    <p class="caption">Endpoint A and endpoint B are the two ends of an imaginary line through the part. The radius is the distance outward from that line needed to cover the entire part, including brackets and servo bodies. The photo above identifies parts; it does not supply dimensions.</p>
    <section class="geometry-frame" aria-label="Coordinate frame at base neutral">
      <h4>Coordinate frame · base neutral</h4>
      <svg viewBox="0 0 340 210" role="img" aria-label="Top view: positive X points right along the arm at base neutral; positive Y points up the drawing, counterclockwise from X; positive Z points out of the table toward the viewer.">
        <defs><marker id="frame-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
        <text x="170" y="18" text-anchor="middle">Top view · looking down at the table</text>
        <g fill="none" stroke="currentColor" stroke-width="2" marker-end="url(#frame-arrow)">
          <path d="M 60 150 H 300" /><path d="M 60 150 V 40" />
          <path d="M 115 150 A 55 55 0 0 0 60 95" />
        </g>
        <g class="geometry-frame-axis"><text x="304" y="154">+X</text><text x="48" y="34">+Y</text></g>
        <text x="126" y="105">Positive rotation: CCW</text>
        <circle cx="60" cy="150" r="8" fill="var(--panel)" stroke="currentColor" stroke-width="2" />
        <circle cx="60" cy="150" r="3" fill="currentColor" />
        <text x="20" y="178">(0, 0, 0)</text><text x="128" y="178">+Z toward you / upward</text>
        <text x="128" y="198">+X = base at 1500 µs</text>
      </svg>
      <dl>
        <dt>Origin (0, 0, 0)</dt><dd>Where the base rotation axis meets the table surface.</dd>
        <dt>+X</dt><dd>Along the arm’s reach projected onto the table when the base (servo 6 / channel 5) is at 1500 µs.</dd>
        <dt>+Y</dt><dd>90° counterclockwise from +X, viewed from above. Positive rotation about +Z turns +X toward +Y.</dd>
        <dt>+Z</dt><dd>Upward from the table. Table height is Z = 0; X × Y = Z.</dd>
      </dl>
      <p class="caption">Use millimeters for every endpoint and joint origin in this same fixed frame. The frame stays fixed when the base turns and is independent of the video feed. Base neutral sets the direction of reach; links can still have different heights and measured sideways offsets. Increasing the base pulse is not assumed to produce positive rotation.</p>
      <h4>Joint rotations at neutral</h4>
      <table class="geometry-axis-table">
        <thead><tr><th>Joint</th><th>Rotation axis</th></tr></thead>
        <tbody>
          <tr><td>Claw mount · servo 2 / CH 1</td><td>+X · (1, 0, 0)</td></tr>
          <tr><td>Wrist · servo 3 / CH 2</td><td>+Y · (0, 1, 0)</td></tr>
          <tr><td>Middle arm · servo 4 / CH 3</td><td>+Y · (0, 1, 0)</td></tr>
          <tr><td>Upper arm · servo 5 / CH 4</td><td>+Y · (0, 1, 0)</td></tr>
          <tr><td>Base · servo 6 / CH 5</td><td>+Z · (0, 0, 1)</td></tr>
        </tbody>
      </table>
      <p class="caption">Positive rotation follows the right-hand rule: about +X, +Y turns toward +Z; about +Y, +Z turns toward +X (and +X toward −Z). These are the axes at the neutral measurement pose; downstream axes move with upstream joints. Claw opening is covered by its capsule.</p>
    </section>
    <p class="caption">Include brackets, servo bodies, and the claw at every opening. Base/channel 5 does not change height. Jog remains free play.</p>
    <p id="geometry-status" role="status">Loading measurements…</p>
    <form id="geometry-form" novalidate>
      <div class="geometry-pair">
        <label>Required table gap (mm)<input id="geometry-clearance" type="number" min="0" step="any" /></label>
        <label>Geometry / sag allowance (mm)<input id="geometry-error-mm" type="number" min="0" step="any" /></label>
      </div>
      <div id="geometry-capsules"></div>
      <button id="geometry-add" type="button" class="secondary">Add part capsule</button>
      <details id="geometry-joint-details"><summary>Joint axes and angle calibration</summary>
        <p class="caption">The clearance calculation also needs each axis location and direction at startup. Angles are zero at 1500 µs. Enter measured pulse and angle pairs in the rows below. Include (1500 µs, 0°), with at least three samples in increasing pulse order and strictly increasing or strictly decreasing angles. Reference estimates never fill measured values. Positive angles follow the right-hand rule around the axis, regardless of whether pulse width increases or decreases. New drafts use the neutral axes above; existing saved axes remain as entered. Use a neutral-axis button to update that joint in this draft, keeping its measured angle signs consistent.</p>
        <div id="geometry-joints"></div>
      </details>
      <div class="actions"><button id="geometry-save" type="submit">Save measurements</button>
        <button id="geometry-activate" type="button" class="secondary">Activate for guarded movement</button>
        <button id="geometry-reload" type="button" class="secondary">Reload saved</button></div>
    </form>
    <p id="geometry-error" role="alert" hidden></p>`;
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  let state: State | null = null;
  let geometry: Geometry | null = null;
  let visible = false;
  let pending = false;
  let dirty = false;

  function numeric(parent: HTMLElement, label: string, value: number | null, id?: string) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = "number"; input.step = "any"; input.value = value == null ? "" : String(value);
    input.setAttribute("aria-label", label);
    if (id) input.id = id;
    wrapper.append(input); parent.append(wrapper);
    return input;
  }
  function vector(parent: HTMLElement, label: string, value: Vec, id: string) {
    const group = document.createElement("fieldset");
    group.className = "geometry-vector";
    const legend = document.createElement("legend"); legend.textContent = label;
    group.append(legend);
    ["x", "y", "z"].forEach((axis, index) => {
      const field = numeric(group, `${label} ${axis}`, value?.[index] ?? null, `${id}-${index}`);
      field.parentElement!.firstChild!.textContent = axis.toUpperCase();
    });
    parent.append(group);
  }
  function value(id: string) {
    const input = el<HTMLInputElement>(id);
    if (input.validity.badInput) throw new Error(`Enter a number for ${input.getAttribute("aria-label") ?? id}.`);
    return input.value === "" ? null : input.valueAsNumber;
  }
  function readVector(id: string) { return [0, 1, 2].map(index => value(`${id}-${index}`)); }
  function render() {
    root.querySelectorAll<HTMLButtonElement>("button").forEach(button => { button.disabled = pending || !state; });
    root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea").forEach(field => { field.disabled = pending || !state; });
    if (!state) return;
    const active = state.active_revision == null ? "No active geometry." : `Active geometry: revision ${state.active_revision}.`;
    el("geometry-status").textContent = dirty ? `Unsaved measurements. ${active}`
      : `${state.domain === "simulated" ? "SIMULATED · " : ""}Saved revision ${state.revision}. ${active} ${state.valid ? "Measurements are valid." : "Complete measurements before activation: " + state.validation_error}`;
  }
  function renderFields() {
    if (!geometry) return;
    el<HTMLInputElement>("geometry-clearance").value = geometry.clearance_mm == null ? "" : String(geometry.clearance_mm);
    el<HTMLInputElement>("geometry-error-mm").value = geometry.geometry_error_mm == null ? "" : String(geometry.geometry_error_mm);
    el("geometry-capsules").replaceChildren();
    geometry.capsules.forEach((capsule, index) => {
      const card = document.createElement("details"); card.className = "geometry-card"; card.open = index === 0;
      const part = knownPart(capsule.name, capsule.parent_channel);
      if (part) card.dataset.armPart = part.storedName!;
      const title = document.createElement("summary");
      title.textContent = part ? channelLabel(part.channel) : `Custom part · ${capsule.name}`;
      card.append(title);
      if (part) {
        const description = document.createElement("p"); description.className = "caption";
        description.textContent = part.description; card.append(description);
      }
      const nameLabel = document.createElement("label"); nameLabel.textContent = "Stored part name";
      const name = document.createElement("input"); name.id = `capsule-${index}-name`; name.value = capsule.name;
      name.setAttribute("aria-label", `Part ${index + 1} name`); nameLabel.append(name);
      const parentLabel = document.createElement("label"); parentLabel.textContent = "Last joint moving this part";
      const parent = document.createElement("select"); parent.id = `capsule-${index}-parent`;
      parent.setAttribute("aria-label", `Part ${index + 1} parent channel`);
      [4, 3, 2, 1].forEach(channel => parent.add(new Option(channelLabel(channel), String(channel))));
      parent.value = String(capsule.parent_channel); parentLabel.append(parent);
      const measurementLabel = part ? `${part.name} (CH ${part.channel})` : capsule.name;
      vector(card, `${measurementLabel} endpoint A (mm)`, capsule.a_mm, `capsule-${index}-a`);
      vector(card, `${measurementLabel} endpoint B (mm)`, capsule.b_mm, `capsule-${index}-b`);
      numeric(card, `${measurementLabel} radius (mm)`, capsule.radius_mm, `capsule-${index}-radius`);
      const currentPart = () => knownPart(name.value.trim(), Number(parent.value));
      const show = document.createElement("button"); show.type = "button"; show.className = "secondary";
      show.textContent = "Show on image"; show.hidden = !part;
      show.onclick = () => { const selected = currentPart(); if (selected) showPart(selected.storedName!, true); };
      card.insertBefore(show, card.querySelector("fieldset"));
      card.addEventListener("focusin", () => {
        root.closest("#commissioning-panel")?.querySelectorAll(".arm-part-selected").forEach(item => item.classList.remove("arm-part-selected"));
        const selected = currentPart();
        if (selected) card.classList.add("arm-part-selected");
        showPart(selected?.storedName ?? "", false);
      });
      const updateIdentity = () => {
        const selected = currentPart();
        title.textContent = selected ? channelLabel(selected.channel) : `Custom part · ${name.value.trim()}`;
        // Re-label existing controls without replacing them or losing unsaved values/focus.
        const prefix = selected ? `${selected.name} (CH ${selected.channel})` : name.value.trim();
        card.querySelectorAll(".geometry-vector").forEach((group, endpoint) => {
          const label = `${prefix} endpoint ${endpoint === 0 ? "A" : "B"} (mm)`;
          group.querySelector("legend")!.textContent = label;
          group.querySelectorAll("input").forEach((input, axis) => input.setAttribute("aria-label", `${label} ${["x", "y", "z"][axis]}`));
        });
        const radius = el<HTMLInputElement>(`capsule-${index}-radius`);
        radius.setAttribute("aria-label", `${prefix} radius (mm)`);
        radius.parentElement!.firstChild!.textContent = `${prefix} radius (mm)`;
        show.hidden = !selected;
        if (!selected) card.classList.remove("arm-part-selected");
        showPart(selected?.storedName ?? "", false);
      };
      name.addEventListener("input", updateIdentity);
      parent.addEventListener("change", updateIdentity);
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary";
      remove.textContent = "Remove part"; remove.onclick = () => {
        try { collect(); geometry!.capsules.splice(index, 1); dirty = true; renderFields(); render(); }
        catch (error) { fail(error); }
      };
      const advanced = document.createElement("details"); advanced.className = "capsule-mapping"; advanced.open = !part;
      const advancedTitle = document.createElement("summary"); advancedTitle.textContent = "Advanced: stored name and moving joint";
      advanced.append(advancedTitle, nameLabel, parentLabel, remove);
      card.append(advanced); el("geometry-capsules").append(card);
    });
    el("geometry-joints").replaceChildren();
    geometry.joints.forEach((joint, index) => {
      const card = document.createElement("fieldset"); card.className = "geometry-card";
      const title = document.createElement("legend"); title.textContent = channelLabel(joint.channel); card.append(title);
      vector(card, `Channel ${joint.channel} axis origin (mm)`, joint.origin_mm, `geometry-joint-${index}-origin`);
      vector(card, `Channel ${joint.channel} axis direction`, joint.axis, `geometry-joint-${index}-axis`);
      const axisName = joint.channel === 1 ? "+X" : "+Y";
      const axis = joint.channel === 1 ? [1, 0, 0] : [0, 1, 0];
      const useAxis = document.createElement("button"); useAxis.type = "button"; useAxis.className = "secondary";
      useAxis.textContent = `Use neutral ${axisName} axis for channel ${joint.channel}`;
      useAxis.onclick = () => {
        axis.forEach((value, component) => { el<HTMLInputElement>(`geometry-joint-${index}-axis-${component}`).value = String(value); });
        dirty = true; render();
      };
      card.append(useAxis);
      numeric(card, `Channel ${joint.channel} angle error (degrees)`, joint.error_deg, `geometry-joint-${index}-error`);
      const samples = document.createElement("div"); samples.id = `geometry-joint-${index}-samples`;
      samples.className = "angle-samples";
      samples.setAttribute("role", "group"); samples.setAttribute("aria-label", `Channel ${joint.channel} pulse angle samples`);
      let sampleNumber = 0;
      const addSample = (pair: (number | null)[]) => {
        const row = document.createElement("div"); row.className = "angle-sample";
        const number = ++sampleNumber;
        const pulse = numeric(row, `CH ${joint.channel} sample ${number} pulse (µs)`, pair[0]);
        pulse.dataset.samplePulse = "";
        const angle = numeric(row, `CH ${joint.channel} sample ${number} observed angle (degrees)`, pair[1]);
        angle.dataset.sampleAngle = "";
        const reference = document.createElement("output"); reference.className = "nominal-angle";
        const update = () => { reference.textContent = nominalText(joint.channel, pulse.valueAsNumber); };
        pulse.addEventListener("input", update); update();
        const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary";
        remove.textContent = "Remove sample"; remove.setAttribute("aria-label", `Remove CH ${joint.channel} sample ${number}`);
        remove.onclick = () => { row.remove(); dirty = true; render(); };
        row.append(remove, reference); samples.append(row);
      };
      joint.pulse_angle_deg.forEach(addSample);
      const add = document.createElement("button"); add.type = "button"; add.className = "secondary";
      add.textContent = `Add sample for channel ${joint.channel}`;
      add.onclick = () => { addSample([null, null]); dirty = true; render(); };
      card.append(samples, add); el("geometry-joints").append(card);
    });
  }
  function collect() {
    if (!geometry) return;
    geometry.clearance_mm = value("geometry-clearance"); geometry.geometry_error_mm = value("geometry-error-mm");
    geometry.capsules = geometry.capsules.map((_, index) => ({
      name: el<HTMLInputElement>(`capsule-${index}-name`).value.trim(),
      parent_channel: Number(el<HTMLSelectElement>(`capsule-${index}-parent`).value),
      a_mm: readVector(`capsule-${index}-a`), b_mm: readVector(`capsule-${index}-b`),
      radius_mm: value(`capsule-${index}-radius`),
    }));
    geometry.joints = geometry.joints.map((joint, index) => ({ ...joint,
      origin_mm: readVector(`geometry-joint-${index}-origin`), axis: readVector(`geometry-joint-${index}-axis`),
      error_deg: value(`geometry-joint-${index}-error`),
      pulse_angle_deg: [...el(`geometry-joint-${index}-samples`).querySelectorAll<HTMLElement>(".angle-sample")].map(row =>
        [...row.querySelectorAll<HTMLInputElement>("input")].map(input => {
          if (input.validity.badInput || (input.value !== "" && !Number.isFinite(input.valueAsNumber))) {
            throw new Error(`Channel ${joint.channel}: enter finite pulses and observed angles, or leave unknown values blank.`);
          }
          return input.value === "" ? null : input.valueAsNumber;
        })),
    }));
  }
  function fail(error: unknown) { el("geometry-error").textContent = error instanceof Error ? error.message : "Could not save measurements"; el("geometry-error").hidden = false; }
  async function request(body?: unknown): Promise<State> {
    const response = await fetch("/api/coordinator/commissioning/geometry", {
      method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message ?? "Measurements request failed");
    return result;
  }
  function load(result: State) {
    state = result; geometry = structuredClone(result.geometry) as unknown as Geometry;
    dirty = false; renderFields(); render();
  }
  async function refresh() {
    if (pending || dirty) return;
    pending = true; render();
    try { load(await request()); } catch (error) { fail(error); }
    finally { pending = false; render(); }
  }
  async function save(activate: boolean) {
    if (pending || !state) return;
    pending = true; el("geometry-error").hidden = true; render();
    try {
      collect();
      load(await request({ context_key: state.context_key, revision: state.revision, geometry, activate }));
    } catch (error) { fail(error); }
    finally { pending = false; render(); }
  }
  el("geometry-form").addEventListener("input", () => { dirty = true; render(); });
  el<HTMLFormElement>("geometry-form").onsubmit = event => { event.preventDefault(); void save(false); };
  el<HTMLButtonElement>("geometry-activate").onclick = () => { void save(true); };
  el<HTMLButtonElement>("geometry-reload").onclick = () => { dirty = false; el("geometry-error").hidden = true; void refresh(); };
  el<HTMLButtonElement>("geometry-add").onclick = () => {
    if (!geometry) return;
    try { collect(); geometry.capsules.push({ name: `part_${geometry.capsules.length + 1}`, parent_channel: 1, a_mm: null, b_mm: null, radius_mm: null }); dirty = true; renderFields(); render(); }
    catch (error) { fail(error); }
  };
  render();
  return {
    selectPart(name: string) {
      root.closest("#commissioning-panel")?.querySelectorAll(".arm-part-selected").forEach(item => item.classList.remove("arm-part-selected"));
      // Read current fields so renamed or reassigned parts are never mistaken for defaults.
      const index = geometry?.capsules.findIndex((_, index) => {
        const nameField = el<HTMLInputElement>(`capsule-${index}-name`);
        const parentField = el<HTMLSelectElement>(`capsule-${index}-parent`);
        return knownPart(nameField.value.trim(), Number(parentField.value))?.storedName === name;
      }) ?? -1;
      if (index < 0) return false;
      const card = el<HTMLInputElement>(`capsule-${index}-name`).closest<HTMLDetailsElement>(".geometry-card")!;
      card.open = true; card.classList.add("arm-part-selected");
      card.scrollIntoView({ block: "start" });
      card.querySelector<HTMLElement>("summary")!.focus({ preventScroll: true });
      return true;
    },
    setVisible(value: boolean) { const entered = value && !visible; visible = value; if (entered) void refresh(); },
  };
}
