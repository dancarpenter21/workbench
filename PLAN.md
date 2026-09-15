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
option and keep DFRobot automated retrieval unavailable until those behaviors have
been designed and validated.

## First implementation: useful without moving the arm

Software foundations are now present on this branch: selectable camera capture,
standalone camera setup and reference collection, six-channel pulse validation and
an offline checker, a Pi runtime profile, and explicit refusal of uncommissioned
PCA9685 retrieval. Device behavior and physical
performance remain unverified. The steps below separate these foundations from
the remaining bench work.

1. **Record the actual hardware direction.** Replace the USB/Wi-Fi-first roadmap with
   local Pi I2C control and a short CSI camera connection. Keep unconfirmed Pi RAM,
   display interface, camera lens variant, physical fits, harness contacts and protection
   choices visible. The owner's selections are a working design, not a tested assembly.
2. **Capture from Camera Module 3.** Add a Picamera2 backend while retaining the OpenCV
   backend for USB cameras. Keep capture behind the vision application's existing
   boundary, with the same image processing and preview behavior. Import optional
   device libraries only for the selected backend. Verify selection, frame color/size,
   capture failure, and cleanup with fakes; then run a camera-only smoke check on the Pi
   with motor power disconnected.
3. **Prepare six-channel calibration and pulse dry runs.** Represent each named joint's
   assigned channel and measured usable pulse interval. Keep direction and joint-angle
   mapping as follow-up calibration work once those physical relationships are known.
   Reject duplicate channels, incomplete mappings, nonfinite values and out-of-range
   requests. Start with software validation and inspectable pulse output. Do not ship
   invented joint limits or a factory-midpoint home pose as a usable calibration.
4. **Resolve motion semantics before retrieval integration.** Decide between a deliberately
   constrained open-loop design with independently validated visual checks and adding
   measured feedback. Establish startup pose, bounded movement, completion evidence,
   stop/hold or power-disable behavior, and recovery from lost control. Last-commanded
   position must remain distinct from observed position. A frozen Python process can
   leave the PCA9685 producing its previous outputs; define the physical response.

This phase does not need wireless links, ROS, new registries, inverse kinematics or
changes to public retrieval contracts. Keep the LLM limited to high-level requests.

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

After the motion design is settled, establish the six actual channel-to-joint mappings
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
