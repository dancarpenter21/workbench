import type { components } from "@workbench/contracts";
import { renderCurrent } from "./current";

type Status = components["schemas"]["CommissionStatus"];
type Draft = components["schemas"]["CommissionDraft"];
type Session = components["schemas"]["CommissionSession"];
type Workbench = components["schemas"]["WorkbenchStatus"];
type Export = components["schemas"]["CommissionExport"];

export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `
    <div class="panel-heading"><div><p class="eyebrow">MEASURE & RECORD</p><h2>Pulse test</h2></div>
      <span id="trial-domain" class="badge">Connecting…</span></div>
    <p>Test one servo at a time on the factory-assembled arm. Base is servo 5; claw is servo 0.
      Support the links. Isolated trials require power isolation of the other five servos.</p>
    <div id="trial-current" aria-label="Claw current monitor"></div>
    <details class="telemetry-details"><summary>▤ All pulse values</summary><pre id="trial-targets" aria-label="All servo targets"></pre></details>
    <p class="caption">Initial trial settings: 1500 μs start · 1450–1550 μs window · 10 μs per click.
      These are trial settings, not measured limits. Applying power may cause a large initial movement.
      An upright pose is not assumed.</p>
    <details id="trial-records" class="record-details" open><summary>▤ Records</summary>
    <div class="manual-settings">
      <label>Assembly label<input id="trial-assembly" maxlength="200" placeholder="Factory arm / bench 1" /></label>
      <label>Observer<input id="trial-observer" maxlength="200" placeholder="Your name" /></label>
      <button id="trial-create" class="secondary">New record</button>
      <label>Resume record<select id="trial-drafts"><option value="">Select a record</option></select></label>
    </div>
    </details>
    <p id="trial-message" role="status"></p>
    <div id="trial-joints" class="manual-joints" aria-label="Commissioning joints"></div>
    <div id="trial-record" hidden>
      <h3 id="trial-selected"></h3><p id="trial-observations" class="caption"></p>
      <details id="trial-notes-panel"><summary>✎ Notes & direction</summary>
      <div class="manual-settings"><label>Observed direction when pulse increases
        <input id="trial-direction" maxlength="500" placeholder="Describe what you saw" /></label>
        <label>Notes<textarea id="trial-notes" maxlength="4000" rows="2"></textarea></label>
        <button id="trial-save" class="secondary">Save observations</button></div>
      </details>
      <div id="trial-launch" class="launch-row">
      <label>Test mode<select id="trial-type"><option value="isolated">Isolated single servo</option>
        <option value="all_holding">All servos holding · move one at a time</option></select></label>
      <button id="trial-acquire">▶ Start trial</button></div>

      <button id="trial-export" class="secondary">⇩ Export</button>
    </div>
    <div id="trial-preparation" hidden>
      <h3>1. Prepare with servo power disconnected</h3>
      <label class="ack"><input id="trial-power-off" type="checkbox" /> Base secured, links supported, clearance checked,
        servo power disconnected; for isolated trials, the other five servos are power-isolated.</label>
      <button id="trial-prepare" disabled>Prepare outputs at 1500 μs</button>
    </div>
    <div id="trial-power-on" hidden>
      <h3>2. Apply servo power externally</h3>
      <p>Keep the arm supported. The first powered movement is unknown; the step rate does not limit it.
        If it binds or moves unexpectedly, disconnect servo power and stop the trial.</p>
      <label class="ack"><input id="trial-ready-ack" type="checkbox" /> I applied power to the selected trial servos and observed that they settled with clearance.</label>
      <button id="trial-ready" disabled>Ready for individual steps</button>
    </div>
    <div id="trial-motion" hidden>
      <h3>3. Step, observe, and mark</h3><output id="trial-pulse"></output>
      <div id="trial-typed" class="manual-settings">
        <label>Target pulse (μs)<input id="trial-target" type="number" min="500" max="2500" step="1" /></label>
        <button id="trial-apply">✓ Apply ≤10 μs</button>
        <details id="trial-window-panel" class="window-details"><summary>↔ Trial window</summary><div class="manual-settings">
        <label>Trial minimum (μs)<input id="trial-low" type="number" min="500" max="2500" /></label>
        <label>Trial maximum (μs)<input id="trial-high" type="number" min="500" max="2500" /></label>
        <label class="ack"><input id="trial-window-ack" type="checkbox" /> I reviewed clearance for this trial window.</label>
        <button id="trial-window" class="secondary">✓ Save window</button></div></details>

      </div>
      <div class="actions"><button id="trial-minus" class="secondary">− 10 μs</button>
        <button id="trial-plus" class="secondary">+ 10 μs</button></div>
      <label class="ack"><input id="trial-mark-ack" type="checkbox" /> This target has settled and has clearance; I observed it directly.</label>
      <div class="actions"><button id="trial-initial" class="secondary">✓ Initial</button>
        <button id="trial-minimum" class="secondary">✓ Min</button>
        <button id="trial-maximum" class="secondary">✓ Max</button></div>

    </div>
    <button id="trial-stop" class="danger" hidden>■ Stop trial</button>
    <div id="trial-finish-panel" hidden><h3>4. Disconnect servo power before finishing</h3>
      <p>The last target may still be active. Support the arm and disconnect servo power externally.</p>
      <label class="ack"><input id="trial-finish-ack" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <label class="ack"><input id="trial-review" type="checkbox" /> I reviewed the three marks and saved direction notes for this joint.</label>
      <button id="trial-finish" disabled>Disable outputs & finish trial</button></div>
    <p class="caption">Stop holds the last PWM target. It does not disconnect power. Isolated trials require fresh power-off preparation for each joint. All-servos-holding trials retain the other five targets.
      Individual observations do not validate a combined arm pose or jogging rates.</p>
    <p id="trial-error" role="alert"></p>`;
  root.insertBefore(root.querySelector("#trial-record")!, root.querySelector("#trial-stop"));
  const guide = document.createElement("details");
  guide.className = "panel-guide";
  guide.innerHTML = "<summary>ⓘ Setup & safety guide</summary>";
  for (const paragraph of [...root.querySelectorAll<HTMLParagraphElement>(":scope > p:not([id]), :scope > .caption")]) guide.append(paragraph);
  guide.append(root.querySelector(".telemetry-details")!);
  root.append(guide);
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#trial-${id}`)!;
  const button = (id: string) => el<HTMLButtonElement>(id);
  const input = (id: string) => el<HTMLInputElement>(id);
  let status: Status | null = null;
  let draft: Draft | null = null;
  let selected = "base";
  let token: string | null = null;
  let sequence = 0;
  let pending = false;
  let visible = true;
  let connected = false;
  let generation = 0;
  let nextStep = 0;
  let polling = false;
  let acquiring = false;
  let heartbeat: Promise<void> | null = null;

  async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`/api/coordinator/commissioning/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(1500),
      keepalive: path === "stop",
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Commissioning request failed");
    return value as T;
  }
  function fail(error: unknown) {
    el("error").textContent = error instanceof Error ? error.message : "Commissioning unavailable";
  }
  function fields() {
    const joint = draft?.joints.find((j) => j.id === selected);
    input("direction").value = joint?.direction ?? "";
    el<HTMLTextAreaElement>("notes").value = joint?.notes ?? "";
  }
  async function refreshDraft() {
    if (draft) draft = await request<Draft>(`drafts/${draft.id}`);
  }
  function render() {
    const own = !!token;
    const all = status?.trial_type === "all_holding";
    renderCurrent(el("current"), status?.claw_current);
    el("targets").textContent = Object.entries(status?.targets ?? {}).map(([name, pulse]) =>
      `${name} · Servo ${5 - ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"].indexOf(name)}: requested ${pulse} μs · encoded ${status?.encoded_targets?.[name]?.toFixed(2) ?? "unknown"} μs`).join("\n");
    const ready = own && status?.stage === "ready" && status.claw_current?.healthy === true;
    const disabled = pending || !connected;
    el("domain").textContent = status?.simulated ? "● SIMULATED" : "● PHYSICAL";
    el("domain").title = "Simulated observations are not physical commissioning evidence.";
    const stages = { idle: "○ Choose a record", acquired: "○ Prepare outputs", prepared: "○ Apply power & confirm",
      ready: "✓ Ready · ≤10 μs per step", awaiting_power_off: "■ Power off to finish" };
    el("message").textContent = status ? stages[status.stage ?? "idle"] : "Connecting…";
    el("message").title = status?.message ?? "";
    el("record").hidden = !draft;
    el("records").hidden = !!status?.reserved;
    el("launch").hidden = !!status?.reserved;
    el("selected").textContent = `${selected.replaceAll("_", " ")} · Servo ${draft?.joints.find(j => j.id === selected)?.channel ?? "—"}`;
    const joint = draft?.joints.find(j => j.id === selected);
    el("observations").textContent = joint ? `${joint.state}: ${Object.entries(joint.observations ?? {}).map(([key, mark]) => `${key} ${mark.requested_us} μs`).join(" · ") || "No marks yet"}` : "";
    const joints = el("joints");
    if (!draft) { joints.replaceChildren(); delete joints.dataset.draft; }
    if (draft && joints.dataset.draft !== draft.id) {
      joints.replaceChildren();
      joints.dataset.draft = draft.id;
      for (const j of draft.joints) {
        const b = document.createElement("button");
        b.className = "secondary";
        b.dataset.joint = j.id;
        b.onclick = () => {
          if (token && status?.trial_type === "all_holding" && status.stage === "ready") {
            void action(async () => {
              await command("select", { joint_id: j.id });
              selected = j.id; clearAcks(); fields(); render();
            }, true);
          } else { selected = j.id; fields(); render(); }
        };
        joints.append(b);
      }
    }
    joints.querySelectorAll<HTMLButtonElement>("button").forEach(b => {
      const j = draft!.joints.find(j => j.id === b.dataset.joint)!;
      const label = j.id === "base" ? "Base" : j.id === "claw" ? "Claw" : j.id.replace("joint_", "J");
      const pulse = status?.targets?.[j.id];
      b.innerHTML = `<span class="servo-glyph" aria-hidden="true">⚙</span><strong></strong><span class="joint-value"></span><span class="joint-check" aria-hidden="true"></span>`;
      b.querySelector("strong")!.textContent = `${label} · ${j.channel}`;
      b.querySelector(".joint-value")!.textContent = pulse == null ? "— μs" : `${pulse} μs`;
      b.querySelector(".joint-check")!.textContent = j.state === "observed" ? "✓" : j.state === "needs_review" ? "!" : "";
      b.dataset.state = j.state;
      b.setAttribute("aria-label", `${label}, servo ${j.channel}, ${pulse ?? "unknown"} microseconds, ${j.state}`);
      b.disabled = !!status?.reserved && !(own && all && ready && !pending);
      b.setAttribute("aria-pressed", String(j.id === selected));
    });
    el<HTMLSelectElement>("type").disabled = !!status?.reserved;
    if (status?.reserved) el<HTMLSelectElement>("type").value = status.trial_type ?? "isolated";
    button("create").disabled = disabled || !!status?.reserved;
    el<HTMLSelectElement>("drafts").disabled = pending || !!status?.reserved;
    button("acquire").disabled = disabled || !draft || !status?.available || !!status.reserved;
    button("save").disabled = disabled || !draft;
    button("export").disabled = disabled || !draft;
    el("preparation").hidden = !(own && status?.stage === "acquired");
    el("power-on").hidden = !(own && status?.stage === "prepared");
    el("motion").hidden = !ready;
    button("prepare").disabled = disabled || !input("power-off").checked;
    button("ready").disabled = disabled || !input("ready-ack").checked;
    el("pulse").textContent = `Requested ${status?.requested_us ?? "unknown"} μs · Encoded ${status?.encoded_us?.toFixed(2) ?? "unknown"} μs`;
    const window = status?.windows?.[selected] ?? [1450, 1550];
    el("typed").hidden = !all;
    const targetKey = `${selected}:${status?.requested_us}`;
    if (input("target").dataset.target !== targetKey) {
      input("target").value = String(status?.requested_us ?? 1500);
      input("target").dataset.target = targetKey;
    }
    const windowKey = `${selected}:${window.join(":")}`;
    if (input("low").dataset.window !== windowKey) {
      input("low").value = String(window[0]); input("high").value = String(window[1]);
      input("low").dataset.window = windowKey;
    }
    button("apply").disabled = disabled || !ready || Date.now() < nextStep;
    button("window").disabled = disabled || !ready || !input("window-ack").checked;
    button("minus").disabled = disabled || !ready || Date.now() < nextStep || (status?.requested_us ?? window[0]) - 10 < window[0];
    button("plus").disabled = disabled || !ready || Date.now() < nextStep || (status?.requested_us ?? window[1]) + 10 > window[1];
    for (const k of ["initial", "minimum", "maximum"]) button(k).disabled = disabled || !ready || !input("mark-ack").checked;
    button("stop").hidden = !status?.reserved || status.stage === "awaiting_power_off";
    el("finish-panel").hidden = status?.stage !== "awaiting_power_off";
    button("finish").disabled = disabled || !input("finish-ack").checked;
  }
  async function listDrafts() {
    const drafts = await request<Draft[]>("drafts");
    const select = el<HTMLSelectElement>("drafts");
    select.replaceChildren(new Option("Select a record", ""), ...drafts.map(d => new Option(`${d.assembly_id} · ${d.observer} · ${d.id.slice(0, 8)}`, d.id)));
    select.value = draft?.id ?? "";
  }
  function clearAcks() {
    root.querySelectorAll<HTMLInputElement>('input[type="checkbox"]').forEach(i => { i.checked = false; });
  }
  async function end() {
    generation++;
    token = null;
    clearAcks();
    const trial = status?.trial_id;
    if (trial && status?.stage !== "awaiting_power_off") {
      try { status = await request<Status>("stop", { trial_id: trial }); }
      catch (error) { fail(error); }
    }
    render();
  }
  async function action(work: () => Promise<void>, stopOnFailure = false) {
    if (pending) return;
    pending = true;
    el("error").textContent = "";
    render();
    try { await heartbeat; await work(); }
    catch (error) {
      fail(error);
      if (stopOnFailure) await end();
    } finally { pending = false; render(); }
  }
  async function command(path: string, body: object = {}) {
    if (!token) return;
    const epoch = generation;
    const result = await request<Status>(path, { token, sequence: ++sequence, ...body });
    if (epoch === generation) status = result;
  }
  button("create").onclick = () => void action(async () => {
    draft = await request<Draft>("drafts", { assembly_id: input("assembly").value.trim(), observer: input("observer").value.trim() });
    selected = "base"; fields(); await listDrafts();
  });
  el<HTMLSelectElement>("drafts").onchange = () => void action(async () => {
    const id = el<HTMLSelectElement>("drafts").value;
    draft = id ? await request<Draft>(`drafts/${id}`) : null;
    selected = "base"; fields();
  });
  button("save").onclick = () => void action(async () => {
    // Refresh revision without replacing local unsaved text.
    await refreshDraft();
    draft = await request<Draft>(`drafts/${draft!.id}`, { revision: draft!.revision, joint_id: selected,
      direction: input("direction").value, notes: el<HTMLTextAreaElement>("notes").value });
  });
  button("acquire").onclick = () => void action(async () => {
    const epoch = ++generation;
    acquiring = true;
    let result: Session;
    try { result = await request<Session>("acquire", { draft_id: draft!.id, joint_id: selected, trial_type: el<HTMLSelectElement>("type").value }); }
    finally { acquiring = false; }
    if (epoch !== generation || !visible || document.hidden) {
      await request("stop", { trial_id: result.status.trial_id }); return;
    }
    token = result.token; sequence = 0; status = result.status; clearAcks();
    await refreshDraft();
    el<HTMLDetailsElement>("records").open = false;
  }, true);
  button("prepare").onclick = () => void action(async () => {
    await command("prepare", status?.trial_type === "all_holding"
      ? { power_off_and_supported: true } : { power_off_supported_isolated: true }); clearAcks();
  }, true);
  button("ready").onclick = () => void action(async () => {
    await command("ready", { settled_and_clear: true }); clearAcks();
  }, true);
  for (const [id, direction] of [["minus", -1], ["plus", 1]] as const) {
    button(id).onclick = () => void action(async () => {
      input("mark-ack").checked = false;
      await command("step", { direction });
      nextStep = Date.now() + 300;
      setTimeout(render, 310);
    }, true);
  }
  button("apply").onclick = () => void action(async () => {
    input("mark-ack").checked = false;
    if (!input("target").value.trim()) throw new Error("Enter a pulse width");
    await command("target", { joint_id: selected, pulse_us: input("target").valueAsNumber });
    nextStep = Date.now() + 300; setTimeout(render, 310);
  }, true);
  button("window").onclick = () => void action(async () => {
    await command("window", { joint_id: selected, min_us: input("low").valueAsNumber,
      max_us: input("high").valueAsNumber, reviewed: true });
    input("window-ack").checked = false;
    input("mark-ack").checked = false;
  }, true);
  for (const kind of ["initial", "minimum", "maximum"]) {
    button(kind).onclick = () => void action(async () => {
      await command("mark", { kind, expected_requested_us: status!.requested_us, settled_and_clear: true });
      input("mark-ack").checked = false;
      await refreshDraft();
    }, true);
  }
  button("stop").onclick = () => { void end(); };
  button("finish").onclick = () => void action(async () => {
    status = await request<Status>("finish", { trial_id: status!.trial_id,
      power_off_and_supported: true, observations_reviewed: input("review").checked });
    clearAcks(); await refreshDraft();
  });
  button("export").onclick = () => void action(async () => {
    const result = await request<Export>(`drafts/${draft!.id}/export`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `commissioning-${draft!.id}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  root.querySelectorAll('input[type="checkbox"]').forEach(i => i.addEventListener("change", render));
  window.addEventListener("blur", () => { if (token || acquiring) void end(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && (token || acquiring)) void end(); });
  window.addEventListener("pagehide", () => { if (token || acquiring) void end(); });
  setInterval(() => {
    if (token && !pending && !heartbeat && visible && connected) {
      heartbeat = (async () => {
        try { await command("heartbeat"); }
        catch (error) { fail(error); await end(); }
        finally { heartbeat = null; render(); }
      })();
    }
  }, 100);
  async function poll() {
    if (polling || token || pending || !visible) return;
    polling = true;
    const epoch = generation;
    try {
      const result = await request<Status>("status");
      if (epoch !== generation || token || pending) return;
      status = result;
      if (status.reserved && status.draft_id) {
        const changed = draft?.id !== status.draft_id || selected !== status.joint_id;
        const loaded = await request<Draft>(`drafts/${status.draft_id}`);
        if (epoch !== generation || token || pending) return;
        draft = loaded;
        selected = status.joint_id!;
        if (changed) fields();
      }
      render();
    } catch (error) { fail(error); }
    finally { polling = false; }
  }
  void listDrafts().catch(fail);
  void poll();
  setInterval(() => { void poll(); }, 500);
  return {
    end,
    setVisible(value: boolean) { visible = value; if (!value && (token || acquiring)) void end(); else if (value) void poll(); },
    updateWorkbench(value: Workbench | null) {
      connected = value !== null;
      if (!connected && token) void end();
      render();
    },
  };
}
