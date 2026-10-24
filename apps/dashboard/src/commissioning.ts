import type { components } from "@workbench/contracts";
import { mountCurrentScale } from "./current-scale";

type Status = components["schemas"]["CommissionStatus"];
type Draft = components["schemas"]["CommissionDraft"];
type Session = components["schemas"]["CommissionSession"];
type Workbench = components["schemas"]["WorkbenchStatus"];
type Export = components["schemas"]["CommissionExport"];
const joints = ["base", "joint_2", "joint_3", "joint_4", "joint_5", "claw"];
const label = (id: string) => id === "base" ? "Base" : id === "claw" ? "Claw" : id.replace("joint_", "Joint ");

export function mountCommissioning(root: HTMLElement) {
  root.innerHTML = `
    <div id="trial-joints" class="manual-joints" aria-label="Choose joint"></div>
    <p id="trial-message" role="status"></p>
    <div id="trial-motion" hidden>
      <form id="trial-position-form" class="manual-settings">
        <label>Position (μs)<input id="trial-target" type="number" min="500" max="2500" step="1" required /></label>
        <button id="trial-apply" type="submit">Move</button>
      </form>
      <p id="trial-pulse" class="caption"></p>
      <div id="trial-feedback" hidden>
        <p>Is this position OK?</p>
        <div class="actions"><button id="trial-settled">OK</button><button id="trial-unsafe" class="danger">Not OK</button></div>
      </div>
    </div>
    <button id="trial-stop" class="danger" hidden>Stop</button>
    <div id="trial-finish-panel" hidden>
      <p>Support the arm and disconnect servo power.</p>
      <label class="ack"><input id="trial-finish-ack" type="checkbox" /> Servo power is disconnected and the arm is supported.</label>
      <button id="trial-finish">Finish</button>
    </div>
    <p id="trial-error" role="alert"></p>
    <details id="trial-settings"><summary>Setup & history</summary>
      <p id="trial-domain"></p>
      <div class="manual-settings">
        <label>Assembly<input id="trial-assembly" maxlength="200" placeholder="Not named" /></label>
        <label>Operator<input id="trial-observer" maxlength="200" placeholder="Not recorded" /></label>
        <button id="trial-create" class="secondary">New record</button>
        <label>Saved record<select id="trial-drafts"><option value="">New record on Start</option></select></label>
      </div>
      <details id="trial-window-panel"><summary>Position range</summary>
        <div class="manual-settings"><label>Minimum (μs)<input id="trial-low" type="number" min="500" max="2500" /></label>
          <label>Maximum (μs)<input id="trial-high" type="number" min="500" max="2500" /></label>
          <label class="ack"><input id="trial-window-ack" type="checkbox" /> I checked clearance for this range.</label>
          <button id="trial-window" class="secondary">Save range</button></div>
      </details>
      <div id="trial-reset-panel" hidden>
        <label class="ack"><input id="trial-reset-off" type="checkbox" /> Servo power is disconnected; the arm is supported and the sensor fault has been inspected.</label>
        <button id="trial-reset" class="secondary">Reset sensor fault</button>
      </div>
      <p>The first Move initializes all six servos to neutral (1500 μs). Each move is limited to 10 μs. Positions are commanded pulses, not measured joint angles. Stop holds the last target; Not OK attempts output shutdown. Cut physical servo power if movement is unsafe.</p>
      <pre id="trial-targets" aria-label="All servo targets"></pre>
      <div id="trial-history"></div>
      <button id="trial-export" class="secondary">Export record</button>
      <div id="trial-scale"></div>
    </details>`;
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>(`#trial-${id}`)!;
  const button = (id: string) => el<HTMLButtonElement>(id);
  const input = (id: string) => el<HTMLInputElement>(id);
  const scales = mountCurrentScale(el("scale"));
  let status: Status | null = null;
  let draft: Draft | null = null;
  let selected = "base";
  let token: string | null = null;
  let sequence = 0;
  let pending = false;
  let visible = true;
  let connected = false;
  let generation = 0;
  let polling = false;
  let acquiring = false;
  let heartbeat: Promise<void> | null = null;
  let nextMove = 0;
  let savedMessage = "";

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
  async function refreshDraft() { if (draft) draft = await request<Draft>(`drafts/${draft.id}`); }
  for (const id of joints) {
    const b = document.createElement("button");
    b.className = "secondary"; b.dataset.joint = id; b.textContent = label(id);
    b.title = `Servo ${5 - joints.indexOf(id)}`;
    b.onclick = () => void action(async () => {
      if (token) await command("select", { joint_id: id });
      selected = id; savedMessage = "";
    }, true);
    el("joints").append(b);
  }
  function render() {
    scales.update(status);
    const ready = !!token && status?.stage === "ready" && status.claw_current?.healthy === true;
    const awaiting = !!token && status?.stage === "awaiting_feedback";
    const idle = !!status?.available && !status.reserved && status.claw_current?.healthy === true;
    const disabled = pending || !connected;
    el("domain").textContent = status?.simulated ? "SIMULATED — no physical commissioning evidence" : "Physical arm";
    el("message").textContent = savedMessage || (!connected ? "Connecting…" : status?.stage === "awaiting_feedback" ? "" : status?.stage === "awaiting_power_off" ? "Testing stopped." : "");
    el("joints").querySelectorAll<HTMLButtonElement>("button").forEach(b => {
      b.setAttribute("aria-pressed", String(b.dataset.joint === selected));
      b.disabled = disabled || (!!status?.reserved && !(ready && status.trial_type === "all_holding"));
    });
    el("motion").hidden = !idle && !ready && !awaiting;
    el("feedback").hidden = !awaiting;
    button("apply").disabled = disabled || (!ready && !idle) || Date.now() < nextMove;
    input("target").disabled = disabled || (!ready && !idle);
    button("settled").disabled = disabled || !awaiting;
    button("unsafe").disabled = disabled || !awaiting;
    const targetKey = `${selected}:${status?.requested_us}`;
    if (input("target").dataset.target !== targetKey) {
      input("target").value = String(status?.requested_us ?? 1500); input("target").dataset.target = targetKey;
    }
    el("pulse").textContent = `${label(selected)} · Current command: ${status?.requested_us ?? "—"} μs · Up to 10 μs per move`;
    button("stop").hidden = !status?.reserved || status.stage === "awaiting_power_off";
    el("finish-panel").hidden = status?.stage !== "awaiting_power_off";
    button("finish").disabled = disabled || !input("finish-ack").checked;
    el("reset-panel").hidden = !!status?.reserved || !status?.claw_current?.fault;
    button("reset").disabled = disabled || !input("reset-off").checked;
    for (const id of ["assembly", "observer"]) input(id).disabled = !!status?.reserved;
    button("create").disabled = disabled || !!status?.reserved;
    el<HTMLSelectElement>("drafts").disabled = disabled || !!status?.reserved;
    button("export").disabled = disabled || !draft;
    el("window-panel").hidden = status?.trial_type !== "all_holding";
    button("window").disabled = disabled || !ready || !input("window-ack").checked;
    const bounds = status?.windows?.[selected] ?? [1450, 1550];
    if (input("low").dataset.key !== `${selected}:${bounds}`) {
      input("low").value = String(bounds[0]); input("high").value = String(bounds[1]);
      input("low").dataset.key = `${selected}:${bounds}`;
    }
    el("targets").textContent = Object.entries(status?.targets ?? {}).map(([id, pulse]) => `${label(id)}: ${pulse} μs`).join("\n");
    const history = el("history");
    const historyKey = `${draft?.id}:${draft?.revision}:${selected}`;
    if (history.dataset.key !== historyKey) {
      history.replaceChildren(); history.dataset.key = historyKey;
      for (const attempt of draft?.joints.find(j => j.id === selected)?.attempts ?? []) {
        for (const feedback of attempt.feedback ?? []) {
          const row = document.createElement("p");
          row.textContent = `${feedback.requested_us} μs — ${feedback.outcome === "settled_clear" ? "OK" : "Not OK"} · ${(feedback.observed_at ? new Date(feedback.observed_at).toLocaleString() : "")}`;
          history.append(row);
        }
      }
    }
  }
  async function listDrafts() {
    const drafts = await request<Draft[]>("drafts");
    const select = el<HTMLSelectElement>("drafts");
    select.replaceChildren(new Option("New record on Start", ""), ...drafts.map(d => new Option(`${d.assembly_id} · ${d.id.slice(0, 8)}`, d.id)));
    select.value = draft?.id ?? "";
  }
  function clearAcks() { for (const id of ["finish-ack", "window-ack", "reset-off"]) input(id).checked = false; }
  async function end() {
    generation++; token = null; clearAcks();
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
  async function createRecord() {
    // Reuse the explicitly verified assembly identity when one has been configured.
    const scale = await request<components["schemas"]["CurrentScaleState"]>("current-scale");
    const active = scale.records?.find(r => r.id === scale.active_id);
    draft = await request<Draft>("drafts", {
      assembly_id: input("assembly").value.trim() || active?.assembly_id || "Assembly not named",
      observer: input("observer").value.trim() || "Operator name not recorded",
    });
    await listDrafts();
  }
  button("create").onclick = () => void action(createRecord);
  el<HTMLSelectElement>("drafts").onchange = () => void action(async () => {
    const id = el<HTMLSelectElement>("drafts").value;
    draft = id ? await request<Draft>(`drafts/${id}`) : null;
  });
  async function initialize() {
    const epoch = ++generation; acquiring = true;
    let result: Session;
    try {
      if (!draft) await createRecord();
      result = await request<Session>("initialize", { draft_id: draft!.id, joint_id: selected,
        conditions: "Operator-requested neutral initialization and position test" });
    } finally { acquiring = false; }
    if (epoch !== generation || !visible || document.hidden || !document.hasFocus()) {
      await request("stop", { trial_id: result.status.trial_id }); return false;
    }
    token = result.token; sequence = 0; status = result.status; savedMessage = ""; clearAcks();
    el<HTMLDetailsElement>("settings").open = false;
    return true;
  }
  button("reset").onclick = () => void action(async () => {
    status = await request<Status>("reset-current", { power_off_and_supported: true });
    input("reset-off").checked = false; savedMessage = "";
  });
  el<HTMLFormElement>("position-form").onsubmit = event => {
    event.preventDefault();
    if (button("apply").disabled) return;
    const target = input("target").valueAsNumber;
    if (!Number.isFinite(target) || Math.abs(target - (status?.requested_us ?? 1500)) > 10) {
      fail(new Error("Enter a position within 10 μs of the current command.")); return;
    }
    void action(async () => {
      if (!token && !await initialize()) return;
      await command("target", { joint_id: selected, pulse_us: target }); savedMessage = "";
      nextMove = Date.now() + 300; setTimeout(render, 310);
    }, true);
  };
  for (const [id, outcome] of [["settled", "settled_clear"], ["unsafe", "unsafe"]] as const) {
    button(id).onclick = () => void action(async () => {
      await command("feedback", { command_id: status!.pending_command!.command_id,
        joint_id: selected, expected_requested_us: status!.requested_us, outcome,
        observed_direction: "unknown", physical_frame: "Not recorded in simple position test",
        notes: outcome === "settled_clear" ? "Operator marked position OK" : "Operator marked position Not OK" });
      savedMessage = outcome === "settled_clear" ? "OK — saved." : "Not OK — cut servo power.";
      if (outcome !== "settled_clear") token = null;
      await refreshDraft();
    }, true);
  }
  button("window").onclick = () => void action(async () => {
    await command("window", { joint_id: selected, min_us: input("low").valueAsNumber, max_us: input("high").valueAsNumber, reviewed: true }); input("window-ack").checked = false;
  }, true);
  button("stop").onclick = () => { void end(); };
  button("finish").onclick = () => void action(async () => {
    status = await request<Status>("finish", { trial_id: status!.trial_id, power_off_and_supported: true });
    clearAcks(); await refreshDraft();
  });
  button("export").onclick = () => void action(async () => {
    const result = await request<Export>(`drafts/${draft!.id}/export`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `commissioning-${draft!.id}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
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
      const result = await request<Status>("status");
      if (epoch !== generation || token || pending) return;
      status = result;
      if (status.reserved && status.draft_id) {
        const loaded = await request<Draft>(`drafts/${status.draft_id}`);
        if (epoch !== generation || token || pending) return;
        draft = loaded; selected = status.joint_id!;
      }
      render();
    } catch (error) { fail(error); }
    finally { polling = false; }
  }
  void listDrafts().catch(fail); void poll(); setInterval(() => { void poll(); }, 500);
  return {
    end,
    setVisible(value: boolean) { visible = value; if (!value && (token || acquiring)) void end(); else if (value) void poll(); },
    updateWorkbench(value: Workbench | null) { connected = value !== null; if (!connected && token) void end(); render(); },
  };
}
