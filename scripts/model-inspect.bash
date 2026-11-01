#!/usr/bin/env bash
# Offline geometry process only. No ROS nodes or hardware interfaces are started.
set -e
source "$(dirname -- "${BASH_SOURCE[0]}")/ros-env.bash"
exec "$WORKBENCH_ROOT/ros/install/workbench_planning/lib/workbench_planning/model_validator"
