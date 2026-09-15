# Robotic Workbench

An on-premises assistant for retrieving tools: “Grab me the crescent wrench” becomes
a validated pickup sequence and delivery to a tray. Vision, voice, language interpretation,
coordination, arm control and the dashboard are separate applications in one monorepo.

The working default is a **simulated bench** with three tools in fixed positions. Real
OpenCV, local speech/LLM and RoArm-M2-S adapters are included but require local models,
reference images and reviewed arm calibration. Physical pickup has not been validated.

## Raspberry Pi hardware direction

The selected build uses a **Raspberry Pi 5, Camera Module 3, Waveshare PCA9685 HAT,
ServoCity power board and DFRobot ROB0036 V2 arm**. The
[Pi implementation plan](PLAN.md) and [hardware guide](docs/hardware.md) describe
the owner's overhead control station, separate servo power and remaining setup.

The Pi foundations now provide:

- A Picamera2 capture backend, alongside the existing OpenCV USB-camera backend.
- [Camera setup commands](apps/vision/README.md#camera-setup-without-the-service) for raw
  captures, region overlays, reference collection and validation before starting vision.
- Six-channel pulse validation, an offline pose checker and low-level PCA9685 output.
- A Pi profile at `config/development/pi.json`, still defaulting to mock mode.

**PCA9685 automated retrieval is not enabled.** The arm does not provide measured
joint feedback through this controller; starting pose, motion completion and stop/recovery
must be established before connecting it to retrieval. The existing RoArm serial adapter
remains available for that separate hardware.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ and npm.

```sh
uv sync --locked
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. All five backend services start automatically in mock mode.
Enter “Grab me the crescent wrench” or choose a tool chip, then select **Retrieve tool**.
After delivery, use **Reset mock scene** to put the simulated tools back and empty the tray.

**Speak** records locally; click again to finish. Review the transcription before submitting.
Mock voice always returns “Grab me the screwdriver” and mock interpretation uses a small
deterministic command parser. No microphone audio is sent to a cloud service.

**Stop arm** blocks further operations until you inspect the bench and acknowledge recovery.
In hardware mode, software stop is only a hold request; maintain an accessible physical cutoff.
Press Ctrl+C in the launcher terminal to stop the applications. Logs and durable operation
state live under `.runtime/mock/` or `.runtime/hardware/`.

If port 5173 is occupied, use `npm run dev -- --dashboard-port 5174`.

## Applications

| Application | Responsibility | Default port |
| --- | --- | ---: |
| [Coordinator](apps/coordinator/README.md) | Validates requests, owns retrieval workflow and recovery | 8100 |
| [Vision](apps/vision/README.md) | Camera capture, known-tool detection and tray checks | 8101 |
| [Voice](apps/voice/README.md) | Local WAV transcription | 8102 |
| [Assistant](apps/assistant/README.md) | Converts natural language to structured intents | 8103 |
| [Arm controller](apps/arm-controller/README.md) | Calibrated paths, serial feedback and motion limits | 8104 |
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

The launcher explicitly selects `mock` unless passed `--mode hardware`. Follow the
[hardware and calibration guide](docs/hardware.md) before enabling hardware mode. It covers
the selected Pi hardware, Camera Module 3 setup, pulse calibration, power boundaries and
legacy RoArm instructions. USB webcams remain supported through OpenCV. GoPro integration
and simultaneous multi-camera fusion are deferred; actual camera compatibility and physical
pickup still need bench validation.

## Development and verification

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
WAV validation. See the [acceptance protocol](tests/acceptance/README.md) for physical trials
and the required 27/30 successful deliveries with no wrong-tool deliveries.

Run one process/worker per application on a trusted local computer. Distributed deployment,
authentication, general clutter recognition, direct handoffs and autonomous grasp learning
are outside this first version.
