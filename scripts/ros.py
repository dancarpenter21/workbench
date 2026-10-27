"""Inspect the native ROS prerequisites and validate robot models without hardware I/O."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from workbench_common.robot_model import load_model

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = ROOT / "ros/src/workbench_description"
TOOLS = ROOT / "ros/tooling/.venv/bin"
SYSTEM_PACKAGES = (
    "build-essential",
    "cmake",
    "git",
    "pkg-config",
    "python3-dev",
    "libasio-dev",
    "libtinyxml2-dev",
    "libssl-dev",
    "libyaml-dev",
    "libeigen3-dev",
    "libbullet-dev",
    "libfcl-dev",
    "libassimp-dev",
    "liborocos-kdl-dev",
    "liburdfdom-dev",
    "libconsole-bridge-dev",
    "libboost-all-dev",
    "libfmt-dev",
    "libspdlog-dev",
    "libtinyxml-dev",
    "libgl1-mesa-dev",
    "libglu1-mesa-dev",
    "qtbase5-dev",
    "libqt5svg5-dev",
    "libogre-1.12-dev",
)


def installed(package):
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "install ok installed"


def doctor():
    missing = [p for p in SYSTEM_PACKAGES if not installed(p)]
    python_missing = [
        name for name in ("colcon", "rosdep", "vcs", "xacro") if not (TOOLS / name).is_file()
    ]
    result = {
        "os": platform.freedesktop_os_release().get("PRETTY_NAME"),
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "python_base": sys.base_prefix,
        "missing_system_packages": missing,
        "missing_python_tools": python_missing,
        "ros_distro": os.environ.get("ROS_DISTRO"),
        "ros2": shutil.which("ros2"),
        "build_prerequisites_ready": not missing and not python_missing,
        "physical_retrieval_qualified": False,
    }
    print(json.dumps(result, indent=2))
    if missing:
        print("\nOperator installation command (this tool never invokes sudo):")
        print("sudo apt-get update && sudo apt-get install -y " + " ".join(missing))
    if python_missing:
        print("\nInstall Python build tools:")
        print("uv sync --locked --project ros/tooling --python /usr/bin/python3")
    return 0 if result["build_prerequisites_ready"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    validate = commands.add_parser("validate-model")
    validate.add_argument("model", type=Path)
    validate.add_argument("--mode", choices=("mock", "hardware"), required=True)
    render = commands.add_parser("render-model")
    render.add_argument("model", type=Path)
    render.add_argument("--mode", choices=("mock", "hardware"), required=True)
    render.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    try:
        data = load_model(args.model, mode=args.mode)
        if args.command == "render-model":
            result = subprocess.run(
                [
                    str(TOOLS / "xacro"),
                    str(DESCRIPTION / "urdf/workbench.urdf.xacro"),
                    f"model:={args.model.resolve()}",
                    "mock_control:=false",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            # Refuse accidental replacement of a calibrated description.
            with args.output.open("x") as output:
                output.write(result.stdout)
        print(
            json.dumps(
                {
                    "domain": data["domain"],
                    "revision": data["revision"],
                    "model_structurally_valid": True,
                    "physical_retrieval_qualified": False,
                },
                indent=2,
            )
        )
    except (ValueError, OSError, ImportError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
