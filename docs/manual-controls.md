# Manual joint controls

The Jog tab has a Manual joints panel for supervised joint jogging. Commands
travel through the coordinator to the arm controller without calling the assistant,
voice, or vision services. The camera remains useful to the operator, but camera
observations do not authorize manual movement.

Mock mode exposes six simulated joints. Start it with
`npm run dev:mock`. No hardware library is opened by mock control. Manual acquisition
and tool retrieval are mutually exclusive across all browser sessions.

## Wiring and controls

The confirmed wiring runs from the base on servo/channel 5 to the claw on 0.
Panel selection numbers follow the physical order from base to claw:

| Selection | Identifier | Label | PCA9685 channel |
| --- | --- | --- | --- |
| 1 | `base` | Base | 5 |
| 2 | `joint_2` | Joint 2 | 4 |
| 3 | `joint_3` | Joint 3 | 3 |
| 4 | `joint_4` | Joint 4 | 2 |
| 5 | `joint_5` | Joint 5 | 1 |
| 6 | `claw` | Claw | 0 |

The channel mapping is fixed from base 5 through claw 0.

Choose an input and click **Enable manual control**. Only the selected joint moves:

- **Keyboard:** focus the panel; select with 1–6 or Up/Down, then hold Space plus
  Left/Right. Hold Shift for precision. Escape invokes Stop while manual mode is active.
- **Xbox controller:** connect through USB or OS Bluetooth pairing, press a controller
  button to expose it to the browser, select that controller and the Xbox input.
  D-pad Up/Down selects a joint; hold left bumper and deflect the left stick horizontally.
  Right bumper enables precision and B invokes Stop. The stick has a 15% dead zone;
  speed scales proportionally outside it. Only standard browser mappings are accepted.
- **On-screen buttons:** select that input and hold either direction button. Releasing
  or cancelling the pointer ends the jog.

Precision is 25% of the 100 µs/s Jog rate. Select a joint with controls neutral;
release enable and direction after changing joints or input sources before jogging
again. Increasing pulse width is shown; its physical direction must be observed during commissioning. Text fields retain their normal keyboard behavior.

