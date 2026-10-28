# Workbench LLM harness

The Workbench tab contains a prompt/tool editor, conversation input, execution
trace, and a supervised physical joint-control session. The default ROS gateway
owns orchestration; the arm owner executes the sequence independently of model
latency. The existing automatic retrieval/MoveIt physical gate remains closed.

## Configure and run

1. Start the normal ROS stack. In **Prompts and tool configuration**, set an
   OpenAI-compatible chat-completions base URL (including `/v1` when required)
   and model. The endpoint must support function tools. Enable image support only
   for a model that accepts images in chat messages.
2. For authenticated endpoints, set `WORKBENCH_LLM_API_KEY` in the server's launch
   environment, or specify another `WORKBENCH_LLM_*` environment variable. The UI
   accepts the variable name, never its secret value. Endpoint URLs cannot embed
   credentials. Requests do not follow redirects.
3. Edit the system prompt, descriptions, and enabled tools. Schemas and handlers
   are fixed in code. Save before starting a run. A running conversation retains
   its saved configuration revision.
4. For motion, establish joint limits in Initialization and prepare outputs through
   the existing Jog procedure with servo power disconnected and the arm supported.
   The harness does not prepare, home, reset faults, or verify sensor calibration.
5. Enter established claw open/close targets in microseconds, acknowledge supervision,
   and enable the physical session. If scale is unverified, explicitly acknowledge
   that amperes and the 2.5 A cutoff are unverified. Keep the operator browser open.
6. Submit a request. Review the tool trace and live current display. **Stop arm**
   cancels the run and requests coordinated hold. **End session** revokes the lease.
   Holding retains PWM and continuous current monitoring; it is not power removal.

Local recognition is available through `inspect_scene` without an image model
request. `capture_image` uses the existing camera owner and returns a fresh JPEG
with its acquisition timestamp. Neither tool supplies calibrated XYZ coordinates.
Images are sent to the selected endpoint only on demand and are not saved in
harness traces. Prompts, conversation text, operation records, and configuration
are persisted in local ignored runtime databases.

## Motion contract

`execute_sequence` accepts a unique UUID `operation_id` and 1–10 typed steps:

```json
{
  "operation_id": "9eb0e235-25ba-4492-812e-93c451768c60",
  "steps": [
    {"action": "open_claw"},
    {"action": "move_joint", "joint_id": "base", "target_pulse_us": 1510},
    {"action": "close_claw"}
  ]
}
```

These numbers are illustrative, not physical commissioning values. Read
`get_workbench_state` for current identifiers and saved bounds. Joint direction
means increasing/decreasing pulse width, not a calibrated angle or Cartesian
axis. Claw actions use operator-configured targets; direct `move_joint` control of
`claw` is rejected. Every commanded joint requires both saved limits. No nominal
range fallback is allowed. The owner validates the complete sequence, then moves
one joint at a time at up to the configured rate, capped at 100 µs/s.

Completion means targets were commanded. Servos have no measured position
feedback, pulse bounds do not establish collision avoidance, and `inspect_grip`
returns `unknown` with current evidence rather than claiming force or secure grip.
XYZ motion, simultaneous trajectories, automatic grasp inference, and physical
MoveIt integration are outside this implementation.

The owner enforces one session, a three-second operator-heartbeat deadline,
five-minute session lifetime, 20 sequence submissions, and 60 seconds per sequence.
It rejects sequences whose estimated movement cannot fit the remaining budget.
The model loop separately limits each run to five minutes and 20 tool calls. It
waits for sequence completion without repeated inference calls. External MCP
clients poll `get_operation` themselves. Reusing an operation UUID never replays
motion; a different payload with an existing UUID is rejected. Restarted operations
are interrupted and require inspection/recovery.

INA219 monitoring at `0x41` precedes claw output and continues during all movement
and powered holding. Verified scale uses `abs(current)` and aborts at ≥2.5 A.
Missing, stale, invalid, failed, or excessive readings latch a fault, cancel
execution, attempt PWM shutdown, and instruct the operator to cut physical servo
power. There is no automatic motion retry. Software polling/shutdown is not a
physical current limiter.

## MCP and API

Connect a local MCP client using Streamable HTTP at
`http://127.0.0.1:8100/mcp` (adjust for the configured gateway port). For a client
on another computer, use an SSH localhost tunnel. The endpoint rejects nonlocal
Host/Origin/peer addresses; do not publish it directly to the network.

The stateless JSON-response implementation supports protocol versions 2025-03-26,
2025-06-18, and 2025-11-25: initialization, ping, tools discovery/calls, and prompt
discovery/get. GET/SSE and DELETE return 405. The `workbench` prompt contains the
saved system prompt; external clients are responsible for using it and refreshing
discovery after configuration changes. There are no subscription notifications.
MCP clients cannot acquire or heartbeat the operator session through a tool.
External calls/results appear under **External MCP activity** in Workbench.

The browser uses `/harness/config`, `/harness/tools`, `/harness/arm`,
`/harness/session`, `/harness/heartbeat`, `/harness/end`, `/harness/runs`, run lookup,
cancellation, and WebSocket trace endpoints. Typed generated ROS controls carry
session and sequence requests to the arm owner. The retained `--transport http`
stack supports configuration editing but reports harness motion/camera tools as
requiring the ROS gateway.

## Validation and physical acceptance

Automated tests use fake hardware and explicit mock mode. They cover command
ordering, validation before output, duplicate IDs, ownership, expiry, interruption,
sensor faults/shutdown, configuration revisions, MCP boundaries, and browser flows.
These checks do not establish physical acceptance.

With an operator at the power cutoff, first verify live raw current and the scale
label, then exercise one small established joint movement, claw open/close, and a
short sequence. Verify Stop and browser-heartbeat loss retain monitored holding.
Use controlled sensor-fault injection only under the commissioning procedure;
confirm attempted shutdown and required operator recovery. Record actual results
and setup conditions before expanding movements or loads.
