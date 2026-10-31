# DF Robot V2 assembly profile

Issue #2 implementation starts with T1 (#3). The executable profile is
`apps/arm-controller/src/workbench_arm_controller/assembly_profile.py`, version
`dfrobot-v2/1`. Loading it performs no device access or movement.

| Servo | Channel | Independent bounds (µs) | Nominal shaft scale (°/µs) |
|---|---|---|---|
| Base 6 | 5 | 500–2500 | 0.135 |
| Shoulder 5 | 4 | 600–2100 | 0.09 |
| Elbow 4 | 3 | 775–2500 | 0.09 |
| Wrist bend 3 | 2 | 500–2500 | 0.09 |
| Claw rotation 2 | 1 | 500–2500 | 0.09 |
| Claw 1 | 0 | 850–1600 | 0.09 |

Shared six-channel and single-servo output validation intersects supplied saved
limits with these bounds. Empty intersections and intervals without representable
PWM ticks are rejected. Invalid requested targets are rejected before PWM writes;
valid boundary targets quantize inward. Manual advertises these independent bounds
and retains its existing jog stopping behavior at endpoints. Its legacy manual
configuration remains unused. Harness saved bounds continue to narrow motion.
The startup request remains 1500 µs on every channel.

Nominal travel over 500–2500 µs is 270° for the base and 180° for the other
servos. Restricted bounds never rescale this slope. Shaft travel does not measure
claw aperture. Calibrated direction and measured uncertainty remain unknown;
base +X has the specified 1500 µs reference.

The profile preserves operator observations: base rotates about +Z; shoulder is
reported +Y, rising with increasing pulse, roughly upright at 2000 µs and 45° down
at 600 µs. Shoulder bounds address base self-collision only. Elbow is reported −Y,
with increasing pulse pointing downward. Wrist bend is reported +Y, with a
horizontal parent and endpoints pointing claw −Z at 500 µs and +Z at 2500 µs.
Claw twist spans nominally 180°, and increasing claw pulse closes it. These
observations are not validated URDF calibration.

In-bounds combinations can still collide, especially across servos 5–2. This
profile does not enable automatic retrieval, planning or LLM motion. Preparation,
ownership, watchdogs, recovery and continuous INA219 monitoring remain required.
Tests use simulated output and do not establish physical acceptance. Following a
ROS overlay rebuild, hardware service restart requires disconnected servo power
and a supported arm; the operator must explicitly prepare again.
