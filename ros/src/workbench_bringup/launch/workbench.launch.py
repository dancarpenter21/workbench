"""Native ROS owners and browser gateway; mode must be selected explicitly."""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
    SetEnvironmentVariable,
)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from workbench_common import ROOT


def start(context):
    mode = LaunchConfiguration("mode").perform(context)
    if mode not in ("mock", "hardware"):
        raise ValueError("Select mode:=mock or mode:=hardware explicitly")
    planning = LaunchConfiguration("planning").perform(context)
    if planning not in ("true", "false"):
        raise ValueError("planning must be true or false")
    if mode == "hardware" and planning == "true":
        raise ValueError("Physical planning execution is not commissioned")
    config = LaunchConfiguration("config").perform(context)
    if not config:
        local = ROOT / ".runtime/hardware/workbench-config.json"
        config = str(
            local
            if mode == "hardware" and local.is_file()
            else ROOT / "config/development" / ("default.json" if mode == "mock" else "pi.json")
        )
    if not Path(config).is_file():
        raise ValueError(f"Missing workbench config: {config}")
    actions = [
        SetEnvironmentVariable("WORKBENCH_MODE", mode),
        SetEnvironmentVariable("WORKBENCH_CONFIG", str(Path(config).resolve())),
    ]
    nodes = [
        Node(package="workbench_inference", executable="voice", output="screen"),
        Node(package="workbench_inference", executable="assistant", output="screen"),
        Node(package="workbench_vision", executable="vision", output="screen"),
        Node(package="workbench_arm_controller", executable="arm_controller", output="screen"),
        Node(package="workbench_coordinator", executable="coordinator", output="screen"),
        Node(package="workbench_bringup", executable="browser_gateway", output="screen"),
        Node(package="workbench_bringup", executable="lifecycle_manager", output="screen"),
    ]
    for node in nodes:
        actions.append(
            RegisterEventHandler(
                OnProcessExit(
                    target_action=node,
                    on_exit=[EmitEvent(event=Shutdown(reason="A workbench owner exited"))],
                )
            )
        )
    actions.extend(nodes)
    if planning == "true":
        demo = (
            Path(get_package_share_directory("workbench_moveit_config")) / "launch/demo.launch.py"
        )
        actions.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(demo)),
                launch_arguments={
                    "rviz": LaunchConfiguration("rviz"),
                    "journal_directory": str(
                        Path(os.environ.get("WORKBENCH_STATE_DIR", ROOT / ".runtime/ros/mock"))
                        / "planning-journal"
                    ),
                }.items(),
            )
        )
    return actions


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("mode", description="Required: mock or hardware"),
            DeclareLaunchArgument("config", default_value=""),
            DeclareLaunchArgument("planning", default_value="false"),
            DeclareLaunchArgument("rviz", default_value="false"),
            OpaqueFunction(function=start),
        ]
    )
