import type { components } from "@workbench/contracts";

type ManualStatus = components["schemas"]["ManualStatus"];
type ManualSession = components["schemas"]["ManualSession"];
type WorkbenchStatus = components["schemas"]["WorkbenchStatus"];


export function mountManual(root: HTMLElement, stopArm: () => void) {
  root.innerHTML = `
    <div class="panel-heading"><div><p class="eyebrow">DIRECT ARM CONTROL</p>
      <h2 id="manual-title">Manual joints</h2></div><span id="manual-mode" class="badge">Connecting…</span></div>
    <p id="manual-message" role="status">Connecting to arm controller…</p>
    <button id="manual-reset" class="secondary" disabled>Reset all servos to 1500 µs</button>
    <p id="manual-gate" role="status"></p>
    <p id="manual-next" role="status"></p>
    <p id="manual-clearance">Free play · table-clearance checks off · claw-current monitoring active.</p>
    <div id="manual-joints" class="manual-joints" aria-label="Select joint"></div>
    <div class="manual-readout"><strong id="manual-selected">Select a joint</strong>
      <output id="manual-value">—</output><span id="manual-limits"></span></div>
    <p id="manual-direction" class="caption"></p>
    <label class="ack"><input id="manual-precision" type="checkbox" /> Precision speed (25%)</label>
    <div id="manual-pulses"></div>
    <p class="caption">Move applies the pulse immediately. Control movement increments carefully.</p>
    <div id="manual-preparation" hidden>
      <p>Before preparation, disconnect servo power and support the arm. Preparation programs 1500 µs on all six servos.</p>
      <label class="ack"><input id="manual-power-off" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <button id="manual-prepare" class="secondary" disabled>Prepare for Manual</button>
    </div>
    <label id="manual-ready-label" class="ack" hidden><input id="manual-ready" type="checkbox" />
      I applied power using the commissioned procedure and checked that the arm is ready.</label>
    <div class="actions manual-actions"><button id="manual-enable" disabled>Enable manual control</button>
      <button id="manual-end" class="secondary" disabled>End manual control</button>
      <button id="manual-stop" class="danger">Stop arm</button></div>
    <p id="manual-help" class="caption">Click this panel to focus. Select 1–6 or Up/Down; hold Space + Left/Right to jog. Hold Shift for precision. Escape stops the arm.</p>
    <p class="caption">V1 assumes successful movement after each PWM write. Stop retains the last target. Manual uses the nominal 500–2500 µs range, not verified mechanical limits. No commissioning is required.</p>
    <p id="manual-error" role="alert"></p>`;
  root.tabIndex = 0;
  root.setAttribute("aria-labelledby", "manual-title");
  const guide = document.createElement("details");
  guide.className = "panel-guide";
  guide.innerHTML = "<summary>ⓘ Controls & safety guide</summary>";
  for (const paragraph of [...root.querySelectorAll<HTMLParagraphElement>("#manual-help, :scope > p.caption")]) guide.append(paragraph);
  root.append(guide);
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  const button = (id: string) => el<HTMLButtonElement>(id);


  const precision = el<HTMLInputElement>("manual-precision");
  const ready = el<HTMLInputElement>("manual-ready");
  const powerOff = el<HTMLInputElement>("manual-power-off");
  let status: ManualStatus | null = null;
  let token: string | null = null;
  let sequence = 0;
  let selected = 0;
  let visible = true;
  let connected = false;
  let blocked = false;
  let blockedMessage = "";
  let pending = false;
  let generation = 0;
  const source = "keyboard";
  let neutralRequired = true;

  const keys = new Set<string>();



  let lastSentJoint: string | null = null;
  let sending = false;
  let polling = false;

  async function request<T>(path: string, body?: unknown, timeout = 1000): Promise<T> {
    const response = await fetch(`/api/coordinator/manual/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Manual command failed");
    return value as T;
  }

  function fail(error: unknown) {
    el("manual-error").textContent = error instanceof Error ? error.message : "Manual control unavailable";
  }

  function clearInput() {
    keys.clear();

    neutralRequired = true;
  }

  function end() {
    generation++;
    pending = false;
    const previous = token;
    token = null;
    clearInput();
    if (previous) {
      if (status) status = { ...status, active: false, message: "Ending manual control; waiting for the arm controller." };
      const attempt = generation;
      void request<ManualStatus>("release", { token: previous }).then((result) => {
        if (attempt === generation && !token) { status = result; render(); }
      }).catch(fail);
    }
    render();
  }

  function input() {
    let speed = 0;
    let enable = false;
    let fine = precision.checked;
    if (source === "keyboard") {
      enable = keys.has("Space");
      speed = Number(keys.has("ArrowRight")) - Number(keys.has("ArrowLeft"));
      fine ||= keys.has("ShiftLeft") || keys.has("ShiftRight");
    }
    if (neutralRequired) {
      if (!enable && speed === 0) neutralRequired = false;
      return 0;
    }
    return enable ? Math.max(-1, Math.min(1, speed)) * (fine ? 0.25 : 1) : 0;
  }

  function selectJoint(index: number) {
    selected = Math.max(0, Math.min((status?.joints?.length ?? 1) - 1, index));
    clearInput();
    render();
  }

  function render() {
    const active = token !== null;
    const anotherOwner = !!status?.active && !active;
    el("manual-mode").textContent = status?.simulated ? "SIMULATED FREE PLAY" : "FREE PLAY";
    el("manual-message").textContent = active ? "Manual control enabled · hold enable and direction to jog."
      : anotherOwner ? "Another session owns manual control."
      : blocked ? status?.message || blockedMessage
      : status?.message ?? "Connecting to arm controller…";
    const joints = status?.joints ?? [];
    const joint = joints[selected];
    if (!el("manual-pulses").children.length && joints.length) {
      for (const j of joints) {
        const row = document.createElement("div");
        row.innerHTML = `<label>${j.label} · Servo ${j.channel} · Pulse (µs)
          <input type="number" min="${j.min_us}" max="${j.max_us}" step="1" value="${j.commanded_us ?? 1500}" required /></label><button>Move</button>`;
        const field = row.querySelector("input")!;
        row.querySelector("button")!.onclick = async () => {
          if (!token || sending || !field.reportValidity()) return;
          clearInput();
          sending = true;
          const owner = token;
          try {
            const result = await request<ManualStatus>("position", { token: owner, sequence: sequence++, joint_id: j.id, pulse_us: field.valueAsNumber });
            if (token === owner) status = result;
          } catch (error) { end(); fail(error); }
          finally { sending = false; render(); }
        };
        el("manual-pulses").append(row);
      }
    }

    if (el("manual-joints").children.length !== joints.length) {
      el("manual-joints").replaceChildren(...joints.map((j, index) => {
        const control = document.createElement("button");
        control.className = "secondary";
        control.textContent = `${index + 1} · ${j.label}`;
        control.title = `Servo ${j.channel}`;
        control.onclick = () => { selectJoint(index); root.focus(); };
        return control;
      }));
    }
    [...el("manual-joints").children].forEach((control, index) => {
      control.setAttribute("aria-pressed", String(index === selected));
    });
    el("manual-selected").textContent = joint ? `${joint.label} · Servo ${joint.channel}` : "No joints available";
    el("manual-value").textContent = joint?.commanded_us != null ? `${joint.commanded_us.toFixed(1)} µs` : "Unknown";
    el("manual-limits").textContent = joint ? `${joint.min_us}–${joint.max_us} µs · max ${joint.max_rate_us_per_second} µs/s` : "";
    el("manual-direction").textContent = joint?.direction ?? "";
    el("manual-next").textContent = status?.next_action ?? "";
    el("manual-preparation").hidden = !status || (status.prepared && !status.recovery_required) || !joints.length;
    el("manual-ready-label").hidden = !status || status.simulated || !status.prepared;
    button("manual-prepare").disabled = pending || !powerOff.checked || !status?.can_prepare;
    const outdatedBackend = status !== null && typeof status.can_prepare !== "boolean";
    el("manual-gate").textContent = outdatedBackend
      ? "The backend is running an older version. Disconnect servo power, restart the app services, then reload this page."
      : active ? "Manual enabled. Move applies a pulse immediately."
      : !connected ? "Waiting for connection to the arm controller."
      : anotherOwner ? "Another session owns manual control."
      : blocked ? blockedMessage
      : !status?.prepared ? "Disconnect servo power, support the arm, and prepare outputs below."
      : !status?.available ? status?.message ?? "Manual control unavailable."
      : !status.simulated && !ready.checked ? "Check the arm-ready acknowledgement below, then enable manual control."
      : "Click Enable manual control below to unlock Move and Reset.";
    button("manual-reset").disabled = !active || sending || pending;
    const validController = true;
    button("manual-enable").disabled = !connected || !visible || pending || active || anotherOwner || blocked ||
      !status?.available || !status.prepared || !validController || (!status.simulated && !ready.checked);
    button("manual-end").disabled = !active && !pending;
    el("manual-pulses").querySelectorAll<HTMLButtonElement>("button").forEach(b => b.disabled = !active || sending);

  }

  button("manual-enable").onclick = async () => {
    if (button("manual-enable").disabled) return;
    clearInput();
    root.focus();
    pending = true;
    const attempt = ++generation;
    el("manual-error").textContent = "";
    render();
    try {
      const result = await request<ManualSession>("session", { ready_acknowledged: true });
      if (attempt !== generation || !visible || !document.hasFocus()) {
        await request("release", { token: result.token });
        return;
      }
      token = result.token;
      sequence = 0;
      lastSentJoint = null;
      status = result.status;
      root.focus();
    } catch (error) { fail(error); }
    finally { if (attempt === generation) pending = false; render(); }
  };
  button("manual-reset").onclick = async () => {
    if (!token || sending || pending) return;
    clearInput();
    sending = true;
    const owner = token;
    render();
    try {
      const result = await request<ManualStatus>("reset", { token: owner });
      if (token === owner) {
        status = result;
        el("manual-pulses").querySelectorAll<HTMLInputElement>("input").forEach(field => field.value = "1500");
        lastSentJoint = null;
      }
    } catch (error) { end(); fail(error); }
    finally { sending = false; render(); }
  };
  button("manual-end").onclick = end;
  button("manual-stop").onclick = () => { end(); stopArm(); };
  button("manual-prepare").onclick = async () => {
    if (button("manual-prepare").disabled) return;
    pending = true;
    render();
    try {
      el("manual-error").textContent = "";
      status = await request<ManualStatus>("prepare", { power_disconnected_and_arm_supported: true }, 5000);
      blocked = false;
      powerOff.checked = ready.checked = false;
    } catch (error) { fail(error); }
    finally { pending = false; render(); }
  };
  powerOff.onchange = ready.onchange = () => render();
  root.addEventListener("keydown", (event) => {
    if (!visible) return;
    if (event.code === "Escape" && (token || pending)) { event.preventDefault(); end(); stopArm(); return; }
    if (source !== "keyboard" || (event.target as HTMLElement).matches("input, textarea, select, [contenteditable]")) return;
    if (!["Space", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "ShiftLeft", "ShiftRight"].includes(event.code) && !/^Digit[1-6]$/.test(event.code)) return;
    event.preventDefault();
    if (event.repeat) return;
    if (!event.repeat && /^Digit[1-6]$/.test(event.code)) selectJoint(Number(event.code.slice(-1)) - 1);
    else if (!event.repeat && ["ArrowUp", "ArrowDown"].includes(event.code)) selectJoint(selected + (event.code === "ArrowUp" ? -1 : 1));
    keys.add(event.code);
  });
  window.addEventListener("keyup", (event) => keys.delete(event.code));
  root.addEventListener("focusout", (event) => {
    if (!root.contains(event.relatedTarget as Node | null)) end();
    else clearInput();
  });
  window.addEventListener("blur", end);
  window.addEventListener("pagehide", end);
  document.addEventListener("visibilitychange", () => { if (document.hidden) end(); });

  setInterval(async () => {
    if (!token || sending) return;
    if (!visible || document.hidden || !document.hasFocus()) { end(); return; }
    const owner = token;
    const joint = status?.joints?.[selected];
    if (!joint) { end(); return; }
    sending = true;
    try {
      const result = await request<ManualStatus>("input", {
        token: owner, sequence: sequence++, joint_id: joint.id, speed: lastSentJoint === joint.id ? input() : 0,
      }, 250);
      if (token === owner) { lastSentJoint = joint.id; status = result; render(); }
    } catch (error) { if (token === owner) { end(); fail(error); } }
    finally { sending = false; }
  }, 100);

  async function refresh() {
    if (!visible || polling || token || pending) return;
    polling = true;
    const attempt = generation;
    try {
      const result = await request<ManualStatus>("status");
      if (attempt === generation && !token) { status = result; connected = true; render(); }
    } catch (error) {
      connected = false;
      status = { blocker: "sensor", can_prepare: false, next_action: "Reconnect to the arm controller.", ...status, simulated: status?.simulated ?? true, available: false,
        prepared: status?.prepared ?? false, active: status?.active ?? false,
        recovery_required: status?.recovery_required ?? false, message: "Arm controller unavailable; reconnect before enabling manual control." };
      fail(error);
      render();
    }
    finally { polling = false; }
  }
  void refresh();
  setInterval(() => void refresh(), 500);
  return {
    end,
    setVisible(value: boolean) { visible = value; if (!value) end(); else void refresh(); },
    updateWorkbench(value: WorkbenchStatus | null) {
      connected = value !== null;
      blocked = !!value?.recovery_required || !!value?.operation && !["completed", "clarification", "rejected", "failed", "stopped", "status"].includes(value.operation.state);
      blockedMessage = value?.recovery_required
        ? "Inspect the arm and recover before enabling manual control."
        : "A retrieval request is active.";
      if (!value || value.recovery_required && token) end();
      if (!token && !pending && value?.manual) status = value.manual;
      render();
    },
  };
}
