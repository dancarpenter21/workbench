import type { components } from "@workbench/contracts";
type Evidence = components["schemas"]["NeutralEvidence"];
type Current = components["schemas"]["ClawCurrentStatus"];

export function renderNeutral(root: HTMLElement, evidence?: Evidence | null, current?: Current | null, snapshot = false) {
  root.hidden = !evidence?.reference && !evidence?.detail?.startsWith("Neutral reference unavailable");
  if (root.hidden) return;
  if (!root.querySelector("summary")) {
    root.className = "neutral-evidence";
    root.innerHTML = `<div class="neutral-heading"><span class="neutral-icon" aria-hidden="true"></span><strong class="neutral-label"></strong><span class="neutral-count"></span></div>
      <details><summary>Neutral capture details</summary><p class="neutral-values"></p><p class="neutral-scope"></p><p class="neutral-context"></p><small class="neutral-provenance"></small></details>`;
  }
  const set = (selector: string, value: string) => { root.querySelector<HTMLElement>(selector)!.textContent = value; };
  const stale = snapshot ? evidence?.state === "fault" : !current?.healthy || !!current.fault || current.age_seconds == null || current.age_seconds >= 0.3;
  const state = stale ? "fault" : evidence?.state ?? "unavailable";
  root.dataset.state = state;
  const labels: Record<string, string> = { unavailable: "Neutral reference unavailable", fault: "Neutral comparison unavailable", off: "Neutral reference · supply off", not_neutral: "Neutral reference recorded", within_reference: "Neutral readings match capture", outside_reference: "Outside neutral reference" };
  set(".neutral-icon", state === "outside_reference" ? "!" : state === "fault" ? "✕" : "✓");
  set(".neutral-label", labels[state]);
  const ref = evidence?.reference;
  set(".neutral-count", ref ? `${Math.round(ref.duration_seconds / 60)} min · ${ref.sample_count.toLocaleString()} samples` : "");
  set(".neutral-values", ref ? `1500 µs × 6 · ${ref.bus_v.min.toFixed(3)}–${ref.bus_v.max.toFixed(3)} V · shunt ${ref.shunt_mv.min.toFixed(3)} to ${ref.shunt_mv.max.toFixed(3)} mV` : "");
  set(".neutral-scope", "Claw circuit only · Amperes and 2.5 A cutoff UNVERIFIED · Observed ranges, not motion or safety limits.");
  set(".neutral-context", stale ? "Live comparison needs fresh healthy sensor readings." : evidence?.detail ?? "");
  set(".neutral-provenance", ref ? `${ref.domain === "simulated" ? "SIMULATED · " : ""}${ref.trial_id} · PWM off verified at completion` : "");
}
