import type { components } from "@workbench/contracts";

type Current = components["schemas"]["ClawCurrentStatus"];

// Presentation grace only. The controller still enforces its 300 ms sensor deadline.
export const CURRENT_DISPLAY_GRACE_SECONDS = 1;

function sensorReportedValid(sample: Current) {
  return (sample.reading_healthy ?? (sample.healthy && !sample.fault)) === true;
}

function readingFresh(sample?: Current | null) {
  return !!sample && sensorReportedValid(sample) &&
    sample.age_seconds != null && sample.age_seconds < CURRENT_DISPLAY_GRACE_SECONDS &&
    Number.isFinite(sample.bus_v) && Number.isFinite(sample.shunt_mv);
}

export function renderCurrent(root: HTMLElement, sample?: Current | null) {
  if (!root.querySelector(".current-main")) {
    root.classList.add("current-status", "device-pill");
    root.innerHTML = `<div class="current-main"><span class="status-light" aria-hidden="true"></span>
      <span class="current-domain"></span><span class="current-label"></span>
      <span class="current-raw"><span class="current-reading current-shunt">Shunt voltage <span class="current-value"></span></span><span class="current-reading current-voltage">Supply <span class="current-value"></span></span><span class="current-reading current-amperes">Current <span class="current-value"></span></span></span></div>
      <details class="current-details"><summary aria-label="Current details">Details</summary><div class="current-details-body"><div class="current-scale"></div>
        <div class="current-age"></div><div class="current-baseline"></div><div class="current-recovery"></div></div></details><span class="current-fault" role="alert"></span>`;
  }
  const set = (selector: string, text: string) => { root.querySelector<HTMLElement>(selector)!.textContent = text; };
  const fresh = readingFresh(sample);
  const delayed = !!sample && sensorReportedValid(sample) && sample.age_seconds != null && sample.age_seconds >= CURRENT_DISPLAY_GRACE_SECONDS;
  const latched = fresh && (!!sample?.fault || sample?.healthy !== true);
  // Physical off inference requires the recorded voltage AND absolute-shunt bounds.
  // Exact-zero fallback is only for explicitly simulated readings.
  const zero = fresh && !!sample && (sample.power_state === "off" ||
    (!sample.off_bounds && sample.simulated && (sample.scale_verified ? sample.current_a === 0 : sample.shunt_mv === 0)));
  const state = !sample || delayed ? "waiting" : !fresh ? "error" : zero ? "off" : latched ? "paused" : "ok";
  root.dataset.state = state;
  root.dataset.simulated = String(sample?.simulated === true);
  root.dataset.deviceState = fresh ? "ok" : !sample || sample.age_seconds == null || sample.age_seconds >= 0.3 ? "waiting" : "error";
  root.dataset.pillState = root.dataset.deviceState;
  set(".status-light", state === "error" ? "✕" : (state === "waiting" || state === "paused") ? "…" : state === "off" ? "○" : "✓");
  set(".current-label", state === "error" ? "Sensor fault" : state === "waiting" ? (sample ? "Updates delayed" : "Connecting") : "");
  root.querySelector<HTMLElement>(".current-label")!.hidden = fresh;
  root.querySelector<HTMLElement>(".current-label")!.title = zero ? "Within the recorded off-voltage and shunt bounds; not a measured power-switch state." : "Claw INA219 monitor";
  set(".current-shunt .current-value", `${sample?.shunt_mv?.toFixed(2) ?? "—"} mV`);
  set(".current-voltage .current-value", `${sample?.bus_v?.toFixed(2) ?? "—"} V`);
  set(".current-amperes .current-value", `${sample?.current_a?.toFixed(3) ?? "—"} A`);
  root.querySelector<HTMLElement>(".current-amperes")!.hidden = sample?.scale_verified !== true;
  set(".current-domain", sample?.simulated ? "Ammeter · SIMULATED" : "Ammeter");

  set(".current-scale", sample?.scale_verified ? `✓ Verified scale · ${sample.shunt_ohms ?? "unknown"} Ω · cutoff |current| ≥ 2.5 A · ${sample.verification_evidence || "Evidence unavailable"}` : "Amperes and 2.5 A cutoff UNVERIFIED · raw shunt voltage only");
  set(".current-age", `Sample age: ${sample?.age_seconds?.toFixed(2) ?? "—"} s · INA219 0x41`);
  set(".current-baseline", sample?.off_bounds
    ? `Off baseline: ≤${sample.off_bounds.max_bus_v.toFixed(3)} V and |shunt| ≤${sample.off_bounds.max_abs_shunt_mv.toFixed(3)} mV · ${sample.off_bounds.baseline_id}`
    : sample?.off_baseline_error ? `Off baseline unavailable: ${sample.off_baseline_error}` : "Off baseline not established for this sensor setup.");
  set(".current-recovery", latched ? "Current readings are available. A previous monitoring interruption still needs an explicit reset before motion. Previous interlock: " + sample?.fault : "");
  set(".current-fault", sample && !fresh && !delayed ? sample.fault || "No fresh sensor reading. Physical testing unavailable." : "");
}

export function renderServoPower(root: HTMLElement, sample?: Current | null) {
  if (!root.querySelector(".power-label")) {
    root.innerHTML = '<span class="status-light" aria-hidden="true"></span><span class="power-label"></span>';
  }
  const fresh = readingFresh(sample);
  const off = fresh && !!sample && (sample.power_state === "off" ||
    (!sample.off_bounds && sample.simulated && sample.shunt_mv === 0));
  const state = off ? "off" : fresh && sample?.power_state === "energized" ? "ok" : "waiting";
  const label = state === "off" ? "Powered down" : state === "ok" ? "Powered" : "Power unknown";
  root.dataset.state = state;
  root.dataset.pillState = state;
  root.querySelector(".power-label")!.textContent = label;
  root.querySelector(".status-light")!.textContent = state === "off" ? "○" : state === "ok" ? "✓" : "…";
  root.setAttribute("aria-label", `Servo power: ${label}${sample?.simulated ? " (simulated)" : ""}`);
  root.title = "Servo supply state inferred from the ammeter; not a motion-readiness indication.";
}

export function renderHatPresence(root: HTMLElement, sample: components["schemas"]["PeripheralStatus"] | null | undefined, elapsed: number, connected: boolean) {
  const fresh = connected && sample?.age_seconds != null && sample.age_seconds + elapsed < 3;
  const state = !fresh ? "waiting" : sample?.state === "detected" ? "ok" : sample?.state === "error" ? "error" : "waiting";
  root.classList.add("device-pill");
  root.dataset.state = state;
  root.dataset.pillState = state;
  if (!root.querySelector(".status-light")) {
    root.innerHTML = '<span class="status-light" aria-hidden="true"></span><span>Servo HAT</span>';
  }
  root.querySelector(".status-light")!.textContent = state === "ok" ? "✓" : state === "error" ? "✕" : "…";
  root.setAttribute("aria-label", `Servo HAT: ${state === "ok" ? "detected" : state === "error" ? "not detected" : "unknown"}`);
  root.title = !connected ? "Service unavailable; device status unknown" : `${sample?.simulated ? "SIMULATED · " : ""}PCA9685 · ${sample?.address != null ? "0x" + sample.address.toString(16) : "address unknown"} · ${sample?.detail ?? "Waiting for device check"}`;
}