The browser Gamepad API requires a secure context. Use the browser on the Pi through
`http://localhost:5173`, an SSH port forward to localhost, or an HTTPS reverse proxy
for remote access. Ordinary HTTP on a LAN hostname/IP may disable gamepad access;
keyboard and on-screen controls still work. Browser support and OS controller
mapping must be checked on the actual device. See the
[Gamepad specification](https://www.w3.org/TR/gamepad/).

## Hardware preparation

Follow [hardware setup](hardware.md) and [commissioning](commissioning.md) first. Save valid minimum and maximum pulse limits for all six joints in Initialization. Each range must include the 1500 µs starting pulse and a representable PCA9685 tick. Jog then uses those limits automatically. Its default rate is 100 µs/s; physical suitability of that rate and the starting arrangement must be confirmed by the operator. PCA9685 automatic retrieval remains disabled.

Opening the dashboard does not command arm movement. The current monitor must read the claw INA219 before output and continuously throughout powered movement and holding.

At each hardware service startup:

1. Disconnect servo power and physically support the arm and any load. Put the
   assembly into the reviewed starting arrangement for applying the 1500 µs
   initial targets. Verify channel wiring, clearance and the accessible power cutoff.
2. Check **Servo power is disconnected and the arm is supported**, then **Prepare
   outputs**. This initializes the PCA9685 and programs all six reviewed initial
   targets while motor power is disconnected. It clears prior recovery state.
3. Apply servo power through the physically commissioned procedure. Initial target
   application can move the servos; verify the observed response and stationary state.
4. Confirm the ready checkbox, select a joint and explicitly enable manual control.

Preparation after a fault requires disconnected power and support again. Repeating
preparation intentionally reinitializes all six outputs; it is not a powered recovery
or an automatic home operation. Ordinary acknowledged recovery can clear a software
Stop or input-timeout latch when the output state remains known.

## Hold, stop and failure behavior

Displayed values are commanded, quantized pulses in µs, never measured joint angles.
Only one target changes at a time. The controller integrates normalized input at
50 Hz, retains fractional changes, clamps to saved bounds, and caps each update's
elapsed time at 20 ms to avoid catch-up jumps. Pulse quantization can make individual
updates larger than the fractional target increment; the configured rate describes
target progression, not measured physical joint speed.

Direction release holds the target and permits another jog. **End manual control**
invalidates ownership and retains the targets. **Stop arm** invalidates ownership,
retains the targets, and requires inspection and recovery. These operations cease
changing targets; they cannot confirm physical standstill or stop an actuator already
approaching a target. Use the physical cutoff when control is uncertain.

The browser sends current input every 100 ms, without a queued command backlog.
During jogging, the arm service expires ownership after 300 ms without valid input
and requires recovery. Idle sessions retain ownership when browser timers slow down.
Changing tabs or window focus clears held keys and stops jogging while keeping Manual
enabled. End manual control, Stop, page unload, connection failures, and monitoring
faults end the session. Reconnection requires explicitly enabling again.

A partial I2C write makes all commanded-state readouts unknown, latches a fault, and
prevents automatic retries. Inspect with power disconnected and prepare again.
Restart never restores ownership or assumes old persisted targets match the arm.

Manual release retains PWM targets while continuous claw monitoring stays active.
Orderly service shutdown attempts PWM full-off before closing the current monitor;
support the arm and disconnect physical servo power before shutdown.
A service/host crash can also leave those pulses active, and this software watchdog
cannot run after a host failure. Follow the commissioned support and physical
power-disconnection procedure when shutting down the bench.

## APIs

Coordinator endpoints under `/manual` forward typed commands to the arm controller:

| Endpoint | Behavior |
| --- | --- |
| `GET /status` | Capabilities, mapping, targets, availability, activity and recovery |
| `POST /prepare` | Requires `power_disconnected_and_arm_supported: true` |
| `POST /session` | Requires `ready_acknowledged: true`; returns private UUID token |
| `POST /input` | Token, increasing integer `sequence`, `joint_id`, signed `speed` in [-1, 1] |
| `POST /release` | Invalidates the supplied ownership token and retains targets |

The first input for a newly selected joint must have zero speed. Clients must send
zero while idle to retain a session. Duplicate/out-of-order sequences do not renew
ownership. Tokens are omitted from shared status and never persisted. The existing
`/stop` and acknowledged `/recover` endpoints cover both operating modes.

## Neutral starting reference for the current bench

The operator's bench observations on 2026-10-23 establish **1500 µs for all six
channels (0–5)** as the intended engaged starting reference. Servos 1–5 were
previously tested at neutral; claw/channel 0 returns to a relaxed closed position
at 1500 µs, and decreasing its pulse opens it. This is an operator-observed
reference for this assembly, not measured position feedback or a validated path
from every possible pose.

The manual configuration example now supplies these six `initial_us` values.
Travel limits, rates, direction labels, and commissioning flags still require
completion before manual jogging can be enabled. The existing **Prepare outputs**
flow uses these configured initial targets with servo power disconnected, followed
by operator-controlled power application and observation. Starting the app does
not energize or home the arm automatically.

To rehearse the complete neutral starting arrangement separately from the app:

```sh
uv run --no-sync python -m workbench_arm_controller.neutral_servo_test --include-claw
uv run --no-sync python -m workbench_arm_controller.neutral_servo_test --include-claw --live
```

Begin with all servo power OFF, the arm supported and the claw clear. `PREPARE`
programs all six targets before external power application. Observe the complete
assembly before using it as the app's starting arrangement. The test retains its
15-second input timeout, 60-second total timeout, and full-off exit behavior.
Support the arm throughout shutdown. With `--include-claw`, this combined test continuously reads the INA219 at
`0x41` and aborts on sensor faults. It reports raw shunt voltage; the ampere scale
and 2.5 A cutoff remain unverified. Main-app and commissioning control now use the same raw reader with continuous
service monitoring and fault-triggered PWM shutdown. See [pulse testing](joint-trials.md)
for unverified-scale labels, verified-shunt configuration, and physical acceptance.


## Typed pulse testing before limits are commissioned

Use **Initialization** to enter a pulse for any joint and click **Move**, or edit
minimum/maximum values and click **Save limits**. The first move initializes all six
outputs at 1500 μs; later moves affect only the selected row. Direct moves accept
500–2500 μs regardless of saved limits or blank Min/Max fields. Saved values are recorded
as manually entered limits, not observed movement evidence. See
[joint values](joint-trials.md) for monitoring and recovery details.

## Supervised 30-minute neutral hold

The separate `neutral_hold_test` runner supports a fixed-neutral hold of up to
1800 seconds. It does not extend or remove the original short neutral test's
15-second input and 60-second total timeouts.

Disconnect **all servo power**, secure/support every link, clear the claw/path,
and remain at the bench with the cutoff accessible. Stop the arm-controller
service and other HAT writers before starting this standalone runner. Keep the
other application services unchanged. After explicitly confirming these physical
conditions:

```sh
uv run --no-sync python -m workbench_arm_controller.neutral_hold_test \
  --live --power-off-confirmed --supervised --duration 1800
```

The runner requires fresh valid INA219 readings within the measured off bounds
before preparing channels 0–5 at 1500 µs. Continuous monitoring remains active
while waiting up to 120 seconds for external power. Apply power only after it
reports that neutral targets are programmed and monitoring is active. Observe
initial movement: the first movement is not rate-limited. The 30-minute timer
starts when supply voltage is detected; no further pulse changes are issued.

Samples are recorded at nominal 100 ms intervals under
`.runtime/commissioning/neutral-holds/<trial-id>/samples.jsonl` and in the arm
SQLite journal. `report.json` records requested/encoded pulses, setup, per-phase
sample counts, voltage/shunt/current statistics, and verified or failed PWM
shutdown. Only the **claw circuit** is measured by this INA219; currents in the
other five servos are not individually measured. With the current unverified
shunt scale, amperes and the 2.5 A cutoff remain explicitly **UNVERIFIED**; raw
bus voltage and signed shunt voltage are mandatory. This requires continuous
operator supervision and is not an unattended current-protected endurance test.

Missing/failed readings, conversion overflow, a 300 ms sampling gap, verified
`abs(current) >= 2.5 A`, power loss, timeout, interruption, or completion attempts
PWM full-off before closing the current monitor. Never resume motion
automatically after a fault. Support the links throughout shutdown and **cut
physical servo power**. Resume the main arm service after the standalone runner
has exited; starting the app does not restore neutral targets or motion ownership.

## Manual pulse entry bounds

Manual displays each servo’s independent assembly range beside its pulse input.
Empty, fractional, or out-of-range entries disable Move and cannot submit via Enter.
Values are not silently clamped. Saved commissioning limits apply only to LLM
harness mode; Manual requires no commissioning. In-bounds combinations can still
collide. See the [assembly profile](assembly-profile.md) for the channel bounds.
