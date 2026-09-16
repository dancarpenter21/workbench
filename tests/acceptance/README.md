# Acceptance protocol

Automated simulated acceptance: start mock applications, then run
`uv run python scripts/smoke.py`. This checks 30 deliveries, identity and duplicate IDs.
It is not a physical acceptance result.

Use the offline [commissioning notebook and trial tools](../../docs/commissioning.md)
to prepare unknown values now, freeze calibration snapshots and record each later
physical attempt. The tools keep simulated and physical evidence separate, retain
incomplete outcomes, and never enable hardware retrieval. A recorded pose review or
successful delivery count does not establish safe transitions or stop behavior.

For physical acceptance, log 10 trials for each of the three tools. Record tool, spoken
request, transcript, operation ID, start/end time, source/tray observations, successful
delivery, wrong-tool delivery, stop/recovery event, and failure explanation. Require at
least 27 successes out of 30, and zero wrong-tool deliveries. Keep raw results alongside
local audio and camera recordings, excluding private recordings from git.

Also test missing/swapped/displaced tools, uncertain speech, an unknown tool, two tools in
one request, stale/disconnected cameras, an occupied tray, serial disconnection, stop during
movement, power interruption, service restart during movement and explicit recovery.
Disconnect internet after model setup and repeat a complete request. Measure recognition,
inference, motion and total latency separately. Confirm webcam selection with a second
USB camera.

No recorded real camera/audio fixtures or hardware trial results are included yet. The
integration suite generates a synthetic silent WAV to check transport/format handling;
mock voice returns a fixed phrase. Collect real recordings during calibration to evaluate
the actual models and vision thresholds.

## Recording and evaluating a physical cohort

Before testing, identify the three qualified tool IDs and the frozen calibration/setup
revision. Record every attempted delivery in order with `scenario: "delivery"`, including
failures and interrupted attempts. Preserve raw observations and references. Keep unknown
results unset and mark interrupted or unresolved trials `incomplete`. Commanded pulses
and time spent waiting cannot establish measured position or motion completion.

The commissioning summary checks one explicitly selected cohort. It requires ten
completed deliveries for each selected tool, at least 27 passing successful deliveries,
zero recorded wrong-tool deliveries, no unresolved records in that evidence domain,
and one frozen calibration version. It rejects duplicate trial/operation IDs and reports
physical and simulated counts separately. Do not omit failed attempts or substitute
simulation results to reach the target. The result evaluates entered delivery records;
it never qualifies the hardware or enables automation.

Record component and failure tests under distinct scenario names, with the exact
procedure, conditions, observed response, uncertainty and stop/recovery notes. For the
PCA9685 assembly, include host/process loss, controller/I2C loss, PWM loss, servo-power
interruption and restart; the physical responses are not established. Such tests require
a reviewed bench procedure and are not automatic CLI actions. Also validate Camera
Module 3 capture, focus, references and scene changes on the installed Pi. The existing
second-USB-camera compatibility check applies only if USB camera support is evaluated.

All physical acceptance and Pi/device validation remain pending. No generated notebook,
simulation, pulse check or software test result is a physical acceptance result.
