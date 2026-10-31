import type { components } from "@workbench/contracts";

type Config = components["schemas"]["HarnessConfig"];
type Run = components["schemas"]["HarnessRun"];
type Arm = components["schemas"]["HarnessArmStatus"];
type Tool = components["schemas"]["HarnessTool"];

export function mountHarness(root: HTMLElement, stopArm: () => void) {
  root.innerHTML = `
    <div class="panel-heading"><h2>LLM harness</h2><span class="badge">Supervised joint control</span></div>
    <p>One joint at a time. Positions are commanded, not measured. Pulse limits do not prevent collisions.</p>
    <details><summary>Prompts and tool configuration</summary>
      <form id="harness-config" class="harness-form">
        <label>Model endpoint<input id="harness-endpoint" type="url" required /></label>
        <label>Model<input id="harness-model" /></label>
        <label>Server credential environment variable<input id="harness-credential" pattern="WORKBENCH_LLM_[A-Z0-9_]+" /></label>
        <label class="ack"><input id="harness-images" type="checkbox" /> Model supports images (sent only on request)</label>
        <label>System prompt<textarea id="harness-prompt" rows="8"></textarea></label>
        <div id="harness-tools"></div>
        <button type="submit">Save configuration</button>
        <p>Saved edits apply to new runs. Credentials stay on the server.</p>
      </form>
    </details>
    <details><summary>Physical session</summary>
      <p>Prepare outputs in Manual first. Save joint limits in Config. Establish claw targets before enabling.</p>
      <label>Claw open target (µs)<input id="harness-open" type="number" min="500" max="2500" step="any" /></label>
      <label>Claw close target (µs)<input id="harness-close" type="number" min="500" max="2500" step="any" /></label>
      <label class="ack"><input id="harness-supervised" type="checkbox" /> I am supervising the arm and can cut servo power.</label>
      <label class="ack"><input id="harness-unverified" type="checkbox" /> If current scale is unverified, I acknowledge amperes and the 2.5 A cutoff are unverified. Raw readings remain required.</label>
      <div class="actions"><button id="harness-enable" type="button">Enable physical harness</button>
      <button id="harness-end" type="button" class="secondary" disabled>End session</button></div>
    </details>
    <p id="harness-arm" role="status">Checking arm…</p>
    <pre id="harness-joints" class="harness-output"></pre>
    <form id="harness-request" class="harness-form">
      <label>Ask the harness<textarea id="harness-text" rows="3" maxlength="8000" required placeholder="Inspect the scene and describe the available joint movements"></textarea></label>
      <div class="actions"><button id="harness-run" type="submit">Run harness</button>
      <button id="harness-stop" type="button" class="danger">Stop arm</button></div>
    </form>
    <p id="harness-message" role="status"></p>
    <pre id="harness-trace" class="harness-output" aria-label="Harness execution trace"></pre>
    <details><summary>External MCP activity</summary><pre id="harness-activity" class="harness-output"></pre></details>
    <p class="caption">Local MCP: http://127.0.0.1:8100/mcp · External clients use this same supervised session.</p>`;
  const settings = document.getElementById("harness-config")!.closest("details")!;
  settings.open = true;
  document.getElementById("llm-config-panel")!.append(settings);
  const targets = document.createElement("section");
  targets.innerHTML = "<h2>Claw targets</h2>";
  for (const id of ["harness-open", "harness-close"]) targets.append(document.getElementById(id)!.closest("label")!);
  document.getElementById("llm-config-panel")!.prepend(targets);
  const el = <T extends HTMLElement = HTMLElement>(id: string) => document.querySelector<T>(`#${id}`)!;
  const input = (id: string) => el<HTMLInputElement>(id);
  const configMessage = document.createElement("p");
  configMessage.setAttribute("role", "status");
  settings.append(configMessage);
  let config: Config | null = null;
  let lease: { token: string } | null = null;
  let running = false;
  let stream: WebSocket | null = null;
  let refreshing = false;
  let heartbeatBusy = false;
  const message = (value: unknown) => {
    const text = value instanceof Error ? value.message : String(value);
    el("harness-message").textContent = text;
    configMessage.textContent = text;
  };
  async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const response = await fetch(`/api/coordinator/harness${path}`, { method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message ?? "Harness request failed");
    return value as T;
  }
  function renderArm(arm: Arm) {
    el("harness-arm").textContent = `${arm.simulated ? "SIMULATED" : "PHYSICAL"} · ${arm.message}${arm.active ? ` · ${Math.ceil(arm.remaining_seconds ?? 0)}s remaining` : ""}`;
    el("harness-joints").textContent = (arm.joints ?? []).map(j => `${j.id}: ${j.commanded_us ?? "unknown"} µs · limits ${j.min_us}–${j.max_us} · ${j.direction}`).join("\n") || "No joints with both saved limits.";
    if (!arm.active) lease = null;
    el<HTMLButtonElement>("harness-enable").disabled = arm.active || !arm.prepared || arm.recovery_required;
    el<HTMLButtonElement>("harness-end").disabled = !lease;
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      renderArm(await request<Arm>("/arm"));
      const events = await request<NonNullable<Run["events"]>>("/activity");
      el("harness-activity").textContent = events.slice(-20).map(e => `${e.kind}\n${JSON.stringify(e.data, null, 2)}`).join("\n\n");
    }
    catch (error) { el("harness-arm").textContent = String(error); }
    finally { refreshing = false; }
  }
  async function load() {
    try {
      config = await request<Config>("/config");
      input("harness-endpoint").value = config.endpoint;
      input("harness-model").value = config.model;
      input("harness-credential").value = config.credential_env;
      input("harness-images").checked = config.supports_images;
      el<HTMLTextAreaElement>("harness-prompt").value = config.system_prompt;
      const tools = await request<Tool[]>("/tools");
      el("harness-tools").replaceChildren(...(config.tools ?? []).map(tool => {
        const section = document.createElement("section");
        const label = document.createElement("label");
        const enabled = document.createElement("input");
        enabled.type = "checkbox"; enabled.checked = tool.enabled; enabled.dataset.tool = tool.name;
        label.append(enabled, ` ${tool.name}`);
        const description = document.createElement("textarea");
        description.value = tool.description; description.dataset.description = tool.name;
        description.setAttribute("aria-label", `${tool.name} description`);
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = "Argument schema (read-only)";
        const schema = document.createElement("pre"); schema.className = "harness-output";
        schema.textContent = JSON.stringify(tools.find(t => t.name === tool.name)?.input_schema, null, 2);
        details.append(summary, schema); section.append(label, description, details); return section;
      }));
    } catch (error) { message(error); }
  }
  el("harness-config").onsubmit = async event => {
    event.preventDefault(); if (!config) return;
    try {
      config = await request<Config>("/config", "PUT", { ...config,
        endpoint: input("harness-endpoint").value, model: input("harness-model").value,
        credential_env: input("harness-credential").value, supports_images: input("harness-images").checked,
        system_prompt: el<HTMLTextAreaElement>("harness-prompt").value,
        tools: (config.tools ?? []).map(t => ({ ...t,
          enabled: document.querySelector<HTMLInputElement>(`[data-tool="${t.name}"]`)!.checked,
          description: document.querySelector<HTMLTextAreaElement>(`[data-description="${t.name}"]`)!.value,
        })),
      });
      message(`Saved revision ${config.revision}`);
    } catch (error) { message(error); }
  };
  el("harness-enable").onclick = async () => {
    if (!input("harness-supervised").checked) { message("Confirm operator supervision first."); return; }
    if (!input("harness-open").value || !input("harness-close").value) { message("Enter established claw targets first."); return; }
    try {
      lease = await request<{ token: string }>("/session", "POST", {
        supervised: true, acknowledge_unverified_scale: input("harness-unverified").checked,
        claw_open_us: Number(input("harness-open").value), claw_close_us: Number(input("harness-close").value),
      });
      await refresh();
    } catch (error) { message(error); }
  };
  el("harness-end").onclick = async () => {
    if (!lease) return;
    try { await request("/end", "POST", lease); lease = null; await refresh(); }
    catch (error) { message(error); }
  };
  el("harness-stop").onclick = () => { lease = null; stopArm(); };
  el("harness-request").onsubmit = async event => {
    event.preventDefault(); if (running) return;
    running = true; el<HTMLButtonElement>("harness-run").disabled = true;
    el("harness-trace").textContent = "";
    try {
      const run = await request<Run>("/runs", "POST", { text: el<HTMLTextAreaElement>("harness-text").value });
      const url = new URL(`/api/coordinator/harness/runs/${run.id}/events`, location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      stream?.close(); stream = new WebSocket(url);
      stream.onmessage = event => {
        const value = JSON.parse(event.data) as { state: string; events: Run["events"] };
        for (const entry of value.events ?? []) el("harness-trace").textContent += `${entry.kind}\n${JSON.stringify(entry.data, null, 2)}\n\n`;
        message(`Run ${value.state}`);
        if (value.state !== "running") { running = false; el<HTMLButtonElement>("harness-run").disabled = false; }
      };
      stream.onclose = () => {
        if (running) message("Trace disconnected. Inspect run status before starting again; operator session still expires if heartbeat is lost.");
        running = false; el<HTMLButtonElement>("harness-run").disabled = false;
      };
    } catch (error) { message(error); running = false; el<HTMLButtonElement>("harness-run").disabled = false; }
  };
  setInterval(() => { void refresh(); }, 1500);
  setInterval(async () => {
    if (!lease || heartbeatBusy) return;
    heartbeatBusy = true;
    try { renderArm(await request<Arm>("/heartbeat", "POST", lease)); }
    catch (error) { lease = null; message(error); }
    finally { heartbeatBusy = false; }
  }, 700);
  void load(); void refresh();
}
