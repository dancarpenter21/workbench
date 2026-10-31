# Standalone ROB0036 movement module

`workbench_arm_controller.movement` is a hardware-independent table-clearance
checker and stateful movement gate. Guarded all-holding commissioning uses it
through `SingleServoOutput`; `Pca9685Output` is guarded by default. Manual Jog
explicitly selects free play, with no table-clearance checks. The existing
API error/message fields surface movement rejection without a new HTTP endpoint.

## V1 operating assumptions

- All six requested pulses at **1500 us** are the owner-tested safe startup.
  This is the reference configuration, not a claim that all links are horizontal.
- Base/channel **5** rotates parallel to the table and cannot change height.
  Channels **4, 3, 2, 1** form the height chain. Claw/channel **0** is enclosed
  by geometry covering its entire opening range.
- **A successful complete PWM write means successful movement for v1.** The
  next move starts from that encoded target, without waiting for position sensors
  or operator confirmation. Results explicitly identify
  `position_source: assumed_from_successful_pwm_write` and `measured_feedback: false`.
  Joint tracking error/overshoot allowances still apply to every check. V2 can
  replace this assumption with measured position.
- Failed/partial writes invalidate the assumed position and prohibit retries.
  Recovery requires a new, explicitly established startup session. Writing 1500
  us from an arbitrary fault position is not an automatic recovery command.
- The table is an infinite horizontal plane. Only moving parts are checked;
  the fixed mounting surfaces intentionally contact the table. The first model
  is for an empty claw; add payload geometry and load allowances before carrying
  a tool. Self-collision, other obstacles, and mechanical failures are not modeled.

## Supply measured geometry

Use **Initialization → Capsule measurements** to record each capsule's endpoints,
radius, and parent joint. All measurements share one fixed table frame. For the
measurement pose, keep servos **2–6 / channels 1–5 at 1500 us**. Set servo
**1 / channel 0** to an operator-measured full-open pulse using Initialization's
current-monitored pulse controls; do not assume that 1500 us means fully open.
Measure the open claw, and make its capsule large enough to enclose the fingers
and mechanism throughout the full opening/closing range. The capsule follows
channel 1, so opening the claw does not redefine the arm's reference frame.
The custom claw pulse is entered in the existing positioning control; it is not
stored as a new geometry reference or inferred from the capsule dimensions.
New motion sessions still initialize all six channels at 1500 us before applying
the requested claw pulse. Continuous INA219 monitoring applies during movement
and powered holding, including measurement; sensor faults require shutdown and
operator power cutoff, with no automatic motion retry.

Expand **Joint axes and angle calibration** to enter the rotation
axes, pulse/angle pairs, and angle-error allowances needed by the height model.
This is separate from the optional minimum/maximum PWM fields.

**Save measurements** stores incomplete drafts without opening or commanding
hardware. **Activate for guarded movement** validates the complete model and its
startup clearance, then makes it available to the next guarded output session.
End active movement sessions before activation. A later draft does not replace
the last active revision; both are retained across restarts. Stale edits are
rejected, and measurements are scoped to the assembly, I2C setup, and operating
mode. The REST interface is `GET/POST /commissioning/geometry`, proxied by the
coordinator. Draft storage never changes saved PWM limits.

**Jog requires no measurement or pulse-limit configuration.** Manual uses the
[independent assembly bounds](assembly-profile.md). Saved commissioning endpoints
constrain only LLM harness mode and do not narrow Manual. Jog
always displays **Free play · table-clearance checks off**, even if a geometry
model has been activated. Startup remains 1500 us. Current monitoring, the
hold-to-jog input, rate limits, watchdog, and fault handling still apply. A complete
successful PWM write still advances the v1 assumed position. The bypass is an
explicit `MovementSession(free_play=True)` choice made only by ManualControl;
ordinary movement sessions remain guarded by default.

Alternatively, supply a geometry file directly:

Copy `config/workbench/movement-geometry.example.json` to a calibration file and
replace every `null` with measured data. The template deliberately supplies no
invented physical dimensions or tolerances. Set `calibrated: true` only after
checking the completed geometry and assigning a new `revision`.

