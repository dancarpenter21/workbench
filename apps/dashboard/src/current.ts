import type { components } from "@workbench/contracts";

type Current = components["schemas"]["ClawCurrentStatus"];

// Presentation grace only. The controller still enforces its 300 ms sensor deadline.
export const CURRENT_DISPLAY_GRACE_SECONDS = 1;

function sensorReportedValid(sample: Current) {
  if (sample.reading_state != null) return sample.reading_state === "ok";
  return (sample.reading_healthy ?? (sample.healthy && !sample.fault)) === true;
}

function readingFresh(sample?: Current | null) {
  return !!sample && sensorReportedValid(sample) &&
    sample.age_seconds != null && sample.age_seconds >= 0 && sample.age_seconds < CURRENT_DISPLAY_GRACE_SECONDS &&
    Number.isFinite(sample.bus_v) && Number.isFinite(sample.shunt_mv);
}

export function ageCurrent(sample: Current | null | undefined, elapsed: number, connected: boolean) {
  if (!sample || (!connected && elapsed >= CURRENT_DISPLAY_GRACE_SECONDS)) return null;
  return { ...sample, age_seconds: sample.age_seconds == null ? null : sample.age_seconds + elapsed };
}

export function currentPresentation(sample?: Current | null) {
  const fresh = readingFresh(sample);
  const reading = fresh ? "ok" : !sample ? "unavailable"
    : sample.reading_state === "error" ? "error" : sample.age_seconds == null ? "unavailable"
    : sample.reading_state === "stale" || sample.age_seconds >= CURRENT_DISPLAY_GRACE_SECONDS ? "stale" : "error";
  // Compatibility for older simulated fixtures only; physical power comes from the service.
  const legacyOff = sample?.reading_state == null && !sample?.off_bounds && sample?.simulated && sample?.shunt_mv === 0;
  const power = !fresh ? "unknown" : legacyOff ? "off" : sample?.power_state ?? "unknown";
  const resetRequired = sample?.reset_required === true || !!sample?.fault;
  return {
    fresh, reading, power, resetRequired,
    sensorLabel: reading === "ok" ? "Sensor responding" : reading === "error" ? "Sensor fault" : reading === "stale" ? "Updates delayed" : "Sensor unavailable",
    powerLabel: power === "off" ? "Powered off" : power === "energized" ? "Powered" : "Power unknown",
  };
}

export function renderCurrent(root: HTMLElement, sample?: Current | null) {
  if (!root.querySelector(".current-main")) {
    root.classList.add("current-status", "device-pill");
    root.innerHTML = `<div class="current-main"><span class="status-light" aria-hidden="true"></span>
      <span class="current-domain"></span><span class="current-label"></span>
      <span class="current-raw"><span class="current-reading current-shunt">Shunt voltage <span class="current-value"></span></span><span class="current-reading current-voltage">Supply <span class="current-value"></span></span><span class="current-reading current-amperes">Current <span class="current-value"></span></span></span></div>
      <details class="current-details"><summary aria-label="Current details">Details</summary><div class="current-details-body"><div class="current-scale"></div>
        <div class="current-fault-detail"></div><div class="current-age"></div><div class="current-baseline"></div><div class="current-recovery"></div></div></details><span class="current-fault" role="alert"></span><span class="current-interlock" role="status"></span>`;
  }
  const set = (selector: string, text: string) => { root.querySelector<HTMLElement>(selector)!.textContent = text; };
  const presentation = currentPresentation(sample);
  const { fresh, reading, resetRequired } = presentation;
  const delayed = reading === "stale";
  const latched = fresh && resetRequired;
  const zero = presentation.power === "off";
  const state = reading === "unavailable" || delayed ? "waiting" : !fresh ? "error" : zero ? "off" : "ok";
  root.dataset.state = state;
  root.dataset.simulated = String(sample?.simulated === true);
  root.dataset.deviceState = fresh ? "ok" : reading === "error" ? "error" : "waiting";
  root.dataset.pillState = root.dataset.deviceState;
  set(".status-light", state === "error" ? "✕" : state === "waiting" ? "…" : state === "off" ? "○" : "✓");
  set(".current-label", fresh ? "" : presentation.sensorLabel);
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
  set(".current-interlock", resetRequired ? "Motion inhibited — reset required" : "");
  root.querySelector<HTMLElement>(".current-interlock")!.title = sample?.fault ?? "";
  const fault = sample && !fresh && !delayed
    ? sample.fault || "No fresh sensor reading. Physical testing unavailable." : "";
  set(".current-fault-detail", root.id === "header-current" ? fault : "");
  set(".current-fault", root.id === "header-current" && fault
    ? sample?.simulated ? "Sensor fault — testing unavailable. See Details." : "Sensor fault — CUT PHYSICAL SERVO POWER."
    : fault);
}

// A short delivery gap may retain a confirmed supply label, never motion authorization.
const confirmedPower = new WeakMap<HTMLElement, {
  power: "off" | "energized";
  expiresAt: number;
  simulated: boolean;
  monitorSession: string | null | undefined;
}>();

export function renderServoPower(root: HTMLElement, sample?: Current | null) {
  if (!root.querySelector(".power-label")) {
    root.innerHTML = '<span class="status-light" aria-hidden="true"></span><span class="power-label"></span>';
  }
  const presentation = currentPresentation(sample);
  const now = performance.now();
  let power = presentation.power;
  let cached = false;
  if (presentation.fresh && (power === "off" || power === "energized")) {
    confirmedPower.set(root, {
      power,
      expiresAt: now + (CURRENT_DISPLAY_GRACE_SECONDS - sample!.age_seconds!) * 1000,
      simulated: sample?.simulated === true,
      monitorSession: sample?.monitor_session_id,
    });
  } else {
    const confirmed = confirmedPower.get(root);
    const delayed = !sample || (sample.reading_state === "stale" &&
      sample.age_seconds != null && sample.age_seconds >= 0 &&
      sample.age_seconds < CURRENT_DISPLAY_GRACE_SECONDS &&
      Number.isFinite(sample.bus_v) && Number.isFinite(sample.shunt_mv));
    const sameMonitor = !sample || (sample.simulated === confirmed?.simulated &&
      sample.monitor_session_id === confirmed?.monitorSession);
    if (confirmed && delayed && sameMonitor && now < confirmed.expiresAt) {
      power = confirmed.power;
      cached = true;
    } else {
      confirmedPower.delete(root);
    }
  }
  const state = power === "off" ? "off" : power === "energized" ? "ok" : "waiting";
  const label = power === "off" ? "Powered off" : power === "energized" ? "Powered" : "Power unknown";
  root.dataset.state = state;
  root.dataset.pillState = state;
  root.dataset.cached = String(cached);
  root.querySelector(".power-label")!.textContent = label;
  root.querySelector(".status-light")!.textContent = state === "off" ? "○" : state === "ok" ? "✓" : "…";
  const simulated = sample?.simulated ?? confirmedPower.get(root)?.simulated;
  root.setAttribute("aria-label", `Servo power: ${label}${simulated ? " (simulated)" : ""}`);
  root.title = cached
    ? "Last confirmed supply state, less than one second old; awaiting an update. Not a motion-readiness indication."
    : "Servo supply state inferred from the ammeter; not a motion-readiness indication.";
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
  root.title = !fresh ? "HAT status unavailable or stale; device status unknown" : `${sample?.simulated ? "SIMULATED · " : ""}PCA9685 · ${sample?.address != null ? "0x" + sample.address.toString(16) : "address unknown"} · ${sample?.detail ?? "Waiting for device check"}`;
}
