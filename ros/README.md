# ROS migration workspace

This directory contains the native ROS backend used by the application's launcher.
Physical automatic retrieval remains disabled.

Implemented components:

- `workbench_description`: parameterized Xacro, a deliberately synthetic model,
  and an incomplete physical-measurement template.
- `workbench_interfaces`: typed retrieval/inference actions, observations,
  current telemetry, execution evidence, and explicit recovery interfaces.
- `workbench_inference`: speech and intent action servers sharing the existing
  engines, with cancellation and diagnostic publication.
- `workbench_vision`: lifecycle-owned camera acquisition and observation/image topics.
- `workbench_coordinator`: durable request orchestration, cancellation, recovery,
  observation freshness checks, and completion evidence.
- `workbench_arm_controller`: lifecycle-owned guarded commissioning, continuous
  claw-current monitoring, typed controls, and exclusive retrieval admission.
- Browser gateway: existing HTTP/WebSocket API backed by ROS actions, services,
  and telemetry. No internal HTTP connection is needed between ROS services.
- `workbench_planning`: mock-only Task Constructor executor with a durable journal.
- `workbench_bringup`: combined backend launch and one-time lifecycle activation.
- `workbench_moveit_config`: a mock-controller MoveIt/RViz launch configuration.
  Headless mock retrieval passes through MoveIt and ros2_control. Interactive RViz
  and physical execution have separate validation requirements.

Still required: monitored physical ros2_control integration,
measured scene/calibration, open-loop feasibility trials, and physical acceptance.
The existing capsules and hardware protections have not been removed during this transition.

## Native Debian tooling

Keep the project environment on the system Python ABI to preserve Picamera2:

```sh
uv sync --locked
uv sync --locked --project ros/tooling --python /usr/bin/python3
uv run --no-sync python scripts/ros.py doctor
```

The tooling environment has its own lockfile so ROS build tooling does not
re-resolve or rebuild inference dependencies. `scripts/ros-env.bash` combines it
with the project environment and native ROS install. It restricts discovery to
localhost, uses domain 42 by default, and selects CycloneDDS. Source it in a fresh
bash shell; it starts no nodes or hardware:

```sh
source scripts/ros-env.bash
```

`doctor` checks initial build prerequisites, not every transitive ROS dependency.
Subsequent rosdep/build diagnostics can identify additional Debian packages.
It never runs sudo. Native sources, builds, and logs live under `.runtime/ros/`.
`scripts/ros-native.py fetch` imports the exact commits in `ros/sources.lock.repos`.
`scripts/ros-native.py build --component core` builds ROS and the backend owners;
`--component planning` additionally builds MoveIt and Task Constructor. Builds
use two package workers and two compiler jobs per package to limit Pi memory use.
Unused RMW implementations and the obsolete Iceoryx transport are excluded.
Optional LTTng tracing is disabled; ROS diagnostics and application logging remain
available. The locked tooling supplies pybind11 headers and CMake metadata.
Vendor packages install under `.runtime/ros/underlay/install`; project packages
install separately under `ros/install`. Use `--apps-only` to rebuild only project
packages against a completed native underlay. Builds of the same workspace serialize.
For an interrupted build with unchanged sources and build options, `--resume`
skips successful vendor packages while rebuilding failed/unfinished packages and
the project overlay. Omit it after changing the source lock or build configuration.
Source import includes the submodules pinned by each upstream repository.

The tracked patch in `ros/patches/moveit-rviz-qt.patch` selects the Qt major version
actually exported by RViz rather than inferring it from RViz's version number.
This supports the upstream RViz Qt5 fallback on this native Debian installation.
The build helper checks/applies it idempotently and refuses mismatched patch context.

When Debian development dependencies are unavailable system-wide,
`scripts/ros-system-prefix.py` can extract authenticated Debian archives into an
ignored local prefix. It verifies archive sizes and SHA256 hashes, runs no package
maintainer scripts, and leaves the system package database unchanged. The ROS
environment includes this prefix when present.

After a successful core build, run the hardware-free ROS smoke test:

```sh
source scripts/ros-env.bash
ROS_DOMAIN_ID=43 uv run --no-sync python scripts/ros-smoke.py
ROS_DOMAIN_ID=43 uv run --no-sync python scripts/ros-workflow-smoke.py
ROS_DOMAIN_ID=44 uv run --no-sync python scripts/ros-control-smoke.py
ROS_DOMAIN_ID=45 uv run --no-sync python scripts/ros-bringup-smoke.py
ROS_DOMAIN_ID=46 uv run --no-sync python scripts/ros-bringup-smoke.py --planning
```