All coordinates share **one right-handed table frame anchored to base neutral**:

- **Origin `(0, 0, 0)`:** the intersection of the base rotation axis and the table surface.
- **`+X`:** the arm's direction of reach projected onto the table with base
  servo **6 / channel 5 at 1500 us**.
- **`+Y`:** 90 degrees counterclockwise from `+X`, viewed from above the table.
- **`+Z`:** upward from the table; the table is `Z = 0` and `X × Y = Z`.

Positive rotation about `+Z` turns `+X` toward `+Y`. For example, a positive
90-degree rotation about the base axis sends `(100, 0, 50)` to `(0, 100, 50)` mm.
This defines an angle sign, not the direction of increasing servo pulse width;
pulse-to-angle direction must be established separately on the assembled arm.
The height checker ignores base yaw because this rotation preserves height.

The frame stays fixed when the base turns and has no implied alignment with the
video feed. Base neutral aligns the direction of reach with `+X`; it does not
force every endpoint onto the X axis. Measure actual heights and sideways offsets.
Use this frame for every endpoint, joint origin and joint-axis vector; coordinates
are not relative to individual links. Saved values are not automatically
transformed: measurements made in a different horizontal frame must be expressed
consistently in this frame, including rotating joint-axis vectors.

### Joint axes at the neutral measurement pose

The owner-specified axes in this frame are:

| Servo / channel | Part | Positive axis vector |
| --- | --- | --- |
| 2 / 1 | Claw mount | +X `(1, 0, 0)` |
| 3 / 2 | Wrist section | +Y `(0, 1, 0)` |
| 4 / 3 | Middle arm | +Y `(0, 1, 0)` |
| 5 / 4 | Upper arm | +Y `(0, 1, 0)` |
| 6 / 5 | Base | +Z `(0, 0, 1)`; height-invariant yaw |

Positive rotation about +X turns +Y toward +Z. Positive rotation about +Y turns
+Z toward +X and +X toward −Z. Axis vectors describe the neutral pose; upstream
joint motion rotates downstream axes with the arm. Servo 1 / channel 0 remains
represented by the full opening/closing capsule, rather than a height-chain axis.

The template supplies these owner-specified axes for channels 4–1. Joint origins,
angle samples and error allowances still require measurements. Existing saved
axes are preserved. In **Joint axes and angle calibration**, each **Use neutral
axis** button edits that joint's draft vector without saving, activating or moving
hardware. Recheck measured angle signs when changing an axis: changing an existing
−Y vector to +Y requires negating its angle samples to describe the same motion.

| Field | Measurement/convention |
| --- | --- |
| `joints[].origin_mm` | A point on that servo's rotation axis, in the reference/table frame |
| `joints[].axis` | Axis direction vector; normalized on load; positive angles follow the right-hand rule |
| `joints[].pulse_angle_deg` | Ordered measured `[pulse_us, angle_deg]` pairs, including `[1500, 0]`; pulses increase and angles strictly increase or decrease |
| `joints[].error_deg` | Nonnegative bound covering tracking error, repeatability, backlash and overshoot |
| `capsules[].a_mm`, `b_mm` | Capsule centerline endpoints in the reference/table frame |
| `capsules[].parent_channel` | Last joint affecting the component, from 4 through 1 |
| `capsules[].radius_mm` | Positive enclosing radius, covering the component's full thickness |
| `geometry_error_mm` | Additional radial allowance for measurement uncertainty, table alignment error and sag |
| `clearance_mm` | Required positive gap above the table after all allowances |

Include capsules for each moving link plus additional servo housings/brackets as
needed. A capsule named `claw`, attached to channel 1, is mandatory; its envelope
must include both fingers throughout the opening range. More capsules can describe
irregular shapes. Model completeness must be checked against the actual assembly.

Nominal conversion can guide measurement: the five 180-degree servos have a
0.09-degree/us slope; the base has a 0.135-degree/us slope. Installed signs and
offsets need measurement. Only channels 4–1 need angle mappings for height; pulse
limits still apply to all six channels. Include enough mapping range to cover the
**quantized** pulses, including the approximately 1498.16-us nominal encoding of
a 1500-us request with the existing clock settings. No extrapolation is allowed.

