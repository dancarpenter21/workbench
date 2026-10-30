# Consolidate arm geometry around the ROS model

Discussion recorded October 30, 2026. This is a proposed direction for future work;
it does not change runtime behavior or authorize physical execution.

## Current models and why both exist

The arm is a DFRobot ROB0036 V2. The repository currently maintains two geometric
representations:

| Model | Purpose | Geometry and coordinates |
| --- | --- | --- |
| ROS robot model, rendered as URDF/Xacro | Link and joint description for visualization and exploratory MoveIt planning | Oriented collision boxes; link-relative metres and radians |
| Custom capsule model | Table-clearance checks for guarded movement | Capsule endpoints and radii in table-relative millimetres, with measured joint axes and pulse-to-angle calibration |

A capsule is a cylinder with hemispherical ends. A joint pivot requires both a
point on the rotation shaft and an axis direction. Collision envelopes and joint
pivots describe different aspects of the arm.

The custom checker accounts for measurement error, joint-angle uncertainty, and
servos progressing asynchronously. It checks clearance across the movement, not
just at its endpoints. It does not model self-collision, other obstacles, or
mechanical failure. Free-play Jog does not receive these table-clearance checks.

The ROS transition retained that checker because physical planning execution is
not ready. Planning execution is currently mock-only; monitored physical control
integration, measured scene/calibration, feasibility trials, and physical
acceptance remain outstanding. The demo is kinematic, not a dynamics simulation,
and its synthetic geometry must not become physical calibration.

References: [ROS status and model conventions](ros/README.md),
[movement checks](docs/movement.md), and
[measurement conventions](docs/measurement-illustrations.md).

## Intended destination

Use one measured ROS robot model as the source of link geometry, joint pivots,
axes, and limits. Retire the separately maintained capsule geometry and its
measurement editor once replacement checks are validated.

URDF is a description, not a movement protection by itself. Removing duplicate
geometry does not remove the need for a checker or hardware-specific calibration.
Keep pulse-to-angle mappings, tracking/backlash error allowances, preparation
requirements, current monitoring, and fault handling alongside the robot model.

Our commanded joint positions are not measured physical joint feedback. A
collision-free intended trajectory alone does not establish that the servos
follow it. The replacement must retain conservative coverage of unequal joint
progress and uncertainty throughout guarded movement.

Full physical MoveIt execution need not block this consolidation: first adapt
guarded clearance checking to consume the ROS model's collision geometry. Some
project-specific checking code may remain even after the capsule model is gone.

## CAD authoring options

FreeCAD is the recommended starting point for measured parts and assembly joints.
Its Assembly workbench supports revolute joints for servo shafts. Simplified
solids can represent links and collision envelopes; detailed screws and cosmetic
features are unnecessary.

- [FreeCAD Assembly tutorial](https://freecad.github.io/Website/news/tutorial-getting-started-with-the-assembly-workbench/)
- [Onshape-to-robot](https://onshape-to-robot.readthedocs.io/en/stable/) is an
  alternative CAD-to-robot-description workflow for Onshape assemblies, including
  URDF export. Integration with this project's model format would still be needed.

Measure actual shaft centres and directions, pivot-to-pivot distances, component
dimensions, and the full claw sweep. Assemble simplified parts around those
shafts, then add conservative enclosing collision shapes covering brackets,
servo bodies, and fingers. CAD organizes measurements; it cannot supply measured
servo offsets, backlash, or pulse-to-angle calibration.

Today, geometry must be transferred into the separate ROS robot model and Capsule
measurements editors. The desired workflow records geometry once. CAD import or
export automation is a future integration choice, not an existing capability.

## Proposed migration sequence

1. Audit guarded movement call sites, existing clearance guarantees, calibration
   inputs, model validation, and runtime activation behavior. Establish tests that
   describe the protections to preserve before replacing their implementation.
2. Complete the measured ROS geometry and define the table relative to its base
   frame. Explicitly handle units, joint zero poses, axis signs, collision-box
   orientations, uncertainty margins, and the complete claw envelope. Never infer
   missing physical measurements from synthetic models.
3. Implement conservative table-clearance checks using that geometry, preserving
   whole-movement coverage under asynchronous servo progress and angular error.
   Choose and document the checking algorithm before implementation; endpoint-only
   or nominal-trajectory-only checks are insufficient replacements.
4. Validate offline and in mock mode against existing capsule-checker scenarios
   and independent analytic geometry cases. Compare outcomes while recognizing
   that different enclosing shapes can legitimately give different clearances.
5. Integrate the replacement into guarded output. Preserve rejection of invalid
   or incomplete geometry, explicit model activation, and session consistency.
   Model validation must not independently authorize hardware motion.
6. Retire capsule-specific configuration, APIs, and UI only after replacement
   validation. Preserve historical measurements for review; do not automatically
   reinterpret table-frame capsule data as link-local ROS measurements.
7. Update documentation and generated contracts as applicable, rebuild the ROS
   overlay, and perform separate supervised physical acceptance. Physical MoveIt
   execution remains a subsequent task with its own readiness requirements.

## Validation and hardware requirements

Cover safe and colliding poses, intermediate collisions with clear endpoints,
unequal joint progress, uncertainty margins, rotated boxes, coordinate conversion,
axis signs, full claw envelopes, missing calibration, and checking-budget failure.
Use mock mode for automated tests and distinguish simulated results from physical
acceptance. Do not expand free-play behavior or enable automated retrieval as an
incidental part of this migration.

Every physical claw test must retain continuous INA219 readings at address 0x41,
including before enabling output and throughout movement and powered holding.
Missing, stale, invalid, or failed readings require stopping, attempted PWM
shutdown, and instructions to cut physical servo power, with no automatic retry.
With verified shunt/current scale, abort at abs(current) >= 2.5 A. Until verified,
show raw shunt readings and explicitly mark amperes and the cutoff unverified;
never assume a shunt value. Follow AGENTS.md and commissioning procedures.

Implementation details still to resolve when this work begins include the
ROS-geometry clearance algorithm, calibration interface, legacy-data transition,
and whether CAD integration belongs in the first migration or a later change.
