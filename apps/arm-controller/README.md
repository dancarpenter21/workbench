# arm-controller

Owns the robot connection and execution journal. `POST /execute` accepts `{operation_id, tool_id}` for a configured path. It permits one operation at a time and never replays a submitted ID. `GET /status`, `POST /stop`, and `POST /recover` expose operator control.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_arm_controller.app:create_app --factory --host 127.0.0.1 --port 8104
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8104/health`; interactive API
documentation: `http://127.0.0.1:8104/docs`.

The legacy `arm.backend: roarm` hardware mode needs the `arm` extra, a USB serial device and reviewed calibration. It checks startup pose, bounded joint targets/speeds, and feedback at each waypoint. It starts with recovery required. Stop requests a software hold; loss of feedback or serial access requires the physical cutoff. No arbitrary motion endpoint is exposed. See the hardware guide before use.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).

## DFRobot / PCA9685 foundations

`arm.backend: pca9685` identifies the selected Pi hardware. In hardware mode, the service
reports `ready: false` and rejects execution/recovery before opening I2C. It cannot reuse
the RoArm driver's measured-position checks. Mock mode still works.

`pca9685.PulsePlan` validates six explicitly calibrated channels and converts requested
microseconds to hardware ticks, keeping quantized pulses inside the supplied limits.
The template `config/workbench/pwm-calibration.example.json` is empty and disabled.
The offline checker never imports a device library or opens the bus:

```sh
uv run --no-sync python -m workbench_arm_controller.pca9685 \
  --calibration path/to/pwm-calibration.json --pose path/to/pose.json
```

The calibration contains `calibrated: true` and six `channels` entries keyed by actual
joint labels. Each entry supplies a unique `channel` (0-15), `min_us`, and `max_us`
within the documented 500-2500 microsecond envelope. The pose supplies one requested
pulse per label. Actual usable limits must be measured; no channel map or safe home
is supplied. The initial frame rate is approximately 50 Hz; clock accuracy and servo
behavior require physical validation. `--reference-clock-hz` supports clock calibration.

`Pca9685Output` is a low-level development primitive, not a retrieval driver or a live
CLI. The optional `pwm` extra supplies SMBus2. Opening it requests all HAT outputs off
before configuring the device; closing requests them off and releases I2C. Pulse loss
may release an arm or load, and a process crash may leave old pulses active.
A partial I2C write latches a fault and invalidates the commanded-pulse cache. There
is no automatic retry, measured feedback, homing, interpolation or watchdog.

The [hardware guide](../../docs/hardware.md) and [plan](../../PLAN.md) describe what
must be established before supervised motor testing and automatic execution.
