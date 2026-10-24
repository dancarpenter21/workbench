import type { components } from "@workbench/contracts";

type Current = components["schemas"]["ClawCurrentStatus"];

function readingFresh(sample?: Current | null) {
  return !!sample && (sample.reading_healthy ?? (sample.healthy && !sample.fault)) === true &&
    sample.age_seconds != null && sample.age_seconds < 0.3 &&
    Number.isFinite(sample.bus_v) && Number.isFinite(sample.shunt_mv);
}

export function renderCurrent(root: HTMLElement, sample?: Current | null) {
  if (!root.querySelector(".current-main")) {
    root.classList.add("current-status");
    root.innerHTML = `<div class="current-main"><span class="status-light" aria-hidden="true"></span>
      <strong class="current-label"></strong><span class="current-raw"></span>
      <span class="current-domain badge"></span></div>
      <details class="current-details"><summary>Current details</summary><div class="current-scale"></div>
        <div class="current-age"></div><div class="current-baseline"></div><div class="current-recovery"></div></details><span class="current-fault" role="alert"></span>`;
  }
  const set = (selector: string, text: string) => { root.querySelector<HTMLElement>(selector)!.textContent = text; };
  const fresh = readingFresh(sample);
  const latched = fresh && (!!sample?.fault || sample?.healthy !== true);
  // Physical off inference requires the recorded voltage AND absolute-shunt bounds.
  // Exact-zero fallback is only for explicitly simulated readings.
  const zero = fresh && !!sample && (sample.power_state === "off" ||
    (!sample.off_bounds && sample.simulated && (sample.scale_verified ? sample.current_a === 0 : sample.shunt_mv === 0)));
  const state = !sample ? "waiting" : !fresh ? "error" : zero ? "off" : latched ? "paused" : "ok";
  root.dataset.state = state;
  set(".status-light", state === "error" ? "✕" : (state === "waiting" || state === "paused") ? "…" : state === "off" ? "○" : "✓");
  set(".current-label", state === "error" ? "Sensor fault" : state === "waiting" ? "Connecting" : zero ? "Powered down" : latched ? "Reset required" : "Current monitored");
  root.querySelector<HTMLElement>(".current-label")!.title = zero ? "Within the recorded off-voltage and shunt bounds; not a measured power-switch state." : "Claw INA219 monitor";
  set(".current-raw", sample ? `${sample.shunt_mv?.toFixed(2) ?? "—"} mV · ${sample.bus_v?.toFixed(2) ?? "—"} V${sample.scale_verified ? ` · ${sample.current_a?.toFixed(3) ?? "—"} A` : ""}` : "");
  set(".current-domain", sample?.simulated ? "SIMULATED" : "CLAW");
  set(".current-scale", sample?.scale_verified ? `✓ Verified scale · ${sample.shunt_ohms ?? "unknown"} Ω · cutoff |current| ≥ 2.5 A · ${sample.verification_evidence || "Evidence unavailable"}` : "Amperes and 2.5 A cutoff UNVERIFIED · raw shunt voltage only");
  set(".current-age", `Sample age: ${sample?.age_seconds?.toFixed(2) ?? "—"} s · INA219 0x41`);
  set(".current-baseline", sample?.off_bounds
    ? `Off baseline: ≤${sample.off_bounds.max_bus_v.toFixed(3)} V and |shunt| ≤${sample.off_bounds.max_abs_shunt_mv.toFixed(3)} mV · ${sample.off_bounds.baseline_id}`
    : sample?.off_baseline_error ? `Off baseline unavailable: ${sample.off_baseline_error}` : "Off baseline not established for this sensor setup.");
  set(".current-recovery", latched ? "Current readings are available. A previous monitoring interruption still needs an explicit reset before motion. Previous interlock: " + sample?.fault : "");
  set(".current-fault", sample && !fresh ? sample.fault || "No fresh sensor reading. Physical testing unavailable." : "");
}


export function renderPowerIndicator(root: HTMLElement, sample?: Current | null) {
  const fresh = readingFresh(sample);
  const latched = fresh && (!!sample?.fault || sample?.healthy !== true);
  const off = fresh && !!sample && (sample.power_state === "off" || (!sample.off_bounds && sample.simulated && sample.shunt_mv === 0));
  const state = !sample ? "waiting" : !fresh ? "error" : off ? "off" : latched ? "paused" : sample?.power_state === "energized" ? "ok" : "waiting";
  const label = state === "error" ? "Sensor fault" : state === "off" ? "Powered down" : state === "ok" ? "Powered" : latched ? "Reset required" : "Power unknown";
  root.dataset.state = state;
  root.textContent = `${state === "error" ? "✕" : state === "off" ? "○" : state === "ok" ? "✓" : "…"} ${label}`;
  root.setAttribute("aria-label", `Claw supply: ${label}${sample?.simulated ? " (simulated)" : ""}`);
  root.title = (latched ? "Readings available; reset the previous monitoring interruption before motion. " : "") + (sample?.fault || `${sample?.simulated ? "Simulated · " : ""}INA219: ${sample?.bus_v?.toFixed(3) ?? "—"} V, ${sample?.shunt_mv?.toFixed(3) ?? "—"} mV. ${sample?.scale_verified ? "Verified current scale" : "Amperes and 2.5 A cutoff unverified"}`);
}
