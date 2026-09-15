# Robotic Workbench — Raspberry Pi implementation plan

## Objective and current state

Build a budget-conscious, camera-guided workstation around the owner's selected
Raspberry Pi 5, Camera Module 3, Waveshare PCA9685 Servo Driver HAT, and DFRobot
ROB0036 V2 arm. Favor ready-made connections and minimal soldering. The
[hardware baseline](docs/hardware.md#selected-hardware-dfrobot-rob0036-v2) records
component choices, the overhead-to-table layout, and remaining physical checks.

The repository already provides six applications, shared API contracts, durable
operation tracking, mock devices, and local hardware/model adapters. Recorded
simulated deliveries exercise the application workflow; they do not establish
physical performance. Vision currently checks fixed regions before and after a
recorded sequence. Continuous visual guidance and physical calibration remain future work.

The previous arm adapter speaks RoArm-M2-S USB serial and reads four measured joint
positions. The selected arm has six PWM servos, and the PCA9685 supplies no measured
position feedback. A pulse command or elapsed delay cannot satisfy the existing
movement-completion or stop contract. Keep the RoArm adapter as an explicit legacy
option and keep DFRobot automated retrieval unavailable until the physical behavior
has been selected, implemented and bench-validated.

## Camera and pulse foundations

Software foundations are now present on this branch: selectable camera capture,
standalone camera setup and reference collection, six-channel pulse validation and
an offline checker, a Pi runtime profile, and explicit refusal of uncommissioned
PCA9685 retrieval. Device behavior and physical performance remain unverified.
The completed software and remaining bench checks are separated below.

1. **Hardware direction recorded.** The plan uses local Pi I2C control and a short
   CSI camera connection. Keep unconfirmed Pi RAM,
   display interface, camera lens variant, physical fits, harness contacts and protection
   choices visible. The owner's selections are a working design, not a tested assembly.
2. **Camera backends implemented.** Picamera2 and OpenCV capture remain behind vision's
   existing boundary, with optional device libraries loaded only for the selected backend.
   Fake-camera tests cover selection, frame color/size, failure and cleanup. The camera-only
   smoke check on the Pi still requires the installed camera and disconnected motor power.
3. **Six-channel validation and pulse dry runs implemented.** The checker validates named
   channel mappings, usable pulse intervals, complete poses and quantized outputs. It rejects
   duplicates, nonfinite values and out-of-range requests. Actual mappings, limits, direction
   and joint-angle relationships remain unmeasured. No usable home pose is supplied.
4. **Software motion states defined; physical semantics unresolved.** Decide between a deliberately
   constrained open-loop design with independently validated visual checks and adding
   measured feedback. Establish startup pose, bounded movement, completion evidence,
   stop/hold or power-disable behavior, and recovery from lost control. Last-commanded
   position must remain distinct from observed position. A frozen Python process can
   leave the PCA9685 producing its previous outputs; define the physical response.

This phase does not need wireless links, ROS, new registries, inverse kinematics or
changes to public retrieval contracts. Keep the LLM limited to high-level requests.

## Pre-hardware development completed

- [Motion design and offline rehearsal](docs/arm-control.md) now define startup/readiness,
  explicit directed transitions, bounded pulse changes, fresh simulated checkpoint evidence,
  deadline faults, unconfirmed stop and inspected recovery. The separate rehearsal model
  commits intent before returning simulated commands, retains terminal evidence and consumed
  IDs across restart, and never completes from pulses or elapsed time. It performs no device
  output and does not replace the existing mock retrieval workflow or public contracts.
- [Commissioning tools](docs/commissioning.md) prepare six unset joint records, evidence-backed
  pose reviews, write-once calibration/trial snapshots and acceptance summaries that separate
  physical results from simulation. File completeness cannot enable or qualify hardware.
- [Pi deployment tooling](docs/pi-deployment.md) generates and checks a mock-only systemd
  bundle with explicit configuration/state paths and manual startup. Read-only health checks
  distinguish service readiness, workflow state and recovery; all physical readiness remains
  unknown. Actual Pi/systemd lifecycle validation remains outstanding.

The software state design is testable now. The choice between independently observed
open-loop checkpoints and added measured feedback, all physical thresholds, startup/output
behavior, path clearance, payload, and the stop/recovery procedure remain bench decisions.
Automated PCA9685 retrieval stays disabled regardless of record or rehearsal results.

### Latest local verification — 2026-09-15

| Check | Recorded result |
| --- | --- |
| Complete Python suite | 181 passed, including the existing workflow and hardware-refusal tests |
| Offline rehearsal tests | 26 passed: state/evidence gates, bounds, durable intent, terminal results and no replay |
| Commissioning record tests | 28 passed: unset values, validation, snapshots and separate acceptance counts |
| Pi preparation tests | 26 passed: mock-only bundles, configuration drift and read-only health/failure reporting |
| Ruff lint and formatting | Passed for all 52 Python files |
| Diff whitespace and local documentation links | Passed |

The three focused suites are included in the 181 total. These results came from the
existing Windows development environment, with synthetic fixtures and fake devices.
The final suite invocation was:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .runtime/pytest-prehardware-final -p no:cacheprovider
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
```

The installed virtual environment and a workspace temporary directory avoided this
session's restrictions on the default uv cache and Windows temporary directory.
Frontend builds, contract generation and browser end-to-end tests were not rerun for
these additions; frontend and public contracts were unchanged. No live device,
Pi/systemd lifecycle or physical acceptance validation occurred.

## Subsequent bench milestones

### 1. Bring up the camera and electronics

The [camera setup tools](apps/vision/README.md#camera-setup-without-the-service) are
ready for assembly: capture raw frames without existing references, inspect region
overlays, adjust boxes, collect the complete reference matrix and check its files.
Synthetic-image and fake-camera tests cover this workflow. Live camera operation
and recognition remain unverified until the Pi and Camera Module 3 are assembled.

Confirm Pi cooling/HAT/display fit, camera cable orientation, and the selected OS and
Python environment. Inspect live Camera Module 3 frames and stable focus at the bench
height before collecting references. Measure model memory use and latency on the actual
Pi; its RAM and local voice/assistant performance are not established. Provide a
microphone if voice input remains part of the workstation.

Review the table's supply, distribution-board rating, protection, physical cutoff and
long signal harness before servo tests. Confirm contacts and polarity, a shared signal
ground, independent channels, and absence of a positive connection from PDB to HAT.
Detailed electrical limitations are in the [hardware guide](docs/hardware.md).

### 2. Validate one supported movement at a time

After selecting the physical completion and stop approach, establish the six actual channel-to-joint mappings
and safe limits with the arm supported, unloaded and supervised. Verify pulse timing
and signal quality over the intended 4–6-foot harness under the real electrical load.
Characterize startup, target changes, process/controller failure, physical cutoff and
recovery. Record the arm's actual behavior when PWM or servo power disappears.

Choose three lightweight tools only after measuring reach, payload at that reach and
grip suitability. Record known starting poses and clearance paths. A servo stall-torque
number is not a tool payload rating. Keep this physical evidence separate from mocks.

### 3. Integrate a constrained retrieval

Only expose DFRobot execution after the arm implementation can report honest readiness,
completion, stop and recovery states. Update contracts and generated types together if
the chosen design requires different evidence from the RoArm implementation. Retain one
active retrieval, durable operation identities and no automatic motion retries.

First validate fixed pickup, lift, tray delivery, release and return paths. Then calibrate
image coordinates to a named bench frame and evaluate target localization against
physical measurements. Add small, bounded visual corrections only after this baseline
works; stop the approach when observations become stale, uncertain or occluded. Image
classification alone cannot demonstrate a safe joint trajectory.

### 4. Evaluate the physical workflow

Measure successful and wrong-tool deliveries, localization error, frame age, movement
and end-to-end latency, plus failures and required recovery. Retain the existing target
of at least 27 successful deliveries in 30 supervised trials, ten per qualified tool,
with zero wrong-tool deliveries. This target is not an achieved physical result.

Exercise absent/swapped tools, an occupied tray, camera movement or disconnection, stale
images, interrupted control, service restart and failure to reach a target. Set limits
from measured localization and stopping behavior. Do not turn development update-rate
estimates or a timeout into evidence that physical motion completed.

## Scope and deployment

Start with one arm, one fixed overhead camera, constrained tool holders, a small known
tool catalog and delivery into a tray. Direct handoffs, cluttered picking, simultaneous
arms and wireless control are deferred. Recalculate the selected build's cost; the old
$500 RoArm allowance is historical, not a quote for this Pi assembly.

Preserve the six application boundaries. Use one worker per service on a trusted local
host, with loopback HTTP APIs and explicit device ownership. A future multi-host setup
requires a separate security and coordination decision. The Pi owns camera and I2C
access; no dashboard, assistant or voice component should drive a servo directly.
