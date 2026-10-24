# Commissioning telemetry and arm-off baselines

The INA219 on I2C bus 1, address `0x41`, measures the claw supply. Its readings
are evidence about that circuit, not position feedback or proof every servo is
unpowered. Collect off-state evidence with all servo power physically disconnected.
No servo commands are needed for this procedure.

## Logs

The running arm service appends raw samples to the `journal` table in its local
`arm.sqlite3` database under `WORKBENCH_STATE_DIR` (normally `.runtime/hardware`).
Samples outside a trial use `claw:service`; commissioning trial samples use
`commissioning:physical:samples:<draft-id>`. Simulated records have a separate
namespace and an explicit `simulated` flag.

New service samples include a monitor-start UUID, increasing sample sequence,
UTC acquisition time, sample age, I2C bus/sensor/PWM addresses, raw bus volts,
signed shunt millivolts, sensor health/fault, scale verification/evidence, and the
active off-bound reference and classification. Sequence numbers are sample
identifiers, not motion ownership tokens. Older records remain readable without
inventing missing source identifiers. Motion intentions/results, trial windows,
marks and observations remain in the commissioning event history/journal.

## Read-only baseline recorder

The recorder uses only `GET /commissioning/status` and `GET /manual/status` from
the running arm service. It never opens I2C, enables outputs, sends PWM, starts
a motion session or changes the sensor configuration. Keep the existing monitor
running throughout a capture, including during any powered holds elsewhere.

After physically disconnecting all servo power:

```sh
uv run --no-sync python -m workbench_arm_controller.off_baseline \
  --capture --arm-off-confirmed --duration 60 --observer "Your name / bench reference"
```

For a longer drift/noise check, use `--duration 1800` (30 minutes). The recorder
accepts 10–3600 seconds; without `--capture` it prints its procedure and does not
contact the service. `--config` selects service addresses/setup; default is the
Pi development configuration. Do not restart the service or run motion trials
while collecting a baseline. Real readings are required unless
`--allow-simulated` is explicitly selected for development.

Each uniquely named directory under `.runtime/commissioning/off-baselines/` has:

- `samples.jsonl`: a start event, timestamped raw readings, and completion or
  rejection event. Samples are flushed continuously so progress can be inspected
  during a long capture without waiting for a final report.
- `report.json`: acquisition setup, declared power state and observer, domain,
  timing, sample count, statistics, raw-log SHA-256 hash, and proposed bounds.
  It is written at completion or controlled failure, with a rejected status and
  no proposed bounds if the capture was unsuccessful.

Repeated cached readings are not counted as new samples. Sensor faults, stale
readings, active manual control, implausible off readings, changes in setup/scale
or monitor session, and insufficient time/sample coverage reject the capture.
Partial evidence is retained. The recorder does not automatically retry a failed
capture. The arm service continues its independent fault/shutdown behavior.

## First physical reference: 2026-10-23

Run `20261023T212809Z-b783d417` collected **542 distinct healthy samples** over a
60-second acquisition. The operator reported the arm off. Setup: bus 1, sensor
`0x41`, PCA9685 `0x40`. Current scale/shunt resistance remain unverified.

| Measurement | Observed range | Mean | Provisional off bound |
| --- | --- | --- | --- |
| Bus voltage | 0–0.008 V | 0.004059 V | 0–0.020 V |
| Signed raw shunt voltage | −0.040 to +0.040 mV | −0.001365 mV | absolute value ≤0.080 mV |
| Current in amperes | Unverified | Unverified | Unverified |

Both voltage and absolute-shunt bounds must match a fresh healthy reading before
the display infers **Powered down**. A zero-current reading on an energized bus
is not classified as off by this measured profile. A stale/failed sensor always
has unknown power state, even if its last reading was inside the off bounds.