The smoke test uses real ROS actions/topics with simulated speech, intent, and
observations. It tests camera lifecycle activation/deactivation without opening
the physical camera. It does not exercise arm output or claim physical acceptance.
The workflow test uses a fake retrieval executor. The control test uses simulated
PCA9685/INA219 I/O and checks lifecycle, typed request rejection, exclusive
ownership, raw current evidence, and fail-closed behavior after monitor failure.

The combined backend launch passes the mock bringup smoke test. `npm run dev`
starts it along with the dashboard:

```sh
ros2 launch workbench_bringup workbench.launch.py mode:=mock
ros2 launch workbench_bringup workbench.launch.py mode:=mock planning:=true
```

The gateway binds localhost port 8100; launch Vite separately for the dashboard.
This applies to a direct `ros2 launch`; the normal npm launcher starts Vite itself.
Bringup requires an explicit mode and does not restart failed owners or retry
failed lifecycle transitions. Physical planning execution is rejected.

## Models

Initialization's **ROS robot model** editor saves assembly-scoped drafts through
typed arm-owner services. Unknown dimensions remain blank. Saving detects stale
revisions and rejects malformed or mixed-domain drafts. **Validate complete model**
checks structural completeness; it does not activate physical output. Export the
saved JSON and use the commands below to validate/render it. Existing supervised
capsule records remain under their separate section and are never converted into
invented link dimensions or joint calibration.

```sh
uv run --no-sync python scripts/ros.py validate-model \
  ros/src/workbench_description/config/simulated.json --mode mock
uv run --no-sync python scripts/ros.py render-model \
  ros/src/workbench_description/config/simulated.json --mode mock \
  --output .runtime/workbench-demo.urdf
```

Rendering refuses to overwrite an existing output. Validation rejects synthetic
models in hardware mode and incomplete physical measurements. Coordinates are
link-local metres and rotations are radians, unlike the previous table-frame
millimetre capsules. Do not copy the synthetic dimensions into calibration.

The demo is a kinematic model with mock joint state, not a dynamics simulation.
Its claw is a simplified synthetic envelope, not a measured finger mechanism.
Position-only IK is exploratory: it does not qualify a grasp orientation for
this five-axis arm. Physical model validation alone never authorizes execution.

## ROS component interfaces

| Component | ROS interface |
| --- | --- |
| Voice | `/workbench/voice/transcribe`, `workbench_interfaces/action/Transcribe` |
| Assistant | `/workbench/assistant/interpret`, `workbench_interfaces/action/Interpret` |
| Vision | `/workbench/observations`, `workbench_interfaces/msg/Observations` |
| Camera | `/camera/image_raw`, `/camera/camera_info` |
| Mock scene | `/workbench/mock/scene`, `workbench_interfaces/srv/SetMockScene` |
| Requests | `/workbench/requests`, `workbench_interfaces/action/RequestTool` |
| Arm owner | `/workbench/arm/retrieve`, `/workbench/arm/readiness`, `/workbench/arm/telemetry` |
| Guarded controls | `/workbench/arm/control/*`, generated typed services |
| Internal planner | `/workbench/planning/retrieve`, `/workbench/planning/readiness` |
| Recovery | `/workbench/recover`, `/workbench/arm/recover` |
| Health | `/diagnostics`, `diagnostic_msgs/msg/DiagnosticArray` |

Vision starts unconfigured. Configuration reads settings; activation opens the
camera in hardware mode. Deactivation closes it. CameraInfo remains explicitly
uncalibrated until an intrinsic calibration is integrated; no camera-to-table TF
or metric tool pose is fabricated. Frame timestamps remain acquisition timestamps.

Inference uses the existing `WORKBENCH_MODE`, `WORKBENCH_CONFIG`, and calibration
settings. Select mock configuration explicitly for tests. Accepted cancellation
does not forcibly terminate a native model call: the model stays locked until
its worker exits. No inference result directly commands the arm.

All physical claw paths must retain continuous INA219 monitoring, verified-scale
absolute-current limits, fault shutdown, physical power-cutoff instructions, and
the prohibition on automatic motion retry. ROS mock state is not physical feedback.
