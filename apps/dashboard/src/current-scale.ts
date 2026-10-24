import type { components } from "@workbench/contracts";

type ScaleState = components["schemas"]["CurrentScaleState"];
type Verification = components["schemas"]["CurrentScaleVerification"];
type Status = components["schemas"]["CommissionStatus"];

export function mountCurrentScale(root: HTMLElement) {
  root.innerHTML = `<details><summary>Current-scale verification</summary>
    <p>Record an external measurement of this sensor's shunt resistance. Neutral-hold readings cannot establish the scale.
    Polling and PWM shutdown are not a physical current limiter. A verified scale enables the absolute 2.5 A software cutoff.</p>
    <p id="scale-source" role="status"></p><div class="manual-settings">
    <label>Assembly label<input id="scale-assembly" maxlength="200" /></label>
    <label>Observer<input id="scale-observer" maxlength="200" /></label>
    <label>Measured at (include timezone)<input id="scale-date" placeholder="2026-10-23T15:00:00-04:00" /></label>
    <label>Verified resistance (Ω)<input id="scale-resistance" type="number" min="0" step="any" /></label>
    <label>I2C bus<input id="scale-bus" type="number" min="0" value="1" /></label>
    <label>PWM address (decimal)<input id="scale-pwm" type="number" value="64" /></label>
    <label>Measurement method<textarea id="scale-method" maxlength="2000"></textarea></label>
    <label>Instrumentation<input id="scale-instrumentation" maxlength="1000" /></label>
    <label>Evidence reference<input id="scale-evidence" maxlength="2000" /></label></div>
    <label class="ack"><input id="scale-attest" type="checkbox" /> I externally verified this resistance for the identified assembly and sensor.</label>
    <button id="scale-save" class="secondary">Save verification record</button>
    <label>Saved verification<select id="scale-records"></select></label>
    <pre id="scale-details"></pre>
    <label class="ack"><input id="scale-off" type="checkbox" /> ALL servo power is disconnected and the arm is supported.</label>
    <button id="scale-apply" class="secondary">Apply selected verification</button>
    <p id="scale-error" role="alert"></p></details>`;
  const el = <T extends HTMLElement>(id: string) => root.querySelector<T>(`#scale-${id}`)!;
  const input = (id: string) => el<HTMLInputElement>(id);
  let records: ScaleState | null = null;
  let status: Status | null = null;
  let pending = false;
  async function request<T>(path = "", body?: unknown): Promise<T> {
    const response = await fetch(`/api/coordinator/commissioning/current-scale${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(3000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message ?? "Current scale request failed");
    return result;
  }
  function render() {
    el("source").textContent = `Active source: ${records?.source ?? "loading"} · ${records?.active_id ?? "startup configuration"}. Sensor 0x41. ${status?.simulated ? "SIMULATED evidence only." : "PHYSICAL"}`;
    const record = records?.records?.find(r => r.id === el<HTMLSelectElement>("records").value);
    el("details").textContent = record ? [
      `${record.domain.toUpperCase()} · ${record.assembly_id} · ${record.shunt_ohms} Ω`,
      `${record.observer} · ${record.observed_at}`, `Method: ${record.method}`,
      `Instrumentation: ${record.instrumentation}`, `Evidence: ${record.evidence}`,
      `Bus ${record.i2c_bus} · sensor 0x41 · PWM 0x${record.pwm_address.toString(16)}`,
    ].join("\n") : "No verification selected";
    el<HTMLButtonElement>("save").disabled = pending || !status || !input("attest").checked;
    el<HTMLButtonElement>("apply").disabled = pending || !status || status.reserved || !record || !input("off").checked;
  }
  async function refresh(selected?: string) {
    records = await request<ScaleState>();
    const select = el<HTMLSelectElement>("records");
    const previous = selected ?? select.value;
    select.replaceChildren(new Option("Choose verification", ""), ...(records.records ?? []).map(r => new Option(`${r.assembly_id} · ${r.observer} · ${r.shunt_ohms} Ω · ${r.domain}`, r.id)));
    select.value = previous;
    render();
  }
  async function run(work: () => Promise<void>) {
    if (pending) return;
    pending = true; el("error").textContent = ""; render();
    try { await work(); }
    catch (error) { el("error").textContent = error instanceof Error ? error.message : "Verification unavailable"; }
    finally { pending = false; render(); }
  }
  el<HTMLButtonElement>("save").onclick = () => void run(async () => {
    const text = (id: string) => input(id).value.trim();
    const date = text("date");
    if (!/(Z|[+-]\d\d:\d\d)$/.test(date)) throw new Error("Include a timezone in the measurement timestamp");
    const record = await request<Verification>("", {
      domain: status?.simulated ? "simulated" : "physical", assembly_id: text("assembly"), observer: text("observer"),
      observed_at: date, shunt_ohms: input("resistance").valueAsNumber,
      i2c_bus: input("bus").valueAsNumber, pwm_address: input("pwm").valueAsNumber, sensor_address: 65,
      method: el<HTMLTextAreaElement>("method").value.trim(), instrumentation: text("instrumentation"),
      evidence: text("evidence"), externally_verified: true,
    });
    input("attest").checked = false; await refresh(record.id);
  });
  el<HTMLButtonElement>("apply").onclick = () => void run(async () => {
    await request("/apply", { verification_id: el<HTMLSelectElement>("records").value, power_off_and_supported: true });
    input("off").checked = false; await refresh();
  });
  root.addEventListener("change", render);
  void run(() => refresh());
  return { update(value: Status | null) { status = value; render(); } };
}
