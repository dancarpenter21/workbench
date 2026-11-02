import type { components } from "@workbench/contracts";

const channelLabels: Record<number, string> = { 5: "Base", 4: "Shoulder", 3: "Elbow", 2: "Wrist Bend", 1: "Wrist Rotation", 0: "Claw" };

type ManualStatus = components["schemas"]["ManualStatus"];
type ManualSession = components["schemas"]["ManualSession"];
type WorkbenchStatus = components["schemas"]["WorkbenchStatus"];

export function mountManual(root: HTMLElement, stopArm: () => void) {
  root.innerHTML = `
    <div class="panel-heading"><div><p class="eyebrow">DIRECT ARM CONTROL</p>
      <h2 id="manual-title">Manual joints</h2></div><span id="manual-mode" class="badge">Connecting…</span></div>
    <p id="manual-message" role="status">Connecting to arm controller…</p>
    <div class="actions manual-actions"><button id="manual-enable" disabled>Enable jogging</button>
      <button id="manual-end" class="secondary" disabled>End control</button>
      <button id="manual-reset" class="secondary" disabled>Reset to 1500 µs</button></div>
    <p id="manual-direction" class="caption"></p>
    <label class="ack"><input id="manual-precision" type="checkbox" /> Precision speed (25%)</label>
    <div id="manual-pulses"></div>
    <p class="caption">Move applies the pulse immediately. Control movement increments carefully.</p>
    <div id="manual-preparation" hidden>
      <p>Before preparation, disconnect servo power and support the arm. Preparation programs 1500 µs on all six servos.</p>
      <label class="ack"><input id="manual-power-off" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <button id="manual-prepare" class="secondary" disabled>Prepare for Manual</button>
    </div>
    <p id="manual-help" class="caption">Click this panel to focus. Select 1–6 or Up/Down; hold Space + Left/Right to jog. Hold Shift for precision. Escape stops the arm.</p>
    <p class="caption">V1 assumes successful movement after each PWM write. Stop retains the last target. Manual uses each servo’s independent assembly bounds. In-bounds combinations can still collide. Saved commissioning limits apply only to LLM harness mode; no commissioning is required for Manual.</p>
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
  let commanding = false;
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
        row.dataset.channel = String(j.channel);
        row.innerHTML = `<label for="manual-pulse-${j.id}">${channelLabels[j.channel] ?? j.label} · Channel ${j.channel}</label>
          <div class="manual-pulse-controls">
            <span class="caption" data-min></span><span aria-hidden="true">&lt;</span>
            <input id="manual-pulse-${j.id}" type="number" min="${j.min_us}" max="${j.max_us}" step="1" value="1500" required />
            <span aria-hidden="true">&lt;</span><span class="caption" data-max></span>
            <button type="button">Move</button>
          </div>`;
        const field = row.querySelector("input")!;
        const move = row.querySelector("button")!;
        field.addEventListener("input", () => render());
        field.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          if (!event.repeat) move.click();
        });
        move.onclick = async () => {
          if (move.disabled || commanding || !field.reportValidity()) return;
          const pulse = field.valueAsNumber;
          commanding = true;
          clearInput();
          render();
          try {
            while (sending) await new Promise(resolve => setTimeout(resolve, 10));
            await acquire();
            const owner = token;
            if (!owner) return;
            const currentJoint = status?.joints?.find(joint => joint.id === j.id);
            if (!currentJoint || pulse < currentJoint.min_us || pulse > currentJoint.max_us) return;
            const result = await request<ManualStatus>("position", { token: owner, sequence: sequence++, joint_id: j.id, pulse_us: pulse });
            if (token === owner) status = result;
          } catch (error) { end(); fail(error); }
          finally { commanding = false; render(); }
        };
        el("manual-pulses").append(row);
      }
    }

    [...el("manual-pulses").children].forEach((row, index) => {
      row.setAttribute("data-selected", String(index === selected));
    });
    el("manual-direction").textContent = joint?.direction ?? "";
    el("manual-preparation").hidden = !status || (status.prepared && !status.recovery_required) || !joints.length;

    button("manual-prepare").disabled = pending || !powerOff.checked || !status?.can_prepare;
    const canMove = connected && !anotherOwner && !blocked && !!status?.available && !!status.prepared;
    button("manual-reset").disabled = (!active && !canMove) || commanding || pending;
    button("manual-enable").disabled = !connected || !visible || pending || active || anotherOwner || blocked ||
      !status?.available || !status.prepared;
    button("manual-end").disabled = !active && !pending;
    [...el("manual-pulses").children].forEach((row, index) => {
      const field = row.querySelector("input")!;
      const move = row.querySelector("button")!;
      const currentJoint = joints[index];
      if (currentJoint) {
        field.min = String(currentJoint.min_us);
        field.max = String(currentJoint.max_us);
        row.querySelector("[data-min]")!.textContent = `${currentJoint.min_us} µs`;
        row.querySelector("[data-max]")!.textContent = `${currentJoint.max_us} µs`;
      }
      const invalid = !field.validity.valid;
      field.setAttribute("aria-invalid", String(invalid));
      field.disabled = commanding || pending || !currentJoint;
      move.disabled = (!active && !canMove) || commanding || pending || !currentJoint || invalid;
    });

  }

  async function acquire() {
    if (token) return;
    if (button("manual-enable").disabled) throw new Error("Manual control is unavailable.");
    clearInput();
    root.focus();
    pending = true;
    const attempt = ++generation;
    el("manual-error").textContent = "";
    render();
    try {
      const result = await request<ManualSession>("session", { ready_acknowledged: true });
      if (attempt !== generation) {
        await request("release", { token: result.token });
        return;
      }
      token = result.token;
      sequence = 0;
      lastSentJoint = null;
      status = result.status;
      root.focus();
    } finally { if (attempt === generation) pending = false; render(); }
  }
  button("manual-enable").onclick = () => { void acquire().catch(fail); };
  button("manual-reset").onclick = async () => {
    if (commanding || pending) return;
    commanding = true;
    clearInput();
    render();
    try {
      while (sending) await new Promise(resolve => setTimeout(resolve, 10));
      await acquire();
      const owner = token;
      if (!owner) return;
      const result = await request<ManualStatus>("reset", { token: owner });
      if (token === owner) {
        status = result;
        el("manual-pulses").querySelectorAll<HTMLInputElement>("input").forEach(field => field.value = "1500");
        lastSentJoint = null;
      }
    } catch (error) { end(); fail(error); }
    finally { commanding = false; render(); }
  };
  button("manual-end").onclick = end;
  button("manual-prepare").onclick = async () => {
    if (button("manual-prepare").disabled) return;
    pending = true;
    render();
    try {
      el("manual-error").textContent = "";
      status = await request<ManualStatus>("prepare", { power_disconnected_and_arm_supported: true }, 5000);
      blocked = false;
      powerOff.checked = false;
    } catch (error) { fail(error); }
    finally { pending = false; render(); }
  };
  powerOff.onchange = () => render();
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
  root.addEventListener("focusout", clearInput);
  window.addEventListener("blur", clearInput);
  window.addEventListener("pagehide", end);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearInput(); });

  setInterval(async () => {
    if (!token || sending || commanding) return;
    if (!visible || document.hidden || !document.hasFocus()) clearInput();
    const owner = token;
    const joint = status?.joints?.[selected];
    if (!joint) { end(); return; }
    sending = true;
    // Sample released keys even when the first request for a joint must be neutral.
    const speed = input();
    try {
      const result = await request<ManualStatus>("input", {
        token: owner, sequence: sequence++, joint_id: joint.id, speed: lastSentJoint === joint.id ? speed : 0,
      }, 250);
      if (token === owner) { lastSentJoint = joint.id; status = result; render(); }
    } catch (error) { if (token === owner) { end(); fail(error); } }
    finally { sending = false; render(); }
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
    setVisible(value: boolean) { visible = value; clearInput(); if (value) void refresh(); },
    updateWorkbench(value: WorkbenchStatus | null) {
      connected = value !== null;
      blocked = !!value?.manual?.recovery_required || !!value?.operation && !["completed", "clarification", "rejected", "failed", "stopped", "status"].includes(value.operation.state);
      blockedMessage = value?.manual?.recovery_required
        ? "Inspect the arm and recover before enabling manual control."
        : "A retrieval request is active.";
      if (!value || value.manual?.recovery_required && token) end();
      if (!token && !pending && value?.manual) status = value.manual;
      render();
    },
  };
}