Set this field in the selected development/runtime configuration:

```json
{
  "arm": {
    "movement_geometry": "config/workbench/movement-geometry.json"
  }
}
```

Relative paths resolve from the repository root. Hardware mode requires
`domain: physical`; standalone simulated fixtures use `domain: simulated`.
Configuration is loaded for each new physical output session. Restart/reprepare
after calibration changes; an active session never silently swaps models.

With missing or invalid geometry, guarded output permits the established neutral
startup but rejects subsequent movement. This does not restrict free-play Jog. Legacy
isolated scripts are neutral-only: with the other servos unpowered, they cannot
establish a full-arm starting pose. Use guarded all-holding controls for movement.
Existing mock application behavior remains synthetic; the offline checker and
fake-bus integration tests exercise geometry without enabling hardware.

## Algorithm and standalone API

The model applies product-of-exponentials rotations about the measured axes.
For a transformed capsule, clearance is `min(a.z, b.z) - radius - geometry_error`.
Base yaw and claw opening do not change the conservative height envelope.

A movement check covers the Cartesian product of starting/target joint intervals,
expanded by angular errors. Thus it includes asynchronous joint progress and
sequential writes, not just a synchronized interpolation. Recursive subdivision
proves clearance using a displacement bound `sum(R[j] * half_width[j])`, where
each `R` is a global reach bound obtained from chain lengths and the triangle
inequality. Sampling alone cannot authorize a move. Unproven boxes at the budget
limit are rejected; the default budget is 4096 boxes.

```python
model = ArmGeometry.from_dict(measured_geometry)
session = MovementSession(model)

# Both dictionaries use integer channel keys 0..5. The output adapter first
# validates pulse limits and quantizes them; the guard never encodes hardware.
result = session.check(requested_pulses, encoded_pulses)  # No writes/state advance
session.move(requested_pulses, encoded_pulses, dispatch)  # Check, write, commit
```

The first `move` must specify all six 1500-us requests. Later moves may specify a
subset; other channels retain their assumed positions. `dispatch` performs only
the prevalidated writes and must raise on any write/readback failure. A rejected
move never invokes it or alters the assumed position. Callers must serialize the
session; the app already uses its arm I/O lock. `check_pose` and `check_motion`
are also available on `ArmGeometry` for hardware-free analysis.

Results include acceptance, reason, limiting component, conservative modeled
clearance bound, required gap, revision, and boxes checked. A budget rejection has
no certified clearance. Ordinary rejection preserves holding PWM and ends jog
requests; it does not shut down holding torque or automatically find a new path.

Physical output adapters require a current-check callback and service it during
subdivision and writes. The owning application's continuous INA219 polling must
also remain active while holding. The mandatory sensor/current fault response
remains attempted PWM shutdown, instruction to cut physical power, and no retry.
No geometry result bypasses current monitoring or enables automated retrieval.

## Offline use and validation

The standalone CLI checks a complete target from the verified startup, using the
existing pulse calibration format and a joint-name-to-pulse target JSON file:

```sh
uv run python -m workbench_arm_controller.movement \
  --geometry config/workbench/movement-geometry.json \
  --calibration path/to/measured-pwm-calibration.json \
  --target path/to/target-pose.json
```

It never opens I2C. Exit code 0 means the model accepted the move, 1 means rejected,
and 2 means malformed inputs. Output is labeled `dry-run` and includes the model's
physical/simulated domain; acceptance is not physical test evidence.

Tests use synthetic geometry and fake outputs, including safe endpoints with an
unsafe intermediate path, asynchronous progress, arbitrary wrist axes, yaw
invariance, quantization, v1 assumed-position updates, current faults, and partial
writes. Run with an explicit mock config to avoid inherited runtime settings:

```sh
WORKBENCH_MODE=mock WORKBENCH_CONFIG=config/development/default.json uv run pytest -q
```

Before physical movement, compare predicted surface heights with independent
measurements at startup and additional clear poses. Establish error allowances
and approach the exclusion boundary only under supervision, without deliberately
making contact. Every powered claw test must continuously read INA219 `0x41`,
including powered holding; apply the repository's verified/unverified-scale rules.
