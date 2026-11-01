# Model workspace

Open **Model** in the dashboard to edit the fixed DF Robot v2 measurement draft.
Manual and Config retain their existing roles. Previewing, saving, importing,
exporting and inspecting a model never prepare the arm or send servo commands.

## Measurements and migration

Lengths can be entered in inches or millimetres; angles use degrees. Stored JSON
uses metres and radians. Changing units preserves measurements. Imported JSON is
checked against the current assembly, fixed topology and evidence domain before
it replaces the editor contents. Import does not save or qualify the model.

Choose **Add measured scene and multiple boxes** to upgrade an existing draft to
schema version 2. Original single-box fields remain in the JSON for traceability.
New `boxes` arrays are authoritative. Each box has a unique name, dimensions,
centre, roll/pitch/yaw, and `margin_m` uncertainty allowance. Allowances expand
both faces of each box axis and are included in collision geometry itself, so
self-collision checks receive them as well as environment checks. Unknown
allowances remain blank; they are never silently assumed to be zero.

The legacy rotating claw box is retained as legacy data only. Measure the entire
finger/mechanism sweep as `claw_envelope` boxes in the hand frame. These remain
fixed to the hand throughout the claw servo's simulated travel. The tool datum has
no geometry. Table and obstacle boxes are measured in the base frame.

## Preview and inspection

The 3D view uses a read-only backend preview route through the ROS gateway or the
explicit HTTP backend. Missing dimensions omit that box; missing joint frames,
or parents omit dependent subtrees. Known frames with unknown axes or limits
are shown fixed at their reference pose with no slider. Omission messages identify what remains.
The grid has 50 mm spacing; it is not a measured table collision object. Preview
sliders change the displayed pose only. Enable **Show collision allowances** to
inspect the expanded robot collision boxes.

Preview URDF has no control interfaces. Placeholder speed/effort values permit
rendering incomplete drafts and do not establish measured limits. The ROS bundle
export requires complete geometry, scene, allowances and joint specifications;
its URDF uses the recorded limits. Export includes measured JSON, URDF, scene,
SRDF and a model hash. Neither export nor a complete flag authorizes execution.

**Check collisions at preview pose** runs the standalone MoveIt model inspector.
It has no controller, action or hardware interface. The result reports joint
bounds, colliding pairs, tool position and reference-pose residuals, bound to the
exact model hash. Model or pose edits invalidate the displayed result. An absent
or failed inspector reports unavailable, never clear. A collision-free pose is
not a collision-free trajectory.

Version 2 checks every link pair by default. The adjacent-pair review panel lets
you record intentional mounting-interface exclusions with reasons. Excluding a
pair hides all contact between its links, including contact away from the pivot;
review the entire operating range before recording an exclusion. No sampled
"never collides" exclusions are generated automatically.

## Reference poses and physical acceptance

Set preview angles to independently measured joint angles and capture a named
reference pose. Record position/orientation tolerances before comparing results,
then enter measured tool position and roll/pitch/yaw in the base frame. MoveIt
computes Euclidean position error and quaternion angular distance; the result
also requires the reference pose to satisfy bounds and collision checks. Missing
observations or tolerances remain incomplete. Computed results are not persisted
as permanent qualification and are recomputed for the inspected model.

Commanded pulses alone are not measured joint angles. Existing angle-calibration
evidence and supervised physical checks remain necessary. See
[robot model acceptance](../tests/acceptance/robot-model.md). Five arm axes cannot
independently achieve every six-dimensional tool pose; the existing position-only
IK demo does not establish requested orientation accuracy.

## Build and runtime

Regenerate contracts after interface changes and rebuild with:

```sh
npm run contracts
uv run python scripts/ros-native.py build --apps-only --component planning
```

The planning component installs the offline `model_validator` executable.
`scripts/model-inspect.bash` loads library paths and starts only that executable;
it does not launch ROS nodes. HTTP and ROS services use the same inspector.

For offline inspection, pipe a JSON object containing `urdf`, `srdf`, `scene`,
`joint_positions` and optional `reference_poses` to that script. All six joint
angles must be supplied through the application API. The ROS bundle contains the
first three inputs. Scene objects use `name`, `kind`, `size`, `xyz`, `rpy`, and
`margin_m`. The mock planner loads its legacy synthetic scene from
`ros/src/workbench_description/config/simulated-scene.json`; v2 models supply their
own scene and exclusions.

Hardware service restarts require servo power disconnected and the arm supported.
The build itself starts no hardware. The implementation does not relax existing
Manual, harness, preparation, watchdog, current-monitor or planned-execution gates.

## Inspect an exported bundle in RViz

Use the URDF from **Export ROS bundle** so RViz uses the same geometry and
recorded limits as the collision inspector. Extract it from the downloaded JSON:

