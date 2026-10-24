# Robotic Workbench

## Start the arm workbench

Install dependencies once (`uv sync --locked --extra vision --extra pwm` and `npm ci`), then run:

```sh
npm start
```

Open **http://127.0.0.1:5173** on the Pi or **http://<pi-address>:5173** on your LAN. The command starts all backend services and the dashboard. On first use, the dashboard opens **Initialization**. Save valid minimum and maximum pulse limits for all six joints; the dashboard then opens **Workbench** on later visits and **Jog** becomes available. Jog still requires manual power preparation and acknowledgment. PCA9685 automatic retrieval remains disabled.

Use `npm run dev:mock` for a simulated stack. Press Ctrl+C or run `npm run stop` to stop. The stop command shuts down this checkout's hardware/mock services and dashboard, including orphaned processes, and force-kills any that do not exit. Logs and commissioning records live under `.runtime/hardware/` for hardware and `.runtime/mock/` for simulation. Existing `.runtime/hardware/workbench-config.json` settings are loaded automatically when present; otherwise the Pi defaults apply.

Startup preserves the installed Python dependencies with `uv run --no-sync`; rerun
the installation command above after dependency changes. If `WORKBENCH_CONFIG` still
points to a deleted `.runtime/hardware/workbench-config.json`, startup reports the
fallback to the mode's defaults. Other missing custom configuration paths remain errors.

`npm run stop` also writes and verifies full-off on every PCA9685 channel after the
processes exit, including when the stack is already stopped. This releases the arm;
it can fall under its own weight. A running mock-only stack never accesses physical
I2C. PWM shutdown failures return an error instructing physical servo-power cutoff.

## About the project

An on-premises assistant for retrieving tools: “Grab me the crescent wrench” becomes
a validated pickup sequence and delivery to a tray. Vision, voice, language interpretation,
coordination, arm control and the dashboard are separate applications in one monorepo.

<img src="Concept.png" alt="Concept illustration of the robotic workbench arm and gripper" width="480">

*Robotic arm concept art.*

The normal launcher uses the **installed Pi hardware**. Simulation is available only
through `npm run dev:mock` or `--mode mock` for development and tests. Real
OpenCV, local speech/LLM and RoArm-M2-S adapters are included but require local models,
reference images and reviewed arm calibration. Physical pickup has not been validated.

## Raspberry Pi hardware direction

The selected build uses a **Raspberry Pi 5, Camera Module 3, Waveshare PCA9685 HAT,
ServoCity power board and DFRobot ROB0036 V2 arm**. The
[Pi implementation plan](PLAN.md) and [hardware guide](docs/hardware.md) describe
the owner's overhead control station, separate servo power and remaining setup.
The [current shopping list](docs/shopping-list.md) records quantities, product links,
accessories, price checks and purchasing decisions still to resolve for this build.

The Pi foundations now provide:

- A Picamera2 capture backend, alongside the existing OpenCV USB-camera backend.
- [Camera setup commands](apps/vision/README.md#camera-setup-without-the-service) for raw
  captures, region overlays, reference collection and validation before starting vision.
- Six-channel pulse validation, an offline pose checker and low-level PCA9685 output.
- [Manual joint controls](docs/manual-controls.md) in the **Jog** tab, with keyboard,
  browser Xbox controller and on-screen jogging. Base is channel 5 through claw on
  channel 0; physical control uses saved commissioning limits.
- Pi hardware defaults at `config/development/pi.json`.
- An [arm-control design and durable offline rehearsal](docs/arm-control.md) covering
  startup, bounded commands, independent simulated completion evidence, stop and recovery.
- [Commissioning notebooks and trial snapshots](docs/commissioning.md) for measured
  assignments, limits, reviewed poses and separate physical/simulated acceptance counts.
- [Pi deployment preparation](docs/pi-deployment.md): reviewable mock-only service
  bundles, configuration checks, read-only health inspection and lifecycle instructions.

**PCA9685 automated retrieval is not enabled.** The arm does not provide measured
joint feedback through this controller; starting pose, motion completion and stop/recovery
must be established before connecting it to retrieval. The existing RoArm serial adapter
remains available for that separate hardware.

Rehearsal results and complete record files cannot qualify hardware. Actual signal
timing, joint limits, paths/payload, completion sensing, physical stop/recovery and Pi
service/device behavior still require the installed bench.

## Pi setup

Requirements: Python 3.11+, uv, Node.js 22.12+, and npm. On this Pi, install camera and PCA9685 dependencies with:

```sh
uv venv --allow-existing --system-site-packages --python /usr/bin/python3 .venv
uv sync --locked --extra vision --extra pwm
npm ci
```

Follow the [Initialization guide](docs/joint-trials.md) before physical motion. The first commissioning move commands all six joints to 1500 μs. Confirm that starting arrangement is safe and keep the claw INA219 monitor connected throughout powered testing. Missing speech and assistant models are reported in Diagnostics; they do not prevent Initialization.

## Applications

| Application | Responsibility | Default port |
| --- | --- | ---: |
| [Coordinator](apps/coordinator/README.md) | Validates requests, owns retrieval workflow and recovery | 8100 |
| [Vision](apps/vision/README.md) | Camera capture, known-tool detection and tray checks | 8101 |
| [Voice](apps/voice/README.md) | Local WAV transcription | 8102 |
| [Assistant](apps/assistant/README.md) | Converts natural language to structured intents | 8103 |
| [Arm controller](apps/arm-controller/README.md) | Hardware status, PCA9685 initialization/jogging and legacy RoArm paths | 8104 |
| [Dashboard](apps/dashboard/README.md) | Camera preview, commands, progress and operator controls | 5173 |

Shared [contracts](packages/contracts/README.md) define the Python API models and generated
OpenAPI/TypeScript types. [Common utilities](packages/python-common/README.md) provide
configuration, structured errors, correlated logs and SQLite state.

See [architecture and interfaces](docs/architecture.md) for application boundaries and failure
behavior. Backends expose interactive API documentation at their `/docs` routes. Run a single
application with its documented uvicorn command, or launch just backends with:

```sh
uv run python scripts/dev.py --backend-only
```

## Configuration and hardware

`config/development/default.json` holds service addresses, camera selection, model settings
and timeouts. `config/workbench/default.json` holds the tool catalog, camera regions and
calibration. All applications load these files at startup. Environment overrides:

| Variable | Purpose |
| --- | --- |
| `WORKBENCH_CONFIG` | Absolute path to runtime settings JSON |
| `WORKBENCH_CALIBRATION` | Absolute path to tool/bench calibration JSON |
| `WORKBENCH_MODE` | `mock` or `hardware` for independently launched applications |
| `WORKBENCH_STATE_DIR` | Durable state/log directory |
| `WORKBENCH_ROOT` | Repository root when launching from an unusual environment |

The launcher selects `hardware` with the Pi configuration unless passed `--mode mock`. Follow the
[hardware and calibration guide](docs/hardware.md) before enabling hardware mode. It covers
the selected Pi hardware, Camera Module 3 setup, pulse calibration, power boundaries and
legacy RoArm instructions. USB webcams remain supported through OpenCV. GoPro integration
and simultaneous multi-camera fusion are deferred; actual camera compatibility and physical
pickup still need bench validation.

## Development and verification

Local verification includes the Python service tests, mock browser workflows, Ruff
lint/format, generated contracts, and the dashboard build. On the
installed Pi, live camera preview, USB microphone capture/playback/cancellation, and
read-only HAT communication were verified through the diagnostics dashboard. See the
[diagnostics guide](docs/diagnostics.md). Physical arm acceptance remains unperformed.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm run contracts
npm run build
```

Optional image-classifier tests use `uv run --extra vision pytest -q`. For the browser
workflow test, install Chromium once with `npx playwright install chromium`, then run
`npm run test:e2e`. It starts the mock stack on dashboard port 5174, or reuses an existing
mock stack there. It resets the simulated scene; do not run it alongside manual mock trials.

With the mock stack running, exercise 30 deliveries across all three tools:

```sh
uv run python scripts/smoke.py
```

Tests cover service interfaces, request idempotence, missing/uncertain tools, stale camera
input, mode mismatches, service loss, motion timeout, stop/recovery, restart protection and
WAV validation. They also cover offline motion evidence, commissioning record integrity,
physical/simulated result separation and Pi bundle/health validation. See the
[acceptance protocol](tests/acceptance/README.md) for physical trials
and the required 27/30 successful deliveries with no wrong-tool deliveries.

Run one process/worker per application on a trusted local computer. Distributed deployment,
authentication, general clutter recognition, direct handoffs and autonomous grasp learning
are outside this first version.

The **Initialization** tab provides [direct joint values](docs/joint-trials.md):
six rows with pulse, minimum, maximum, Move, and Save limits.
[Shared hardware status](docs/hardware-status.md) keeps sensor
health, servo power and latched motion interlocks consistent across all tabs.

## Universal claw-testing rule

**Every physical claw test must monitor the INA219 ammeter at `0x41`**, including
neutral/startup tests involving all six servos, standalone scripts, commissioning,
ad hoc commands, and main-app testing. The claw is channel 0 on the HAT at `0x40`.
Read the sensor before enabling claw output and continuously during movement and
powered holding. Device detection or a single reading does not satisfy this rule.
If readings fail, become stale, or are invalid, stop the test, attempt to disable
PWM, and cut physical servo power; do not automatically retry movement.

Use absolute current: **`abs(current) < 2.5 A`**. With a verified current scale,
readings at or above 2.5 A abort the test and attempt PWM shutdown. Until shunt
resistance is verified, report raw shunt voltage and explicitly state that amperes
and the 2.5 A cutoff remain unverified; never assume a resistance silently.
Polling can miss peaks, and disabling PWM does not disconnect servo power.
Physical claw test paths that lack monitoring must be updated before use.
See [the claw test instructions](apps/arm-controller/README.md#claw-neutral-and-ina219-test).
