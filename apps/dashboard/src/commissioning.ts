import type { components } from "@workbench/contracts";

type Status = components["schemas"]["CommissionStatus"];
type Draft = components["schemas"]["CommissionDraft"];
type Bounds = components["schemas"]["CommissionBounds"];
type Session = components["schemas"]["CommissionSession"];
type Workbench = components["schemas"]["WorkbenchStatus"];
const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];
const label = (id: string) => id === "base" ? "Base" : id === "claw" ? "Claw" : id.replace("joint_", "Joint ");

export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `
    <div class="panel-heading"><h2>Joint pulse bounds</h2><span id="trial-domain" class="caption"></span></div>
    <div id="trial-joints" class="manual-joints pulse-joints" aria-label="Choose joint"></div>
    <p id="trial-message" role="status"></p>
    <div id="trial-motion" hidden>
      <p id="trial-pulse" class="caption"></p>
      <form id="trial-position-form" class="manual-settings">
        <label>Target pulse (μs)<input id="trial-target" type="number" min="500" max="2500" step="1" required /></label>
        <button id="trial-apply" type="submit">Move</button>
      </form>
      <div id="trial-steps" class="pulse-steps" aria-label="Move joint by microseconds"></div>
      <div id="trial-feedback" hidden>
        <p>Has the joint settled safely with clear movement?</p>
        <div class="actions"><button id="trial-settled">OK</button><button id="trial-unsafe" class="danger">Not OK</button></div>
      </div>
      <div class="actions pulse-capture">
        <button id="trial-minimum" class="secondary">Set minimum</button>
        <button id="trial-maximum" class="secondary">Set maximum</button>
      </div>
      <p id="trial-capture-note" class="caption">Move and confirm OK before capturing a bound.</p>
      <div id="trial-window-panel" hidden>
        <p id="trial-window-range" class="caption"></p>
        <label class="ack"><input id="trial-window-ack" type="checkbox" /> I checked clearance for the extended test range.</label>
        <div class="actions">
          <button id="trial-extend-low" class="secondary" hidden>Extend lower range by 50 μs</button>
          <button id="trial-extend-high" class="secondary" hidden>Extend upper range by 50 μs</button>
        </div>
      </div>
    </div>
    <button id="trial-stop" class="danger" hidden>Stop — hold position</button>
    <div id="trial-finish-panel" hidden>
      <p>Support the arm and disconnect servo power.</p>
      <label class="ack"><input id="trial-finish-ack" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <button id="trial-finish">Finish</button>
    </div>
    <div id="trial-reset-panel" hidden>
      <label class="ack"><input id="trial-reset-off" type="checkbox" /> Servo power is disconnected; the arm is supported and the sensor fault has been inspected.</label>
      <button id="trial-reset" class="secondary">Reset sensor fault</button>
    </div>
    <p id="trial-error" role="alert"></p>
    <p class="caption pulse-help">The first move initializes all six servos to 1500 μs. Other joints hold their last command. Stop holds position; Not OK attempts PWM shutdown. Cut physical servo power if unsafe. Pulses are commands, not measured angles; PWM resolution is approximately 4.88 μs, so small changes may produce the same output. Saved bounds do not enable automated motion.</p>`;
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#trial-${id}`)!;
  const button = (id: string) => el<HTMLButtonElement>(id);
  const input = (id: string) => el<HTMLInputElement>(id);
  let status: Status | null = null;
  let bounds: Bounds | null = null;
  let selected = "base";
  let token: string | null = null;
  let sequence = 0;
  let pending = false;
  let visible = false;
  let connected = false;
  let generation = 0;
  let polling = false;
  let acquiring = false;
  let heartbeat: Promise<void> | null = null;
  let nextMove = 0;
  let savedMessage = "";
  const confirmed = new Map<string, string>();

  async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`/api/coordinator/commissioning/${path}`, {
      method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(1500), keepalive: path === "stop",
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Request failed");
    return value as T;
  }
  function fail(error: unknown) { el("error").textContent = error instanceof Error ? error.message : "Unavailable"; }
  const current = () => status?.reserved ? (status.requested_us ?? 1500) : 1500;
  const testWindow = () => status?.windows?.[selected] ?? [1450, 1550];
  const moveLimit = () => status?.max_move_us ?? 40;
  async function refreshBounds() { bounds = await request<Bounds>("bounds"); }
  for (const id of joints) {
    const b = document.createElement("button");
    b.className = "secondary"; b.dataset.joint = id;
    b.innerHTML = `<span>${label(id)}</span><small class="pulse-min"></small><small class="pulse-max"></small>`;
    b.title = `Servo ${5 - joints.indexOf(id)}`;
    b.onclick = () => void action(async () => {
      if (token) await command("select", { joint_id: id });
      selected = id; savedMessage = ""; clearAcks();
    }, true);
    el("joints").append(b);
  }
  for (const delta of [-40, -20, -10, -1, 1, 10, 20, 40]) {
    const b = document.createElement("button");
    b.className = "secondary"; b.dataset.delta = String(delta);
    b.textContent = `${delta > 0 ? "+" : "−"}${Math.abs(delta)} μs`;
    b.onclick = () => { if (!b.disabled) move(current() + delta); };
    el("steps").append(b);
  }
  function render() {
    const ready = !!token && status?.stage === "ready" && status.claw_current?.healthy === true;
    const awaiting = !!token && status?.stage === "awaiting_feedback";
    const idle = !!status?.available && !status.reserved && status.claw_current?.healthy === true;
    const disabled = pending || !connected;
    const [low, high] = testWindow();
    const pulse = current();
    el("domain").textContent = status?.simulated ? "SIMULATED" : status ? "Physical arm" : "Connecting…";
    el("message").textContent = savedMessage || (!connected ? "Connecting…" : status?.stage === "awaiting_power_off" ? "Testing stopped." : status && !status.available ? status.message : "");
    el("joints").querySelectorAll<HTMLButtonElement>("button").forEach(b => {
      const id = b.dataset.joint!;
      b.setAttribute("aria-pressed", String(id === selected));
      b.disabled = disabled || (!!status?.reserved && !(ready && status.trial_type === "all_holding"));
      const saved = bounds?.joints[id];
      b.querySelector(".pulse-min")!.textContent = `Min: ${saved?.minimum ? `${saved.minimum.requested_us} μs` : "Not set"}`;
      b.querySelector(".pulse-max")!.textContent = `Max: ${saved?.maximum ? `${saved.maximum.requested_us} μs` : "Not set"}`;
    });
    el("motion").hidden = !idle && !ready && !awaiting;
    el("feedback").hidden = !awaiting;
    el("position-form").hidden = awaiting;
    el("steps").hidden = awaiting;
    root.querySelector<HTMLElement>(".pulse-capture")!.hidden = awaiting;
    el("capture-note").hidden = awaiting;
    const canMove = !disabled && (ready || idle) && Date.now() >= nextMove;
    const target = input("target").valueAsNumber;
    button("apply").disabled = !canMove || !Number.isFinite(target) || target < low || target > high || Math.abs(target - pulse) > moveLimit();
    input("target").disabled = disabled || (!ready && !idle);
    button("settled").disabled = disabled || !awaiting;
    button("unsafe").disabled = disabled || !awaiting;
    const targetKey = `${selected}:${status?.reserved ? pulse : "idle"}`;
    if (input("target").dataset.target !== targetKey) {
      input("target").value = String(pulse); input("target").dataset.target = targetKey;
      button("apply").disabled = !canMove;
    }
    el("steps").querySelectorAll<HTMLButtonElement>("button").forEach(b => {
      const delta = Number(b.dataset.delta);
      b.disabled = !canMove || Math.abs(delta) > moveLimit() || pulse + delta < low || pulse + delta > high;
    });
    el("pulse").textContent = `${label(selected)} · Current command: ${status?.reserved ? `${pulse} μs` : "—"} · Up to ${moveLimit()} μs per move`;
    const captureReady = ready && !!status?.capture_ready && confirmed.has(selected);
    for (const kind of ["minimum", "maximum"] as const) {
      const opposite = bounds?.joints[selected]?.[kind === "minimum" ? "maximum" : "minimum"];
      const ordered = !opposite || (kind === "minimum" ? pulse < opposite.requested_us : pulse > opposite.requested_us);
      button(kind).disabled = disabled || !captureReady || !ordered;
      button(kind).title = ordered ? "Save this confirmed pulse without moving" : "Minimum must be below maximum";
    }
    el("capture-note").textContent = captureReady ? "Capture the current pulse as a bound. Recapturing replaces the saved value." : "Move and confirm OK before capturing a bound.";
    button("stop").hidden = !status?.reserved || status.stage === "awaiting_power_off";
    el("finish-panel").hidden = status?.stage !== "awaiting_power_off";
    button("finish").disabled = disabled || !input("finish-ack").checked;
    el("reset-panel").hidden = !!status?.reserved || !status?.claw_current?.fault;
    button("reset").disabled = disabled || !input("reset-off").checked;
    el("window-panel").hidden = !ready;
    el("window-range").textContent = `Test range: ${low}–${high} μs · resets each session`;
    button("extend-low").hidden = pulse - low > 40 || low <= 500;
    button("extend-high").hidden = high - pulse > 40 || high >= 2500;
    input("window-ack").parentElement!.hidden = button("extend-low").hidden && button("extend-high").hidden;
    for (const side of ["low", "high"]) button(`extend-${side}`).disabled = disabled || !ready || !input("window-ack").checked;
  }
  function clearAcks() { for (const id of ["finish-ack", "window-ack", "reset-off"]) input(id).checked = false; }
  async function end() {
    generation++; token = null; confirmed.clear(); clearAcks();
    if (status?.trial_id && status.stage !== "awaiting_power_off") {
      try { status = await request<Status>("stop", { trial_id: status.trial_id }); } catch (error) { fail(error); }
    }
    render();
  }
  async function action(work: () => Promise<void>, stopOnFailure = false) {
    if (pending) return;
    pending = true; el("error").textContent = ""; render();
    try { await heartbeat; await work(); }
    catch (error) { fail(error); if (stopOnFailure) await end(); }
    finally { pending = false; render(); }
  }
  async function command(path: string, body: object = {}) {
    if (!token) return;
    const epoch = generation;
    const result = await request<Status>(path, { token, sequence: ++sequence, ...body });
    if (epoch === generation) status = result;
  }
  async function initialize() {
    const epoch = ++generation; acquiring = true;
    let result: Session;
    try {
      // Fresh internal evidence for each session; persistent bounds are independent.
      const context = await request<Bounds>("bounds");
      const draft = await request<Draft>("drafts", {
        assembly_id: context.assembly_id, observer: "Operator name not recorded",
      });
      result = await request<Session>("initialize", { draft_id: draft.id, joint_id: selected,
        conditions: "User-requested neutral initialization and pulse bounds test" });
    } finally { acquiring = false; }
    if (epoch !== generation || !visible || document.hidden || !document.hasFocus()) {
      await request("stop", { trial_id: result.status.trial_id }); return false;
    }
    token = result.token; sequence = 0; status = result.status; savedMessage = ""; clearAcks(); confirmed.clear();
    return true;
  }
  button("reset").onclick = () => void action(async () => {
    status = await request<Status>("reset-current", { power_off_and_supported: true });
    input("reset-off").checked = false; savedMessage = "";
  });
  function move(target: number) {
    const [low, high] = testWindow();
    if (!Number.isFinite(target) || Math.abs(target - current()) > moveLimit() || target < low || target > high) {
      fail(new Error(`Enter a pulse within ${moveLimit()} μs of the current command and inside ${low}–${high} μs.`)); return;
    }
    void action(async () => {
      if (!token && !await initialize()) return;
      confirmed.delete(selected); clearAcks();
      await command("target", { joint_id: selected, pulse_us: target }); savedMessage = "";
      nextMove = Date.now() + 300; setTimeout(render, 310);
    }, true);
  }
  el<HTMLFormElement>("position-form").onsubmit = event => {
    event.preventDefault();
    if (!button("apply").disabled) move(input("target").valueAsNumber);
  };
  input("target").oninput = render;
  for (const [id, outcome] of [["settled", "settled_clear"], ["unsafe", "unsafe"]] as const) {
    button(id).onclick = () => void action(async () => {
      const commandId = status!.pending_command!.command_id;
      await command("feedback", { command_id: commandId,
        joint_id: selected, expected_requested_us: status!.requested_us, outcome,
        observed_direction: "unknown", physical_frame: "Not recorded in pulse bounds test",
        notes: outcome === "settled_clear" ? "User confirmed position OK" : "User reported position Not OK" });
      savedMessage = outcome === "settled_clear" ? "OK — choose a bound or keep testing." : "Not OK — cut servo power.";
      if (outcome === "settled_clear" && status?.capture_ready) confirmed.set(selected, commandId);
      else { token = null; confirmed.clear(); }
    }, true);
  }
  for (const kind of ["minimum", "maximum"] as const) {
    button(kind).onclick = () => void action(async () => {
      await command("mark", { kind, joint_id: selected, command_id: confirmed.get(selected),
        expected_requested_us: current(), settled_and_clear: true });
      await refreshBounds();
      savedMessage = `${label(selected)} ${kind} saved: ${bounds!.joints[selected][kind]!.requested_us} μs.`;
    });
  }
  for (const side of ["low", "high"]) {
    button(`extend-${side}`).onclick = () => void action(async () => {
      const [low, high] = testWindow();
      await command("window", { joint_id: selected,
        min_us: side === "low" ? Math.max(500, low - 50) : low,
        max_us: side === "high" ? Math.min(2500, high + 50) : high, reviewed: true });
      input("window-ack").checked = false; savedMessage = "Test range extended. No movement commanded.";
    }, true);
  }
  button("stop").onclick = () => { void end(); };
  button("finish").onclick = () => void action(async () => {
    status = await request<Status>("finish", { trial_id: status!.trial_id, power_off_and_supported: true });
    token = null; confirmed.clear(); clearAcks(); await refreshBounds(); savedMessage = "Testing finished. Saved bounds retained.";
  });
  for (const id of ["finish-ack", "window-ack", "reset-off"]) input(id).onchange = render;
  window.addEventListener("blur", () => { if (token || acquiring) void end(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && (token || acquiring)) void end(); });
  window.addEventListener("pagehide", () => { if (token || acquiring) void end(); });
  setInterval(() => {
    if (token && !pending && !heartbeat && visible && connected) {
      heartbeat = (async () => {
        try { await command("heartbeat"); } catch (error) { fail(error); await end(); }
        finally { heartbeat = null; render(); }
      })();
    }
  }, 100);
  async function poll() {
    if (polling || token || pending || !visible) return;
    polling = true; const epoch = generation;
    try {
      const [result, saved] = await Promise.all([request<Status>("status"), request<Bounds>("bounds")]);
      if (epoch !== generation || token || pending) return;
      status = result; bounds = saved;
      if (status.reserved && status.joint_id) selected = status.joint_id;
      render();
    } catch (error) { fail(error); }
    finally { polling = false; }
  }
  setInterval(() => { void poll(); }, 500);
  return {
    end,
    setVisible(value: boolean) { visible = value; if (!value && (token || acquiring)) void end(); else if (value) void poll(); },
    updateWorkbench(value: Workbench | null) { connected = value !== null; if (!connected && token) void end(); render(); },
  };
}
