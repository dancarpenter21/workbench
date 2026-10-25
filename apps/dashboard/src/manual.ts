import type { components } from "@workbench/contracts";

type ManualStatus = components["schemas"]["ManualStatus"];
type ManualSession = components["schemas"]["ManualSession"];
type WorkbenchStatus = components["schemas"]["WorkbenchStatus"];
type Source = "keyboard" | "gamepad" | "pointer";

export function mountManual(root: HTMLElement, stopArm: () => void) {
  root.innerHTML = `
    <div class="panel-heading"><div><p class="eyebrow">DIRECT ARM CONTROL</p>
      <h2 id="manual-title">Manual joints</h2></div><span id="manual-mode" class="badge">Connecting…</span></div>
    <p id="manual-message" role="status">Connecting to arm controller…</p>
    <p id="manual-clearance">Free play · table-clearance checks off · claw-current monitoring active.</p>
    <div id="manual-joints" class="manual-joints" aria-label="Select joint"></div>
    <div class="manual-readout"><strong id="manual-selected">Select a joint</strong>
      <output id="manual-value">—</output><span id="manual-limits"></span></div>
    <p id="manual-direction" class="caption"></p>
    <div class="manual-settings">
      <label>Input<select id="manual-source"><option value="keyboard">Keyboard</option>
        <option value="gamepad">Xbox controller</option><option value="pointer">On-screen buttons</option></select></label>
      <label>Controller<select id="manual-controller"><option value="">No controller detected</option></select></label>
      <label class="ack"><input id="manual-precision" type="checkbox" /> Precision speed (25%)</label>
    </div>
    <p id="manual-controller-note" class="caption"></p>
    <div id="manual-preparation" hidden>
      <p>Before preparation, disconnect servo power and support the arm. Preparation programs 1500 µs on all six servos.</p>
      <label class="ack"><input id="manual-power-off" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <button id="manual-prepare" class="secondary" disabled>Prepare outputs</button>
    </div>
    <label id="manual-ready-label" class="ack" hidden><input id="manual-ready" type="checkbox" />
      I applied power using the commissioned procedure and checked that the arm is ready.</label>
    <div class="actions manual-actions"><button id="manual-enable" disabled>Enable manual control</button>
      <button id="manual-end" class="secondary" disabled>End manual control</button>
      <button id="manual-stop" class="danger">Stop arm</button></div>
    <div class="manual-jog"><button id="manual-minus" class="secondary" disabled>− Hold</button>
      <button id="manual-plus" class="secondary" disabled>+ Hold</button></div>
    <p id="manual-help" class="caption">Click this panel to focus. Select 1–6 or Up/Down; hold Space + Left/Right to jog. Hold Shift for precision. Escape stops the arm.</p>
    <p class="caption">V1 assumes successful movement after each PWM write. Stop retains the last target. Saved pulse limits are optional; no capsule measurements are required for Jog.</p>
    <p id="manual-error" role="alert"></p>`;
  root.tabIndex = 0;
  root.setAttribute("aria-labelledby", "manual-title");
  const guide = document.createElement("details");
  guide.className = "panel-guide";
  guide.innerHTML = "<summary>ⓘ Controls & safety guide</summary>";
  for (const paragraph of [...root.querySelectorAll<HTMLParagraphElement>("#manual-help, #manual-controller-note, :scope > p.caption")]) guide.append(paragraph);
  root.append(guide);
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!;
  const button = (id: string) => el<HTMLButtonElement>(id);
  const sourceSelect = el<HTMLSelectElement>("manual-source");
  const controllerSelect = el<HTMLSelectElement>("manual-controller");
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
  let source: Source = "keyboard";
  let neutralRequired = true;
  let pointerSpeed = 0;
  const keys = new Set<string>();
  let gamepads: Gamepad[] = [];
  let gamepadSignature = "";
  let lastSelection = 0;
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
    pointerSpeed = 0;
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

  function gamepad() {
    return gamepads.find((pad) => String(pad.index) === controllerSelect.value);
  }

  function input() {
    let speed = 0;
    let enable = false;
    let fine = precision.checked;
    if (source === "keyboard") {
      enable = keys.has("Space");
      speed = Number(keys.has("ArrowRight")) - Number(keys.has("ArrowLeft"));
      fine ||= keys.has("ShiftLeft") || keys.has("ShiftRight");
    } else if (source === "gamepad") {
      const pad = gamepad();
      if (pad?.mapping === "standard") {
        enable = !!pad.buttons[4]?.pressed;
        const axis = pad.axes[0] ?? 0;
        speed = Math.abs(axis) <= 0.15 ? 0 : Math.sign(axis) * (Math.abs(axis) - 0.15) / 0.85;
        fine ||= !!pad.buttons[5]?.pressed;
      }
    } else {
      enable = pointerSpeed !== 0;
      speed = pointerSpeed;
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
      : blocked ? blockedMessage
      : status?.message ?? "Connecting to arm controller…";
    const joints = status?.joints ?? [];
    const joint = joints[selected];
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
    el("manual-preparation").hidden = !status || status.simulated || status.prepared || !joints.length;
    el("manual-ready-label").hidden = !status || status.simulated || !status.prepared;
    button("manual-prepare").disabled = pending || !powerOff.checked || anotherOwner || blocked && !status?.recovery_required;
    const validController = source !== "gamepad" || gamepad()?.mapping === "standard";
    button("manual-enable").disabled = !connected || !visible || pending || active || anotherOwner || blocked ||
      !status?.available || !status.prepared || !validController || (!status.simulated && !ready.checked);
    button("manual-end").disabled = !active && !pending;
    button("manual-minus").disabled = button("manual-plus").disabled = !active || source !== "pointer";
    el("manual-help").textContent = source === "gamepad"
      ? "D-pad Up/Down selects a joint. Hold left bumper + left stick to jog; right bumper for precision. B stops the arm."
      : source === "pointer" ? "Hold a direction button to jog. Release to retain the current target."
      : "Click this panel to focus. Select 1–6 or Up/Down; hold Space + Left/Right to jog. Hold Shift for precision. Escape stops the arm.";
  }

  button("manual-enable").onclick = async () => {
    if (button("manual-enable").disabled) return;
    clearInput();
    const pad = gamepad();
    if (source === "gamepad" && (pad?.buttons[4]?.pressed || pad?.buttons[1]?.pressed || Math.abs(pad?.axes[0] ?? 0) > 0.15)) {
      fail(new Error("Release the controller buttons and center the stick before enabling."));
      return;
    }
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
  button("manual-end").onclick = end;
  button("manual-stop").onclick = () => { end(); stopArm(); };
  button("manual-prepare").onclick = async () => {
    if (button("manual-prepare").disabled) return;
    pending = true;
    render();
    try {
      status = await request<ManualStatus>("prepare", { power_disconnected_and_arm_supported: true });
      powerOff.checked = ready.checked = false;
    } catch (error) { fail(error); }
    finally { pending = false; render(); }
  };
  powerOff.onchange = ready.onchange = () => render();
  sourceSelect.onchange = () => { clearInput(); source = sourceSelect.value as Source; render(); root.focus(); };
  controllerSelect.onchange = () => { end(); lastSelection = 0; render(); };

  for (const [id, direction] of [["manual-minus", -1], ["manual-plus", 1]] as const) {
    const control = button(id);
    control.onpointerdown = (event) => {
      if (control.disabled || event.button !== 0) return;
      event.preventDefault();
      control.setPointerCapture(event.pointerId);
      pointerSpeed = direction;
    };
    control.onpointerup = control.onpointercancel = control.onlostpointercapture = () => { pointerSpeed = 0; };
    control.oncontextmenu = (event) => event.preventDefault();
  }

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

  function scanGamepads() {
    const previousController = gamepad();
    try { gamepads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter((p): p is Gamepad => !!p?.connected) : []; }
    catch { gamepads = []; }
    const signature = gamepads.map((p) => `${p.index}:${p.id}:${p.mapping}`).join("|");
    if (signature !== gamepadSignature) {
      const previous = controllerSelect.value;
      gamepadSignature = signature;
      controllerSelect.replaceChildren(...gamepads.map((pad) => {
        const option = document.createElement("option");
        option.value = String(pad.index);
        option.textContent = `${pad.id}${pad.mapping !== "standard" ? " (unsupported mapping)" : ""}`;
        return option;
      }));
      if (!gamepads.length) controllerSelect.add(new Option("No controller detected", ""));
      if (gamepads.some((p) => String(p.index) === previous)) controllerSelect.value = previous;
      if (token && source === "gamepad" && (!gamepad() || gamepad()?.id !== previousController?.id)) end();
      render();
    }
    el("manual-controller-note").textContent = !window.isSecureContext
      ? "Controller access requires HTTPS or localhost. Keyboard and on-screen controls remain available."
      : !navigator.getGamepads ? "This browser does not support controllers."
      : !gamepads.length ? "Connect an Xbox controller through USB or Bluetooth, then press a controller button to detect it."
      : gamepad()?.mapping !== "standard" ? "This controller mapping is unsupported; choose a standard Xbox-compatible controller."
      : "Controller connected · select Xbox controller as the input to use it.";
    if (source === "gamepad" && visible && root.contains(document.activeElement)) {
      const pad = gamepad();
      const selection = Number(!!pad?.buttons[13]?.pressed) - Number(!!pad?.buttons[12]?.pressed);
      if (selection && selection !== lastSelection) selectJoint(selected + selection);
      lastSelection = selection;
      const stop = !!pad?.buttons[1]?.pressed;
      if (stop && token) { end(); stopArm(); }
    }
    // Sample neutral state even between network updates.
    input();
    requestAnimationFrame(scanGamepads);
  }
  requestAnimationFrame(scanGamepads);

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
      status = { ...status, simulated: status?.simulated ?? true, available: false,
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
