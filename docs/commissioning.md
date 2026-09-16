# Calibration notebooks and trial evidence

`workbench_arm_controller.commissioning` prepares and checks records offline. It
never opens I2C, a camera or serial, sends pulses, starts services or retries motion.
It does not change runtime calibration or enable DFRobot retrieval. Use it now to
prepare blank notebooks, and later to preserve actual bench observations.

## 1. Prepare a calibration notebook

From the repository root:

```sh
uv run --no-sync python -m workbench_arm_controller.commissioning init calibration \
  --domain physical --output .runtime/commissioning/calibration-working.json
uv run --no-sync python -m workbench_arm_controller.commissioning check \
  --input .runtime/commissioning/calibration-working.json
```

Choose `physical` for the actual assembly or `simulated` for rehearsal data. An empty
physical notebook records no physical results. Never relabel simulated values as
measurements. The tool makes six blank joint slots; their order assigns no channels
or joint names. Every unknown value remains JSON `null`; no default limits, home pose,
direction, payload or stop behavior is supplied. The `poses` mapping starts empty.

Edit the notebook with an ordinary text editor as evidence becomes available:

| Field | What to record |
| --- | --- |
| `assembly_id` | Identity/revision of this arm, wiring and controller assembly |
| `conditions` | Mounting, harness, supply, support, load and relevant setup constraints |
| `joints[].name` | Actual joint label established on the assembly |
| `joints[].channel` | Observed controller channel assignment, not a guessed order |
| `joints[].min_us`, `max_us` | Measured usable pulse interval for that joint and setup |
| `joints[].increasing_pulse_direction` | Observed direction in a named physical frame |
| `joints[].evidence` | Observer, timezone-qualified observation time, method and reference to saved bench notes/recording |
| `poses` | Named requested pulse sets, review state, conditions and review evidence |

Each evidence object has `observer`, `observed_at`, `method` and `reference`; all start
unset. References are operator-supplied paths or identifiers. The checker does not open
or authenticate referenced recordings. Preserve referenced files with the snapshots.
Record the procedure, instrumentation and uncertainty in the referenced bench notes.

The checker accepts partial calibration notebooks and reports `missing_record_fields`.
It rejects malformed numbers, duplicate known channels/names, invalid intervals and
unknown schema fields. Once all six mappings and intervals are entered, it uses
`PulsePlan` to check representable pulses against the existing documented envelope.
`pulse_data_complete` means only that these inputs are present and pass that software
check; it does not validate the measurements or the assembly's mechanical limits.

To record a pose, add a named entry under `poses` with this structure, replacing the
empty `requested_us` with all six established joint labels and their recorded requested
pulses once known:

```json
{
  "requested_us": {},
  "reviewed": false,
  "conditions": null,
  "evidence": {
    "observer": null,
    "observed_at": null,
    "method": null,
    "reference": null
  }
}
```

Unreviewed poses may be empty or partial, with unknown target values set to `null`.
Every supplied target must name an assigned joint and respect its known limits. A
`reviewed: true` pose requires all six bounded targets, complete review evidence and conditions. Requested
pulses remain commands; they are not measured joint position. A pose review establishes
neither a clearance path from another pose nor motion completion, startup readiness,
payload suitability or stop behavior. Reassess pose reviews when the assembly, limits
or conditions change. There is no automatic conversion to an enabled calibration.

## 2. Freeze the calibration used for a trial

```sh
uv run --no-sync python -m workbench_arm_controller.commissioning snapshot \
  --input .runtime/commissioning/calibration-working.json \
  --output .runtime/commissioning/calibration-001.snapshot.json
```

Snapshots preserve the normalized record, save time and a SHA-256 content digest.
Every generated file uses exclusive creation and is flushed to disk; existing files
are never replaced. Partial notebooks can be preserved, including missing values.
Use a new snapshot filename after changes. Snapshot creation is record keeping, not
approval to move.

The digest detects accidental edits when loading; it is not a signature, an independent
attestation or filesystem write protection. Keep backups of originals. A later trial
embeds a copy of the calibration record, so edits to the working notebook cannot change
what the trial records. The embedded calibration and trial are covered by the trial's
digest. Different observation times or conditions are different calibration versions.

## 3. Prepare and record trials

```sh
uv run --no-sync python -m workbench_arm_controller.commissioning init trial \
  --domain physical --output .runtime/commissioning/trial-001-working.json
```

