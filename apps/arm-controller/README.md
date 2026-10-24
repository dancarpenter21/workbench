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

## Manual servo trial (channel indices 4 and 5)

`base_servo_test` is a separate supervised commissioning tool. It does not use or
mark the six-joint calibration as measured. Its defaults are an **offline plan**:

```sh
uv run --no-sync python -m workbench_arm_controller.base_servo_test
```

To test **servo 4** (zero-based numbering 0–5), select `--channel 4`.
Start by checking the offline plan, then run the supervised test:

```sh
uv run --no-sync python -m workbench_arm_controller.base_servo_test --channel 4
uv run --no-sync python -m workbench_arm_controller.base_servo_test --channel 4 --live
```

This starts at the nominal electrical midpoint, **1500 us**, using the same
restricted window and steps as servo 5. The unpowered joint's resting position
does not establish its powered neutral pose. Support every link throughout the
trial and shutdown; disconnect power to the other five servos. Without
`--channel`, the test continues to select base servo 5.

For a later live trial, begin with servo power OFF, other five servos disconnected
from power, arm supported, base secured, correct polarity/common ground and the
rotation path and cables clear. Use the nominal 5 V servo supply and keep the
physical cutoff within reach. Stop other processes accessing this HAT. Then:

```sh
uv run --no-sync python -m workbench_arm_controller.base_servo_test --live
```

The terminal asks for `PREPARE` before accessing I2C. It requests and verifies all
16 channels full-off, configures approximately 50 Hz, then asks you to power only
the selected servo. Type `START` to command 1500 us. **The first pulse may produce a
large movement from an unknown position.** If that movement cannot occur freely,
do not start; establish the servo's neutral position with its mechanical load
safely decoupled first. Do not force the servo shaft by hand.

Use `+` or `-`, followed by Enter, for a 10 us pulse change; `q` then Enter exits.
Direction is not yet calibrated. The fixed 1450–1550 us trial window constrains
requested and quantized pulses, but is not a measured mechanical safety envelope.
There is no sweep, return-to-center command, current sensing or stall detection.
Cut physical power immediately for binding, persistent buzzing without movement,
unexpected movement or heating; do not wait for the terminal to respond.

The session stops after 15 seconds without input or 60 seconds total after setup.
Exit, EOF, Ctrl+C, SIGTERM and normal I2C errors attempt to turn all outputs off
and verify the off registers. Turn servo power OFF afterward. Disabling pulses
can release a load and is not proof of a physical stop. Host failure, SIGKILL or
an I2C fault can leave pulses active; this is not a hardware watchdog. Nominal
pulse timing assumes the internal 25 MHz clock and still requires measurement.

## Combined neutral trial (servos 1–5; claw excluded)

After confirming each selected joint can move to its midpoint with clearance,
use this separate fixed-pose test:

```sh
uv run --no-sync python -m workbench_arm_controller.neutral_servo_test
uv run --no-sync python -m workbench_arm_controller.neutral_servo_test --live
```

With **all servo power OFF**, keep claw/servo 0 physically disconnected from power, secure the
base, support every link, and keep the cutoff accessible. Type `PREPARE` to
disable all outputs and program only channels **1–5** at **1500 us**.
Then apply power only to those five servos while supporting the arm and observing
the combined movement. This tests an electrical midpoint pose; it does not measure
position or qualify a combined path. Writes are sequential and motion is not
synchronized or speed controlled. There are no jogging commands in this test.
Claw/channel 0 stays disabled; keep its power disconnected pending its dedicated
safety wiring.

Press Enter to continue observing; `q` then Enter disables all outputs. Inactivity
for 15 seconds, the 60-second session deadline, or interruption also attempts to
disable all outputs. Support the arm throughout because disabling pulses can
release the links. Turn physical servo power OFF before removing support. Cut
power immediately for binding or unexpected movement. No hardware test is run by
the offline command, and automated retrieval remains disabled.

