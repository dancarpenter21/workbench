# Manual pulse testing and joint commissioning

Open the **Pulse test** tab. This uses the arm
controller directly through the coordinator, without assistant or vision motion
planning. Mock mode remains the default; simulated observations and current
readings are explicitly labelled and are not physical acceptance evidence.

The wiring is base → channel **5**, joints 2–5 → **4–1**, claw → **0**.
Values shown are requested pulses and nominal quantized encodings, not measured
positions. Automatic PCA9685 retrieval remains disabled.

## Prepare and test

1. Create or resume a record with an assembly label and observer. Select either
   **Isolated single servo** or **All servos holding · move one at a time**.
   A new preparation clears current marks for the selected joint (isolated) or
   all six joints (all holding); earlier observations remain in event history.
2. Disconnect **all servo power**, secure the base, support every link and clear
   the path. For isolated trials, physically isolate the other five servos from
   power. Acknowledge those conditions and prepare outputs. Preparation disables
   all PCA9685 outputs before setting approximately 50 Hz PWM. It programs the
   selected servo, or all six servos, at the operator-observed **1500 µs** starting
   reference. This reference is not a validated path from an arbitrary pose.
3. Apply external power to the trial servos while maintaining support. Initial
   movement can be large; discrete steps do not slow this movement. Confirm that
   the assembly has settled with clearance before proceeding.
4. In all-holding mode, select any of the six servos. The other five retain their
   targets. Enter a pulse width and click **Apply target**, or use ±10 µs buttons.
   Each explicit command may change the request by at most **10 µs**, with at
   least **250 ms** between commands. Larger requests are rejected, never queued.
5. Each joint starts with a **1450–1550 µs** trial window. In all-holding mode,
   review clearance and explicitly save a wider or narrower window when needed.
   Windows must lie within **500–2500 µs**, contain both the current request and
   encoded target, and contain a representable PWM tick. These are trial settings,
   not measured travel limits. Isolated trials keep their fixed narrow window.
6. Observe each step directly. Mark initial/minimum/maximum only after a fresh
   settled-and-clear acknowledgment. Save observed direction and notes. Minimum
   must be below maximum and contain initial; partial observations are allowed.
7. Stop the trial, support the assembly, disconnect servo power, and confirm
   **Disable outputs & finish trial**. Complete reviewed observations become
   `observed`; incomplete or faulted observations require review. Exports remain
   disabled drafts until combined poses and rates are separately commissioned.

Physical trials require `mode: "hardware"`, the workbench profile,
`arm.backend: "pca9685"`, and `arm.commissioning_enabled: true` in the selected
`WORKBENCH_CONFIG`. Shipped configurations leave commissioning disabled. Normal
manual jogging has its separate commissioned configuration gate.

## Mandatory claw monitoring

The hardware arm service reads the claw INA219 at **0x41**, on the configured
`arm.i2c_bus`, from startup until output shutdown. Monitoring runs without a
browser owner and continues during stationary holds, stopped trials, and session
expiry. The nominal sampling interval is **100 ms**; a reading at least **300 ms**
old is stale. Preparation requires a successful reading and claw bus voltage at
or below **0.5 V**, in addition to the operator's all-power-off acknowledgment.

The compact dashboard shows raw shunt voltage and bus voltage alongside a status
light. Physical **Powered down** inference uses the recorded off-voltage and
absolute-shunt bounds for this sensor setup, rather than exact zero. It does not
measure the power switch or alter backend interlocks. Missing, stale and failed
readings always show a fault. Open **Current details** for sample age, scale/cutoff
information and the off-baseline reference. See
[commissioning telemetry](commissioning-logging.md) for read-only acquisition,
raw logs, the initial measured bounds and longer drift captures.

By default,
no shunt resistance is assumed: amperes are unavailable and the **2.5 A cutoff is
UNVERIFIED**. Supervised raw-voltage testing does not establish current protection.
After physically verifying the scale, configure:

```json
"claw_current": {
  "scale_verified": true,
  "shunt_ohms": 0.1,
  "verification_evidence": "Reference to your actual measurement"
}
```

Place this inside `arm`. The number above illustrates the configuration syntax;
it is **not** a verified value for this bench. Use the actual verified resistance.
With a verified scale, either current direction trips at **abs(current) ≥ 2.5 A**.
Missing readings, conversion faults, overflow, stale samples and logging failures
also abort testing. The controller invalidates ownership, attempts PWM full-off,
and instructs the operator to **cut physical servo power**. Outputs may release
loads: keep links supported. No motion retries or automatic recovery occur.
A fresh power-off preparation with healthy readings is required after faults.

Polling and PWM shutdown are not a physical current limiter. Software cannot
monitor or shut down PWM after a host crash, process kill, or a blocked I2C call;
use the physical cutoff. Stop other HAT users before starting the arm service.
The shared device lock prevents cooperating programs from owning motion together;
it cannot protect against unrelated I2C writers.

## Stops, records and exports

One session exclusively owns motion. Browser heartbeats run every 100 ms; the
300 ms lease expiry, tab changes, blur, disconnect and Stop end further target
changes. Ordinary Stop retains targets while monitoring continues. Finishing
requires supported links and disconnected power. Orderly service shutdown attempts
PWM full-off before stopping the monitor. Restart never restores motion ownership
or old targets; interrupted trials require power-off finish and fresh preparation.

Local SQLite state under `.runtime/` (or `WORKBENCH_STATE_DIR`) stores records,
command intentions/results, window edits, marks, faults and timestamped raw current
samples. Telemetry is appended to a journal rather than rewriting the observation
history for each sample. Samples outside trials use a service stream. Logs are
retained locally without automatic pruning and stay out of Git.

**Export record & disabled configuration** downloads JSON containing `record`
(observations and history), `telemetry` (trial-linked current samples), and
`manual_config` (`enabled: false`, `commissioned: false`, rates unset). Tokens
are never persisted. Previous records remain readable. Exported limits do not
qualify physical motion, coupled-joint clearance or automatic retrieval.

New coordinator and arm-controller endpoints under `/commissioning`:

- `POST /select`: token, increasing sequence, `joint_id`; changes selection only.
- `POST /target`: token, sequence, selected `joint_id`, `pulse_us`; one small step.
- `POST /window`: token, sequence, selected `joint_id`, `min_us`, `max_us`,
  `reviewed: true`; records a trial-window change.

Acquisition accepts `trial_type: "isolated" | "all_holding"` (default `isolated`
for existing clients). All-holding preparation accepts `power_off_and_supported:
true`; isolated trials retain `power_off_supported_isolated: true`.

See [physical acceptance](../tests/acceptance/joint-trials.md). Automated tests
use simulated or injected hardware only and do not establish physical acceptance.