The generated trial has a unique `trial_id`, `outcome: "not_run"` and unknown result
fields set to `null`. Keep `not_run` until a test occurs. Use `incomplete` for interrupted
tests or unresolved observations; do not convert missing evidence to failure, success
or `wrong_tool_delivery: false`. Use `pass` or `fail` only after recording the result.

Completed trials require timezone-qualified `started_at`/`ended_at`, `scenario`,
`observations` and complete `evidence`. A failed trial also requires `failure_reason`.
Use `scenario: "delivery"` for retrieval acceptance. Other scenario names can record
startup, signal loss, power interruption, stop, recovery or camera checks without being
counted as successful deliveries. Record the actual tested procedure and observed
response; the tool supplies no stop procedure or assumed physical result.

Completed deliveries also require `tool_id`, `operation_id`, `source_observation`,
`tray_observation`, `successful_delivery` and `wrong_tool_delivery`. Passing deliveries
require success and no wrong-tool delivery. The operation ID is the existing application's
durable identity, not a retry request. A supervised component test without an application
operation should use a different scenario and leave the operation ID unset.

Optional fields preserve `spoken_request`, `transcript`, `stop_recovery_notes` and
recognition/inference/motion/total `timings_ms`. Unknown timings stay `null`; durations
are performance observations, never evidence of position or completed motion. Record
independent observations and their limitations in the evidence method and reference.

After editing the trial:

```sh
uv run --no-sync python -m workbench_arm_controller.commissioning check \
  --input .runtime/commissioning/trial-001-working.json
uv run --no-sync python -m workbench_arm_controller.commissioning snapshot \
  --input .runtime/commissioning/trial-001-working.json \
  --calibration .runtime/commissioning/calibration-001.snapshot.json \
  --output .runtime/commissioning/trial-001.snapshot.json
```

A trial requires a calibration snapshot in the same evidence domain. The tool also
preserves not-run and incomplete trials; those snapshots are not completed results.
To correct a record, retain the original, create a new snapshot with the same trial ID,
and explain the correction in `observations`. Choose one revision in a summary and
retain the superseded record in the archive; duplicate trial IDs are rejected.

## 4. Inspect a chosen acceptance cohort

Pass the three actual tool IDs and the explicit trial snapshot paths:

```sh
uv run --no-sync python -m workbench_arm_controller.commissioning summary \
  --tools adjustable_wrench screwdriver pliers \
  .runtime/commissioning/trial-001.snapshot.json
```

This one-trial example cannot meet the target. Add the rest of the cohort's paths as
positional arguments. Summary loading verifies each digest and rejects duplicate trial
or operation IDs and deliveries for unlisted tools. It keeps physical and simulated
counts separate. Only completed delivery trials contribute delivery counts; only passing
deliveries contribute successes. Known wrong-tool results remain counted even for a
failed/incomplete trial or another scenario. Not-run/incomplete trials and other scenarios
are reported separately.

`recorded_delivery_target_met` requires exactly ten completed deliveries per tool, at
least 27 successes, zero recorded wrong-tool deliveries, no unresolved trials in that
domain, and one frozen calibration version for the cohort. It is a calculation over
operator-entered records, not proof the tests occurred, not qualification of other
scenarios and not a reason to discard failed attempts. Record every attempt in order,
define the cohort before testing, and retain failures and superseded records.

Every check/summary reports `hardware_qualified: false` and
`automated_retrieval_enabled: false`. A successful CLI exit means that records are
structurally valid or were saved; incomplete notebooks are intentionally valid.
Malformed records, mixed trial/calibration domains, digest mismatches and existing
output paths produce a nonzero exit. None of these commands controls application
readiness, recovery or durable execution state.

## Remaining physical work

Actual channel assignments, usable joint limits, direction, pose reviews, clearance
paths, reach/load/grip behavior, signal timing, startup and stop/fault/recovery responses
still require bench evidence. The motion/completion design must also be validated before
retrieval integration. The acceptance protocol's 30 deliveries and fault scenarios remain
unperformed. Software tests use generated records only, including synthetic records
marked physical solely to exercise validation boundaries; they are not shipped results.

Keep local notes, photographs, audio and trial files under the ignored `.runtime/`
directory and back them up separately. See [hardware setup](hardware.md),
[the implementation plan](../PLAN.md) and [acceptance protocol](../tests/acceptance/README.md).