## Claw neutral and INA219 test

The standalone claw test programs only **channel 0 at 1500 us** on the HAT at
`0x40` and polls the INA219 at `0x41` on bus 1. It does not jog or sweep.
Preview without touching hardware:

```sh
uv run --no-sync python -m workbench_arm_controller.claw_servo_test
```

With all servo power OFF, other five servos isolated from power, the arm supported,
and the claw clear, run:

```sh
uv run --no-sync python -m workbench_arm_controller.claw_servo_test --live
```

Type `PREPARE` to check the sensor, disable all outputs, and program claw neutral.
Then apply power only to the claw. Applying power may cause a large movement.
The test prints bus voltage and signed shunt voltage approximately every 100 ms;
without a known shunt resistance it explicitly reports current as unknown.
No sensor configuration or calibration registers are changed.

Once the shunt resistance is verified, provide it with `--shunt-ohms OHMS`.
For example, **only for a verified 0.1 ohm shunt**, use
`--live --shunt-ohms 0.1`. Current is calculated directly from shunt voltage;
`abs(current) >= 2.5 A` aborts the test and attempts to disable PWM, regardless
of current direction. `--limit-a` may lower that threshold but cannot exceed 2.5 A. Do not assume the
shunt resistance from the board's appearance or price.

Enter `q` to stop. The 60-second deadline, EOF, Ctrl+C, SIGTERM, sensor faults,
and current-limit trips also attempt to disable all outputs with readback.
Turn physical servo power OFF afterward. PWM shutdown does not disconnect power,
and polling can miss transient peaks; this is not a hardware current limiter.
Cut physical power immediately for binding, unexpected motion, or heating.

### Include the claw in the neutral starting pose

For this bench's all-six neutral starting reference, add `--include-claw`:

```sh
uv run --no-sync python -m workbench_arm_controller.neutral_servo_test --include-claw --live
```

This explicitly includes channel 0 at 1500 µs (observed relaxed closed claw).
All servo power must be OFF during preparation. Support the complete arm and
clear the claw before applying power. The default command still excludes the
claw. See [the app starting reference](../../docs/manual-controls.md#neutral-starting-reference-for-the-current-bench)
for the existing app preparation flow and remaining commissioning requirements.


### Web pulse testing and continuous current monitoring

The Workbench panel now supports **All servos holding**, typed pulse targets,
reviewed per-joint trial windows, and exported timestamped raw INA219 samples.
The arm service continuously monitors the claw at `0x41`, including stationary
holds and stopped sessions. Sensor/logging faults attempt PWM shutdown and require
physical power disconnection. Orderly service shutdown also attempts PWM full-off.
Support the arm before shutdown. The default current scale remains unverified.
See [the complete workflow](../../docs/joint-trials.md) before physical use.


### Read-only arm-off commissioning capture

With all servo power disconnected and the arm service already monitoring INA219:

```sh
uv run --no-sync python -m workbench_arm_controller.off_baseline --capture --arm-off-confirmed --duration 60
```

Use `--duration 1800` for a 30-minute capture. This reads status only and saves
unique raw samples, timing, health, statistics, evidence hash and proposed bounds
under `.runtime/commissioning/off-baselines/`. It never opens I2C or sends PWM.
Do not restart the service during acquisition. See
[commissioning logging](../../docs/commissioning-logging.md) for the measured
initial off reference, record format and limitations.


### Neutral commissioning evidence

The selected `arm.neutral_reference` file records the completed 30-minute
all-six neutral hold's claw voltage/shunt statistics and evidence hashes.
`GET /commissioning/evidence`, Workbench status/events and diagnostic downloads
expose the reference. Live comparison is advisory and requires known six-channel
1500 µs commands; no motion limit, shunt calibration or commissioning flag is
inferred from the capture. See [commissioning telemetry](../../docs/commissioning-logging.md).
