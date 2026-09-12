"""Launch the local applications, mock by default; stop all children on exit."""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["mock", "hardware"], default="mock")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=5173)
    args = parser.parse_args()
    env = {
        **os.environ,
        "WORKBENCH_ROOT": str(ROOT),
        "WORKBENCH_MODE": args.mode,
        "WORKBENCH_STATE_DIR": os.environ.get(
            "WORKBENCH_STATE_DIR", str(ROOT / ".runtime" / args.mode)
        ),
    }
    cfg = json.loads(
        Path(env.get("WORKBENCH_CONFIG", ROOT / "config/development/default.json")).read_text()
    )
    ports = [urlparse(cfg["services"][name]).port for name in MODULES]
    if not args.backend_only:
        ports.append(args.dashboard_port)
    if len(set(ports)) != len(ports):
        raise SystemExit("Service ports must be unique")
    for name, url in cfg["services"].items():
        if urlparse(url).hostname != "127.0.0.1":
            raise SystemExit(f"Local launcher requires 127.0.0.1 service addresses: {name}")
    for port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is already in use; no applications started")
    children = []
    runtime = Path(env["WORKBENCH_STATE_DIR"])
    runtime.mkdir(parents=True, exist_ok=True)
    logs = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for name, module in MODULES.items():
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
        pending = set(MODULES)
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
                    ],
                    cwd=ROOT,
                    env=env,
                    start_new_session=True,
                )
            )
        print(f"Workbench running in {args.mode} mode. Logs: {runtime}", flush=True)
        print("API: " + cfg["services"]["coordinator"] + "/docs", flush=True)
        if not args.backend_only:
            print(f"Dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.3)
        raise RuntimeError("An application exited unexpectedly")
    except KeyboardInterrupt:
        print("Stopping applications…", flush=True)
    finally:
        # Stop the coordinator before the arm and vision so it can request a hold.
        for child in reversed(children):
            if child.poll() is None:
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