The proposed upper bound is the observed maximum plus the largest of three
register counts, three population standard deviations, or the observed span,
rounded upward to a register count. Shunt analysis uses absolute magnitudes;
the signed samples and signed statistics are retained unchanged. TI documents
4 mV bus-register and 10 µV shunt-register resolution in the
[INA219 datasheet, register maps](https://www.ti.com/lit/ds/symlink/ina219.pdf).
These resolutions set a minimum noise margin, not a guaranteed error budget.

The first Pi configuration references this provisional report under
`arm.claw_current.off_baseline`. The physical raw report and samples remain in
ignored runtime storage. The reference is bound to physical/simulated domain,
I2C bus, sensor address and PWM address. Invalid/mismatched references are shown
as unavailable and do not establish an off state. They do not disable current
monitoring. Configuration changes take effect on the next arm-service start;
do not interrupt an ongoing long capture to load them.

These are single-session **display bounds**, not final electrical acceptance or
motion authorization. Review a longer capture, cold starts, wiring changes and
temperature variation before treating them as stable commissioning limits.
Long captures propose another report; they never silently widen the configured
bounds. No offset is subtracted from the raw current signal and the mandatory
`abs(current) >= 2.5 A` cutoff is unchanged when the shunt scale is verified.
Until then, raw shunt measurements remain mandatory and amperes/cutoff remain
explicitly unverified. Power-off preparation still requires operator support and
power-disconnection acknowledgment; the display inference cannot authorize it.

## Unattended observation from the durable journal

For long windows, copying the service journal avoids losing acquisition samples
when a status HTTP response is slow. The passive recorder backfills the specified
UTC window from existing rows, then follows newly appended rows until its end:

```sh
uv run --no-sync python -m workbench_arm_controller.journal_capture \
  --start-at 2026-10-23T21:30:45Z --duration 1800 --arm-off-confirmed \
  --observer "Operator / bench reference"
```

Use the actual start time for your run. SQLite is opened with `mode=ro`; the
recorder cannot alter application state, send motion, recover faults or change
monitoring. It retains acquisition timestamps, raw samples and source journal IDs.
Faults and acquisition gaps are recorded explicitly. A complete but problematic
observation is marked `observed_with_gaps` and provides no proposed off bounds.
The physical service retains responsibility for current monitoring and shutdown.

The 2026-10-23 30-minute request covers **21:30:45–22:00:45 UTC**. The first HTTP
recorder, `20261023T213045Z-56e72682`, rejected its segment after 5,294 distinct
samples because a response made the sample stale. The service journal continued
recording. Observation `20261023T214233Z-journal-97c7c90c` backfills and follows
that same original window from the journal; its report determines whether actual
acquisition gaps occurred. Neither run automatically changes configured bounds.

## Active backend and main Workbench indicators

The selected physical reference is now the completed 30-minute observation
`20261023T214233Z-journal-97c7c90c`: **16,390 valid readings**, no recorded issues,
0–8 mV bus voltage and −0.060 to +0.060 mV raw shunt voltage. Its evidence hash
was checked against the retained raw log before selection. The configured display
bounds are **bus voltage ≤0.020 V and absolute shunt voltage ≤0.120 mV**, inclusive.
This supersedes the first-minute 0.080 mV shunt bound above. Ampere scaling remains
unverified; these bounds do not alter current protection or motion authorization.

The arm service computes `claw_current.power_state` as `off`, `energized`, or
`unknown`. The coordinator includes `claw_current` directly in Workbench status
and live events, even when manual control is uncommissioned. The main Workbench
view shows raw readings and baseline details, and a compact header indicator
remains visible across Workbench, Pulse test, Jog and Diagnostics. Fresh healthy
readings within both bounds show Powered down; readings outside either bound
show Powered. Faults/stale readings never show Powered down. Missing telemetry
shows Power unknown. The browser also expires cached status if live updates stop.

A recorded sensor fault survives service restarts. Restarting to load a new
baseline does not acknowledge recovery, restore motion ownership, or clear that
fault. Explicit acknowledged power-off preparation with healthy readings is
still required before the monitor latch can be cleared.

## Powered neutral reference in control and diagnostics

The all-six neutral hold `20261024T013046Z-a56f8954` completed **1800 seconds**
with **17,192 healthy powered samples** and verified PWM full-off at completion.
The captured claw circuit measured **4.956–4.996 V** (mean 4.987898 V) and signed
shunt voltage **−0.850 to −0.350 mV** (mean −0.760144 mV). Current in amperes and
the 2.5 A cutoff remain unverified. Other servo currents were not measured.

`config/workbench/neutral-reference.json` retains the compact observed statistics,
six 1500 µs requested targets, nominal 1498.16 µs encoded pulse, setup/domain,
completion evidence and SHA-256 hashes of the report and raw samples. Raw logs
remain under ignored `.runtime/commissioning/neutral-holds/`. Import checks
verified the completed report, shutdown, sample count, health and extrema against
the raw capture. No physical position was inferred from those commands.

The Pi configuration selects this reference with `arm.neutral_reference`. The
arm service loads and validates its schema and sensor setup/domain. The new
read-only `GET /commissioning/evidence` endpoint (also forwarded by the coordinator)
exposes the reference and live assessment. Workbench status/events and diagnostic
exports also include `neutral_evidence`.

The assessment distinguishes unavailable evidence, sensor faults, supply off,
neutral targets not established, readings inside the recorded neutral range,
and readings outside that range. Comparison uses bus voltage and **absolute**
shunt magnitude, and runs only when all six currently known commanded targets
are 1500 µs. With no known output preparation, a different pose, or PWM disabled
after the standalone hold, the capture remains historical reference data. It
must not produce a claim that the present arm pose or load is normal.

Workbench displays a compact reference indicator with expandable ranges, sample
count and provenance. Diagnostics adds independent claw-INA219 and neutral-reference
checks and includes the typed reference in downloaded reports. Cached live
comparisons expire with current data. Diagnostic snapshots retain their check
timestamps and are marked stale on connection failure.

Outside-reference readings are **advisory**: they do not establish a stall,
overcurrent, mechanical travel limit or safe operating envelope. Motion gates,
manual calibration/commissioning flags, automatic-retrieval restrictions and the
verified-current cutoff are unchanged. Further supervised tests are needed to
establish limits for other poses, motion rates, loads and individual servo currents.
