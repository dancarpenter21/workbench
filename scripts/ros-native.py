"""Reproducible, unprivileged ROS source import/build; never starts hardware nodes."""

import argparse
import fcntl
import os
import subprocess
import xml.etree.ElementTree as xml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / ".runtime/ros/underlay"
CORE = [
    "workbench_bringup",
    "workbench_inference",
    "workbench_vision",
    "workbench_coordinator",
    "workbench_arm_controller",
    "ros2run",
    "ros2launch",
    "rmw_cyclonedds_cpp",
]
PLANNING = [
    "workbench_moveit_config",
    "moveit_task_constructor_core",
    "moveit_task_constructor_visualization",
    "moveit_task_constructor_capabilities",
]
UNUSED_MIDDLEWARE = [
    "rmw_connextdds",
    "rmw_fastrtps_cpp",
    "rmw_fastrtps_dynamic_cpp",
    "rmw_zenoh_cpp",
    "zenoh_cpp_vendor",
    # CycloneDDS 11 uses its own transport; rmw_cyclonedds only links Iceoryx on <0.11.
    "iceoryx_binding_c",
    "iceoryx_hoofs",
    "iceoryx_posh",
]


def apply_vendor_patches():
    for repository, patch in [
        ("planning/moveit", "moveit-rviz-qt.patch"),
    ]:
        command = [
            "git",
            "-C",
            str(NATIVE / "src" / repository),
            "apply",
        ]
        path = str(ROOT / "ros/patches" / patch)
        applied = subprocess.run(
            [*command, "--reverse", "--check", path], capture_output=True, check=False
        )
        if applied.returncode == 0:
            continue
        subprocess.run([*command, "--check", path], check=True)
        subprocess.run([*command, path], check=True)


def run(command, *, capture=False):
    # Positional shell arguments preserve paths exactly; no data is interpolated as code.
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" && shift && exec "$@"',
            "workbench-ros",
            str(ROOT / "scripts/ros-env.bash"),
            *map(str, command),
        ],
        cwd=ROOT,
        env={**os.environ, "CMAKE_BUILD_PARALLEL_LEVEL": "2"},
        check=False,
        capture_output=capture,
        text=True,
    )
    return result if capture else result.returncode


def build_packages(workspace, sources, selection):
    (workspace / "build").mkdir(parents=True, exist_ok=True)
    with (workspace / "build/.workbench.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Waiting for the build already running in {workspace}", flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        return run(
            [
                "colcon",
                "--log-base",
                ROOT / ".runtime/ros/log",
                "build",
                "--base-paths",
                *sources,
                "--build-base",
                workspace / "build",
                "--install-base",
                workspace / "install",
                *selection,
                "--parallel-workers",
                "2",
                "--cmake-args",
                "--no-warn-unused-cli",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_TESTING=OFF",
                "-DTRACETOOLS_DISABLED=ON",
                f"-DPython3_EXECUTABLE={ROOT / '.venv/bin/python'}",
                "-DOMPL_BUILD_DEMOS=OFF",
                "-DOMPL_BUILD_TESTS=OFF",
                "-DOMPL_BUILD_PYBINDINGS=OFF",
            ]
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--manifest", type=Path, default=ROOT / "ros/sources.lock.repos")
    build = commands.add_parser("build")
    build.add_argument("--component", choices=("core", "planning"), default="core")
    build.add_argument(
        "--resume",
        action="store_true",
        help="Skip successful vendor packages; use only with unchanged sources and build options",
    )
    build.add_argument(
        "--apps-only",
        action="store_true",
        help="Build project packages in the ros/ overlay against the existing native install",
    )
    args = parser.parse_args()
    if args.command == "fetch":
        if not args.manifest.is_file():
            parser.error(f"Missing source lockfile: {args.manifest}")
        (NATIVE / "src").mkdir(parents=True, exist_ok=True)
        return run(
            [
                "vcs",
                "import",
                "--shallow",
                "--recursive",
                "--workers",
                "4",
                "--retry",
                "1",
                "--input",
                args.manifest,
                NATIVE / "src",
            ]
        )
    roots = CORE + (PLANNING if args.component == "planning" else [])
    own = {
        xml.parse(path).getroot().findtext("name")
        for path in (ROOT / "ros/src").glob("*/package.xml")
    }
    if not args.apps_only:
        if args.component == "planning":
            apply_vendor_patches()
        selected = run(
            [
                "colcon",
                "list",
                "--base-paths",
                NATIVE / "src",
                ROOT / "ros/src",
                "--packages-up-to",
                *roots,
                "--packages-ignore",
                *UNUSED_MIDDLEWARE,
                "--names-only",
            ],
            capture=True,
        )
        if selected.returncode:
            print(selected.stderr)
            return selected.returncode
        vendors = [name for name in selected.stdout.splitlines() if name not in own]
        if not vendors:
            raise SystemExit(
                "Native source dependencies are missing; fetch the locked repositories first"
            )
        result = build_packages(
            NATIVE,
            [NATIVE / "src"],
            [
                "--packages-select",
                *vendors,
                "--packages-ignore",
                *UNUSED_MIDDLEWARE,
                *(["--packages-skip-build-finished"] if args.resume else []),
            ],
        )
        if result:
            return result
    workspace = ROOT / "ros"
    result = build_packages(
        workspace,
        [workspace / "src"],
        ["--packages-up-to", *(name for name in roots if name in own)],
    )
    if result == 0:
        (workspace / "install/.workbench_ready").write_text(
            "Native project overlay built successfully\n"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
