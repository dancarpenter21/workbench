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

## Waveshare HAT diagnostic (no servo motion)

From the repository root, install the optional I2C dependency and run:

```sh
uv sync --locked --extra pwm
uv run --no-sync python -m workbench_arm_controller.hat_check
```

The default checks `/dev/i2c-1` at `0x40`, reads mode/prescaler and all sixteen
channel control registers, and checks PCA9685 reserved-bit constraints. It performs
no register writes. Use `--bus N --address 0x41` for a deliberately configured bus
or address; `0x70` is the group all-call address and is rejected. Missing bus,
missing library, permission errors, no response and inconsistent registers produce
JSON diagnostics with exit code 1; passing checks return 0. No bus scan is performed.
If `/dev/i2c-1` is missing, enable I2C in `sudo raspi-config` (Interface Options >
I2C), reboot if requested and retry. Check access with `ls -l /dev/i2c-1` and
`id -nG`; on Raspberry Pi OS, membership in the `i2c` group normally grants access
(log in again after changing membership). Power down before fitting the HAT.

For a stronger register communication test, stop other processes using this
controller, support the arm and disconnect servo power, then run:

```sh
uv run --no-sync python -m workbench_arm_controller.hat_check --register-test
```

This writes two patterns to the disabled SUBADR1 register, verifies each readback,
and restores and verifies the original value even after a test failure. It refuses
if that subaddress is enabled. It never changes PWM outputs, mode or frequency;
existing pulses can continue. Restoration failure is reported as a failure.
Avoid concurrent controller access throughout either check.

These checks establish register communication, not complete HAT functionality.
The PCA9685 has no chip-identification register: a pass indicates compatible
register behavior, not proof of the Waveshare brand. Servo power, output-enable,
oscillator operation, signal timing and servo movement require separate physical
measurement. Before connecting servos, verify approximately 50 Hz and calibrated
pulse widths at the output headers with a scope or logic analyzer during supervised
commissioning. This diagnostic does not generate that test waveform or enable retrieval.

References: [Waveshare Servo Driver HAT](https://www.waveshare.com/wiki/Servo_Driver_HAT)
(default address and setup) and [NXP PCA9685 datasheet](https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf)
(register map, reserved bits and disabled subaddresses).

## Offline motion rehearsal and commissioning records

The [motion design](../../docs/arm-control.md) describes startup/readiness, bounded
commands, independent completion evidence, stop, faults and recovery, including the
open physical decisions. `rehearsal.RehearsalController` is an independent simulation
model using a dedicated durable `Store`; it has no hardware callback or application
backend. Commands and simulated checkpoint observations remain separate. Deadlines
fault, stop stays unconfirmed, recovery needs fresh evidence, and submitted IDs never
replay. Run its scenarios with:

```sh
uv run --no-sync pytest tests/integration/test_rehearsal.py -q
```

The [commissioning CLI](../../docs/commissioning.md) prepares editable notebooks with
unknown measurements unset, validates records, freezes calibration/trial evidence and
summarizes physical and simulated acceptance separately. Neither tool changes the
PCA9685 hardware refusal or qualifies a physical path.