```sh
uv run python - exported-bundle.json /tmp/workbench-model.urdf <<'PY'
import json
import sys
from pathlib import Path

bundle = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(bundle["urdf"])
# JSON is valid YAML for the ROS parameter-file parser.
Path(sys.argv[2] + ".params.json").write_text(json.dumps({
    "robot_state_publisher": {"ros__parameters": {"robot_description": bundle["urdf"]}}
}))
print("Model hash:", bundle["model_hash"])
PY
```

In each of three terminals, source `scripts/ros-env.bash` and set an unused
`ROS_DOMAIN_ID` distinct from the running workbench (normally domain 42).
Keep all three inspection terminals on that same isolated domain. Run:

1. `ros2 run robot_state_publisher robot_state_publisher --ros-args --params-file /tmp/workbench-model.urdf.params.json`
2. `ros2 run joint_state_publisher_gui joint_state_publisher_gui /tmp/workbench-model.urdf`
3. `rviz2`

Set RViz's fixed frame to `base_link`, add RobotModel using the
`/robot_description` topic, and add TF to inspect joint frames and `tool0`.
Toggle RobotModel's visual and collision geometry to compare measured boxes with
expanded allowances. Move only the isolated joint-state GUI sliders to inspect
the model's range; do not launch the hardware bringup or a controller.

The bundle URDF contains robot geometry; its separate `scene` array is loaded by
the offline collision inspector, not by RobotModel. Use the dashboard to view
the table and obstacles, and **Check collisions at preview pose** for an
authoritative scene check. RViz display alone is not a collision test or physical
acceptance evidence.

Every offline inspector input must supply all six finite joint angles, including
each reference pose. Missing angles are rejected rather than inherited from a
previous pose. Malformed inspector responses leave validation unavailable.

## Physical assembly mapping — operator clarification, 2026-11-01

The geometry editor groups the arm into five physical assemblies. These are UI
measurement groups, not a change to the rigid-body chain:

| Physical assembly | Internal geometry | Motion / mounting |
| --- | --- | --- |
| Base (orange) | `base_link` | Stationary housing bolted to table; contains channel 5 servo and turntable mechanism |
| Base-to-shoulder connector (yellow) | `turntable_link` | Connector and rotating output plate turn with channel 5; support shoulder pivot |
| Upper arm (green) | `upper_arm_link` | Channel 4 shoulder rotates about Y |
| Forearm (blue) | `forearm_link` | Channel 3 elbow rotates about −Y with increasing pulse |
| Claw assembly (red) | `wrist_link`, `hand_link`, finger envelope | Channel 2 bends the whole assembly; channel 1 rotates claw about X; channel 0 opens/closes it |

Assume all servos are neutral when recording the reference geometry. The combined
wrist/claw assembly is approximately 7 inches long and contains two servos
(channels 1 and 0). This approximate overall length must not be repeated as the
length of each internal body. The housing before claw rotation and body after
claw rotation require separate geometry because they move differently. In schema
v2 the finger sweep is fixed to `hand_link`; `claw_link` does not need another box.
`tool0` is a gripping reference point, not an extra physical part.

Operator-confirmed pulse ranges: channel 4, 600–2100 µs; channel 3, 775–2500 µs;
channel 2, 500–2500 µs. Channel 2 rotates about +Y with increasing pulse width.
These notes do not overwrite saved pulse bounds or provide angular calibration.
Pulse direction is relative to the operator's stated axes; joint-frame orientation
must be established before applying it as a model axis. Earlier sketch notes give
7¼ inches for the distal measurement; retain that source separately from this
approximate 7-inch description until the endpoints and precise value are measured.


The operator identifies the entire distal wrist/claw mechanism as the **claw
assembly**, not a separate hand. Use that name in the Model UI. There is no
physical tool mount: the internal fixed `tool_mount` / `tool0` transform only
locates the gripping reference point. Its measurements belong inside the claw
assembly card, not in the list of servo pivots. Existing internal frame names
remain compatible with saved models and ROS planning.


### Colored reference clarification

The operator supplied `/home/pi1/DFRobotv2.jpg` and
`/home/pi1/DFRobotv2-marked.jpg`. The marked reference is available in the Model
measurement guide as `apps/dashboard/public/images/dfrobot-v2-components-marked.jpg`.
The turntable is part of the base mechanism, not another physical assembly card.
The yellow bracket between its output and the shoulder is significant and must
be measured. Its rigid geometry belongs to the existing `turntable_link`; its
pivot offset is `shoulder_joint.xyz`, measured from the base turntable origin in
the parent frame at neutral. This does not add a joint or infer an extra degree
of freedom. The stationary base and rotating connector need separate collision
geometry even though the turntable belongs mechanically to the base.

The photo is a component-identification reference, not a neutral-pose measurement.
The drawn rectangles overlap and are not collision volumes. No dimensions,
rotations, or pivot offsets are inferred from image pixels. Existing provisional
sketch-based offsets remain provisional until measured on the physical assembly.
