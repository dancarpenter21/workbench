import type { components } from "@workbench/contracts";

type State = components["schemas"]["GeometryMeasurements"];
type Vec = (number | null)[] | null;
type Capsule = { name: string; parent_channel: number; a_mm: Vec; b_mm: Vec; radius_mm: number | null };
type Joint = { channel: number; origin_mm: Vec; axis: Vec; pulse_angle_deg: (number | null)[][]; error_deg: number | null };
type Geometry = { schema_version: number; revision: string; domain: string; calibrated: boolean;
  reference_pulse_us: number; base_axis_perpendicular_to_table: boolean; claw_envelope: string;
  clearance_mm: number | null; geometry_error_mm: number | null; max_boxes: number;
  capsules: Capsule[]; joints: Joint[] };

export function mountGeometry(root: HTMLElement) {
  root.innerHTML = `
    <h3>Capsule measurements</h3>
    <p>Measure the robot at its 1500 µs startup pose. Each capsule is a line segment with a radius large enough to enclose the part.</p>
    <p class="caption">Use millimeters in one fixed frame: table height is z = 0, positive z points up. Include brackets, servo bodies, and the claw at every opening. Base/channel 5 does not change height. Jog remains free play.</p>
    <p id="geometry-status" role="status">Loading measurements…</p>
    <form id="geometry-form" novalidate>
      <div class="geometry-pair">
        <label>Required table gap (mm)<input id="geometry-clearance" type="number" min="0" step="any" /></label>
        <label>Geometry / sag allowance (mm)<input id="geometry-error-mm" type="number" min="0" step="any" /></label>
      </div>
      <div id="geometry-capsules"></div>
      <button id="geometry-add" type="button" class="secondary">Add part capsule</button>
      <details id="geometry-joint-details"><summary>Joint axes and angle calibration</summary>
        <p class="caption">The clearance calculation also needs each axis location and direction at startup. Angles are zero at 1500 µs. Enter measured pulse : angle pairs, one per line. Positive angles follow the right-hand rule around the axis.</p>
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
      const title = document.createElement("summary"); title.textContent = `Part ${index + 1} · ${capsule.name}`; card.append(title);
      const nameLabel = document.createElement("label"); nameLabel.textContent = "Part name";
      const name = document.createElement("input"); name.id = `capsule-${index}-name`; name.value = capsule.name;
      name.setAttribute("aria-label", `Part ${index + 1} name`); nameLabel.append(name); card.append(nameLabel);
      const parentLabel = document.createElement("label"); parentLabel.textContent = "Last joint moving this part";
      const parent = document.createElement("select"); parent.id = `capsule-${index}-parent`;
      parent.setAttribute("aria-label", `Part ${index + 1} parent channel`);
      [4, 3, 2, 1].forEach(channel => parent.add(new Option(`Channel ${channel}`, String(channel))));
      parent.value = String(capsule.parent_channel); parentLabel.append(parent); card.append(parentLabel);
      vector(card, `Part ${index + 1} endpoint A (mm)`, capsule.a_mm, `capsule-${index}-a`);
      vector(card, `Part ${index + 1} endpoint B (mm)`, capsule.b_mm, `capsule-${index}-b`);
      numeric(card, `Part ${index + 1} radius (mm)`, capsule.radius_mm, `capsule-${index}-radius`);
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary";
      remove.textContent = "Remove part"; remove.onclick = () => {
        try { collect(); geometry!.capsules.splice(index, 1); dirty = true; renderFields(); render(); }
        catch (error) { fail(error); }
      };
      card.append(remove); el("geometry-capsules").append(card);
    });
    el("geometry-joints").replaceChildren();
    geometry.joints.forEach((joint, index) => {
      const card = document.createElement("fieldset"); card.className = "geometry-card";
      const title = document.createElement("legend"); title.textContent = `Joint channel ${joint.channel}`; card.append(title);
      vector(card, `Channel ${joint.channel} axis origin (mm)`, joint.origin_mm, `geometry-joint-${index}-origin`);
      vector(card, `Channel ${joint.channel} axis direction`, joint.axis, `geometry-joint-${index}-axis`);
      numeric(card, `Channel ${joint.channel} angle error (degrees)`, joint.error_deg, `geometry-joint-${index}-error`);
      const label = document.createElement("label"); label.textContent = "Pulse µs : angle degrees";
      const samples = document.createElement("textarea"); samples.id = `geometry-joint-${index}-samples`; samples.rows = 3;
      samples.setAttribute("aria-label", `Channel ${joint.channel} pulse angle samples`);
      samples.value = joint.pulse_angle_deg.map(pair => pair.map(v => v == null ? "" : String(v)).join(" : ")).join("\n");
      label.append(samples); card.append(label); el("geometry-joints").append(card);
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
      pulse_angle_deg: el<HTMLTextAreaElement>(`geometry-joint-${index}-samples`).value.split("\n").filter(line => line.trim()).map(line => {
        const pair = line.split(":").map(v => v.trim() === "" ? null : Number(v.trim()));
        if (pair.length !== 2 || pair.some(v => v !== null && !Number.isFinite(v))) throw new Error(`Channel ${joint.channel}: use pulse : angle, one pair per line.`);
        return pair;
      }),
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
  return { setVisible(value: boolean) { const entered = value && !visible; visible = value; if (entered) void refresh(); } };
}
