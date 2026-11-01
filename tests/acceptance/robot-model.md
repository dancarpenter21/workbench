# Measured robot model acceptance

Status: pending physical measurement evidence. The Model workspace supplies
geometry editing, collision inspection and reference-pose comparisons; its
structure check and 3D preview alone do not satisfy this procedure.

## Prerequisites

Use the fixed arm/channel mapping and existing independent bounds. Record the
model revision, angle-calibration reference, mounting configuration, measurement
instruments, and uncertainty in the issue or linked notes. Do not repeat the
established 0.1 Ω shunt verification. Missing angle calibration blocks physical
planned execution, not existing Manual operation.

Before collecting evidence, record the allowed tool-position error (mm),
orientation error (degrees), and measurement uncertainty. The operator must select
these tolerances for the intended task before comparing results. Do not choose
thresholds retrospectively to pass measured residuals.

## Geometry inspection

1. Establish the base/table frame and marked zero pose. Measure joint pivot
   positions in their parent frames and record right-hand rotation axes.
2. Measure servo bodies and brackets as well as links. Check that every collision
   shape encloses its assigned component with the recorded uncertainty allowance.
3. Measure the entire finger/mechanism sweep relative to the hand frame, including
   intermediate openings. Servo shaft angle alone does not establish aperture.
4. Locate the empty-claw tool datum and measure the table and each obstacle in
   the base frame. Record their placement uncertainty.
5. Review every collision exclusion. Adjacent links can still collide away from
   their mounting interfaces. Sampling without a collision is not a justification
   to disable a pair.

## Pose comparison

Collect clear, supported reference configurations spanning the intended range of
base, shoulder/elbow, wrist-bend, and wrist-roll motion. Use existing Manual
controls only; the editor is not a motion interface. Record joint-angle evidence
separately from commanded pulses and compare measured tool positions/orientations
against ROS forward kinematics for the exact model revision. All residuals must
meet the previously recorded tolerances. Changed geometry, mounting, or angle
calibration requires repeating affected checks.

Any physical claw output or powered holding requires fresh INA219 0x41 readings
before output and continuously thereafter. Preserve raw shunt readings. Stop and
attempt PWM shutdown for missing, stale, invalid, or failed readings, or
abs(current) >= 2.5 A with the established verified scale. Cut physical servo
power on a fault; do not automatically retry. Support the arm with servo power
disconnected during service restarts.

## Digital collision regression

Use the exact measured model, scene, margins, and reviewed exclusions with MoveIt.
Demonstrate rejection of known in-bounds self-collision, table-contact, and
obstacle-contact poses, including combinations across channels 5–2. Also include
clear poses that pass. Test the claw's full envelope and cases where uncertainty
allowances change the outcome. Do not deliberately produce physical collisions.

Confirm that incomplete models, simulated evidence, stale revisions, and an
unavailable validator cannot authorize physical execution. A clear single pose
is not a collision-free trajectory or physical motion approval.

## Five-axis orientation limits

The five arm axes cannot independently achieve every six-dimensional tool pose.
Record the achieved tool orientation with each measured position. The existing
position-only IK demo does not establish requested orientation accuracy. Target
solving and operator-approved trajectory execution belong to later parent-issue
work; unsupported orientation constraints must not be silently relaxed.

Close issue #6 only after measured evidence and authoritative collision results
are attached. Mock results remain explicitly simulated. Automated retrieval stays
disabled until its separate commissioning requirements are satisfied.
