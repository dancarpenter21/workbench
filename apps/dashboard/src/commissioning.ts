import type { components } from "@workbench/contracts";
import { armParts, channelLabel } from "./arm-guide";

type Bounds = components["schemas"]["CommissionBounds"];
type Values = components["schemas"]["CommissionLimitValues"];
const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];

/** Configuration only. All pulse positioning belongs to Manual. */
export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `<h2>Harness pulse limits</h2>
    <p>Saved limits constrain the LLM harness. Manual uses the fixed independent assembly bounds. Saving never moves the arm.</p>
    <p id="init-domain"></p>
    <form id="init-form" novalidate><div class="init-values">
      <div class="init-row init-head"><span>Joint</span><span>Min µs</span><span>Max µs</span></div>
      ${joints.map(id => `<div class="init-row"><span>${channelLabel(armParts.find(p => p.joint === id)!.channel)}</span>
        <input id="init-${id}-min" aria-label="${id} minimum" type="number" min="500" max="1500" step="1" />
        <input id="init-${id}-max" aria-label="${id} maximum" type="number" min="1500" max="2500" step="1" />
        <div class="limit-clear-actions"><button type="button" data-clear="${id}-min">Clear min</button><button type="button" data-clear="${id}-max">Clear max</button></div></div>`).join("")}
      </div><button id="init-save" type="submit" disabled>Save limits</button></form>
    <p id="init-error" role="alert" hidden></p>`;
  const input = (id: string, kind: string) => root.querySelector<HTMLInputElement>(`#init-${id}-${kind}`)!;
  const save = root.querySelector<HTMLButtonElement>("#init-save")!;
  const error = root.querySelector<HTMLElement>("#init-error")!;
  const cleared = new Map<string, string>();
  let bounds: Bounds | null = null;
  let dirty = false;
  let pending = false;
  async function request(body?: object): Promise<Bounds> {
    const response = await fetch("/api/coordinator/commissioning/bounds", {
      method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(5000),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Could not save limits");
    return value;
  }
  function render() {
    save.disabled = pending || !bounds;
    root.querySelectorAll<HTMLInputElement>("input").forEach(field => { field.disabled = pending || !bounds; });
    root.querySelectorAll<HTMLButtonElement>("[data-clear]").forEach(button => {
      button.disabled = pending || !bounds;
      const key = button.dataset.clear!;
      button.textContent = `${cleared.has(key) ? "Undo clear" : "Clear"} ${key.endsWith("-min") ? "min" : "max"}`;
    });
  }
  function load(value: Bounds) {
    bounds = value;
    root.querySelector("#init-domain")!.textContent = value.domain === "simulated" ? "SIMULATED" : "";
    if (!dirty) for (const id of joints) {
      input(id, "min").value = String(value.joints[id]?.minimum?.requested_us ?? "");
      input(id, "max").value = String(value.joints[id]?.maximum?.requested_us ?? "");
    }
    render();
  }
  function fail(value: unknown) { error.textContent = value instanceof Error ? value.message : "Settings unavailable"; error.hidden = false; }
  root.querySelectorAll<HTMLInputElement>("input").forEach(field => {
    field.oninput = () => { dirty = true; cleared.delete(field.id.slice(5)); save.textContent = "Save limits"; };
  });
  root.querySelectorAll<HTMLButtonElement>("[data-clear]").forEach(button => {
    button.onclick = () => {
      const key = button.dataset.clear!;
      const field = input(key.slice(0, -4), key.slice(-3));
      if (cleared.has(key)) { field.value = cleared.get(key)!; cleared.delete(key); }
      else { cleared.set(key, field.value); field.value = ""; }
      dirty = true; save.textContent = "Save limits"; render();
    };
  });
  root.querySelector<HTMLFormElement>("form")!.onsubmit = async event => {
    event.preventDefault();
    if (pending || !bounds) return;
    const changes: Record<string, Partial<Values>> = {};
    for (const id of joints) {
      const min = input(id, "min"), max = input(id, "max");
      max.setCustomValidity(min.value && max.value && min.valueAsNumber >= max.valueAsNumber ? "Maximum must be above minimum." : "");
      if (!min.reportValidity() || !max.reportValidity()) return;
      const values: Partial<Values> = {};
      if (min.value) values.minimum_us = min.valueAsNumber;
      if (max.value) values.maximum_us = max.valueAsNumber;
      if (cleared.has(`${id}-min`)) values.clear_minimum = true;
      if (cleared.has(`${id}-max`)) values.clear_maximum = true;
      if (Object.keys(values).length) changes[id] = values;
    }
    if (!Object.keys(changes).length) return;
    pending = true; error.hidden = true; render();
    try {
      const value = await request({ assembly_id: bounds.assembly_id, setup: bounds.setup, joints: changes });
      dirty = false; cleared.clear(); load(value); save.textContent = "Saved";
    } catch (value) { fail(value); }
    finally { pending = false; render(); }
  };
  void request().then(load).catch(fail);
}
