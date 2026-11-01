"""Mock-only model/planning launch. No I2C adapter or physical output is loaded."""

import json
from pathlib import Path

import xacro
import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from workbench_common import ROOT
from workbench_common.model_geometry import model_srdf
from workbench_common.robot_model import load_model


def start(context):
    description = Path(get_package_share_directory("workbench_description"))
    config = Path(get_package_share_directory("workbench_moveit_config")) / "config"
    model = LaunchConfiguration("model").perform(context)
    measurements = load_model(model, mode="mock")
    robot = xacro.process_file(
        str(description / "urdf/workbench.urdf.xacro"),
        mappings={"model": model, "mock_control": "true"},
    ).toxml()

    def read(name):
        return yaml.safe_load((config / name).read_text())

    limits = {
        joint["name"]: {
            "has_velocity_limits": True,
            "max_velocity": float(joint["velocity"]),
            "has_acceleration_limits": True,
            # Synthetic demo only; physical acceleration requires separate measurement.
            "max_acceleration": 0.3,
        }
        for joint in measurements["joints"]
        if joint["type"] != "fixed"
    }
    parameters = {
        "robot_description": ParameterValue(robot, value_type=str),
        "robot_description_semantic": model_srdf(
            measurements, (config / "workbench.srdf").read_text()
        ),
        "robot_description_kinematics": read("kinematics.yaml"),
        "robot_description_planning": {"joint_limits": limits},
        "planning_pipelines": ["ompl"],
        "default_planning_pipeline": "ompl",
        "ompl": read("ompl_planning.yaml"),
        **read("moveit_controllers.yaml"),
        "publish_robot_description_semantic": True,
        "capabilities": "move_group/ExecuteTaskSolutionCapability",
    }
    nodes = [
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            arguments=["--frame-id", "world", "--child-frame-id", "base_link"],
        ),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": ParameterValue(robot, value_type=str)}],
            output="screen",
        ),
        Node(
            package="controller_manager",
            executable="ros2_control_node",
            parameters=[str(config / "ros2_controllers.yaml")],
            output="screen",
        ),
        Node(
            package="controller_manager",
            executable="spawner",
            arguments=[
                "joint_state_broadcaster",
                "arm_controller",
                "gripper_controller",
                "--controller-manager-timeout",
                "60",
                "--param-file",
                str(config / "ros2_controllers.yaml"),
            ],
            output="screen",
        ),
        Node(
            package="moveit_ros_move_group",
            executable="move_group",
            parameters=[parameters],
            output="screen",
        ),
        Node(
            package="workbench_planning",
            executable="retrieval",
            output="screen",
            parameters=[
                parameters,
                {
                    "mode": "mock",
                    "model_revision": measurements["revision"],
                    "scene_json": json.dumps(
                        measurements.get(
                            "scene",
                            json.loads((description / "config/simulated-scene.json").read_text()),
                        )
                    ),
                    "journal_directory": LaunchConfiguration("journal_directory").perform(context),
                },
            ],
        ),
    ]
    if LaunchConfiguration("rviz").perform(context).lower() == "true":
        nodes.append(
            Node(
                package="rviz2",
                executable="rviz2",
                parameters=[parameters],
                arguments=["-d", str(config / "workbench.rviz")],
            )
        )
    return nodes


def generate_launch_description():
    description = Path(get_package_share_directory("workbench_description"))
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "model", default_value=str(description / "config/simulated.json")
            ),
            DeclareLaunchArgument("rviz", default_value="true"),
            DeclareLaunchArgument(
                "journal_directory", default_value=str(ROOT / ".runtime/ros/mock/arm-journal")
            ),
            OpaqueFunction(function=start),
        ]
    )
