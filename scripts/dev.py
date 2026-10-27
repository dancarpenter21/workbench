"""Launch the local arm workbench; simulation requires --mode mock."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "vision": "workbench_vision",
    "voice": "workbench_voice",
    "assistant": "workbench_assistant",
    "arm-controller": "workbench_arm_controller",
    "coordinator": "workbench_coordinator",
}


def load_launch_config(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Configuration file not found: {path}\n"
            "No applications started. Check WORKBENCH_CONFIG or follow the first-run "
            "initialization steps in README.md#first-run-initialization. "
            "Create the local launch configuration explicitly, then complete the "
            "Initialization checklist; a config file alone does not establish commissioning."
        ) from exc


def resolve_launch_config(mode):
    default = ROOT / (
        "config/development/default.json" if mode == "mock" else "config/development/pi.json"
    )
    local = ROOT / ".runtime/hardware/workbench-config.json"
    override = os.environ.get("WORKBENCH_CONFIG")
    if override:
        path = Path(override)
        if path.resolve() != local.resolve() or path.exists():
            return path
        print(f"Local configuration missing: {path}; using {default}", flush=True)
        return default
    return local if mode == "hardware" and local.exists() else default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["mock", "hardware"], default="hardware")
    parser.add_argument("--transport", choices=["ros", "http"], default="ros")
    parser.add_argument("--dashboard-host", default="0.0.0.0")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=5173)
    args = parser.parse_args()
    config_path = str(resolve_launch_config(args.mode))
    env = {
        **os.environ,
        "WORKBENCH_ROOT": str(ROOT),
        "WORKBENCH_MODE": args.mode,
        "WORKBENCH_CONFIG": config_path,
        "WORKBENCH_STATE_DIR": os.environ.get(
            "WORKBENCH_STATE_DIR", str(ROOT / ".runtime" / args.mode)
        ),
    }
    cfg = load_launch_config(config_path)
    if args.transport == "ros" and not (ROOT / "ros/install/.workbench_ready").is_file():
        raise SystemExit("Build native ROS first; see ros/README.md. No applications started.")
    services = MODULES if args.transport == "http" else {"coordinator": "workbench_coordinator"}
    ports = [urlparse(cfg["services"][name]).port for name in services]
    env["WORKBENCH_GATEWAY_PORT"] = str(urlparse(cfg["services"]["coordinator"]).port)
    if not args.backend_only:
        ports.append(args.dashboard_port)
    if len(set(ports)) != len(ports):
        raise SystemExit("Service ports must be unique")
    for name, url in cfg["services"].items():
        if urlparse(url).hostname != "127.0.0.1":
            raise SystemExit(f"Local launcher requires 127.0.0.1 service addresses: {name}")
    for port in ports:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is already in use; no applications started")
    children = []
    runtime = Path(env["WORKBENCH_STATE_DIR"])
    runtime.mkdir(parents=True, exist_ok=True)
    logs = []

    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if args.transport == "ros":
            log = (runtime / "ros.log").open("a")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    [
                        "bash",
                        "-c",
                        'source "$1" && shift && exec ros2 launch workbench_bringup workbench.launch.py "$@"',
                        "workbench-ros",
                        str(ROOT / "scripts/ros-env.bash"),
                        "mode:=" + args.mode,
                        "config:=" + str(Path(config_path).resolve()),
                        "planning:=" + ("true" if args.mode == "mock" else "false"),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            )
        for name, module in MODULES.items() if args.transport == "http" else []:
            log = (runtime / f"{name}.log").open("a")
            logs.append(log)
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                f"{module}.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(urlparse(cfg["services"][name]).port),
            ]
            children.append(
                subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True
                )
            )
        pending = set(services)
        deadline = time.monotonic() + 180
        next_update = 0
        while pending:
            for name in list(pending):
                try:
                    with urllib.request.urlopen(
                        cfg["services"][name] + "/health", timeout=0.5
                    ) as response:
                        status = json.load(response)
                        if not status["ready"]:
                            print(f"{name}: not ready — {status['detail']}", flush=True)
                        pending.remove(name)
                except (OSError, ValueError):
                    pass
            if any(child.poll() is not None for child in children):
                raise RuntimeError(f"An application exited; inspect logs in {runtime}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"Startup timed out: {sorted(pending)}; inspect {runtime}")
            if pending and time.monotonic() > next_update:
                print(f"Waiting for {', '.join(sorted(pending))}…", flush=True)
                next_update = time.monotonic() + 10
            time.sleep(0.1)
        if not args.backend_only:
            env["WORKBENCH_COORDINATOR_URL"] = cfg["services"]["coordinator"]
            children.append(
                subprocess.Popen(
                    [
                        "npm",
                        "run",
                        "dev",
                        "--workspace",
                        "@workbench/dashboard",
                        "--",
                        "--port",
                        str(args.dashboard_port),
                        "--host",
                        args.dashboard_host,
                    ],
                    cwd=ROOT,
                    env=env,
                    start_new_session=True,
                )
            )
            dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
            deadline = time.monotonic() + 30
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError(f"An application exited; inspect logs in {runtime}")
                try:
                    with urllib.request.urlopen(dashboard_url, timeout=0.5):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Dashboard startup timed out: {dashboard_url}")
                    time.sleep(0.1)
        print(f"Workbench running in {args.mode} mode. Logs: {runtime}", flush=True)
        print("API: " + cfg["services"]["coordinator"] + "/docs", flush=True)
        if not args.backend_only:
            print(f"Dashboard: http://{args.dashboard_host}:{args.dashboard_port}", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.3)
        raise RuntimeError("An application exited unexpectedly")
    except KeyboardInterrupt:
        print("Stopping applications…", flush=True)
    finally:
        # npm/uv/test runners can forward the same shutdown signal more than once.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # Stop the coordinator before the arm and vision so it can request a hold.
        for child in reversed(children):
            if child.poll() is None:
                if args.transport == "ros" and child is children[0]:
                    child.send_signal(signal.SIGINT)
                else:
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
