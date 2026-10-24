"""Stop this checkout's workbench stack, including orphaned services (Linux/Pi)."""

import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    f"workbench_{name}.app:create_app"
    for name in ("vision", "voice", "assistant", "arm_controller", "coordinator")
}


@dataclass
class Process:
    pid: int
    parent: int
    started: str
    args: list[str]
    cwd: Path


def read_process(pid):
    try:
        directory = Path(f"/proc/{pid}")
        stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return Process(
            pid,
            int(stat[1]),
            stat[19],
            (directory / "cmdline").read_bytes().decode().strip("\0").split("\0"),
            (directory / "cwd").resolve(strict=True),
        )
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def is_launcher(process):
    return any(
        arg == str(ROOT / "scripts/dev.py") or (arg == "scripts/dev.py" and process.cwd == ROOT)
        for arg in process.args
    )


def belongs_to_stack(process):
    if is_launcher(process):
        return True
    if process.cwd not in (ROOT, ROOT / "apps/dashboard"):
        return False
    args = process.args
    service = any(
        args[i : i + 2] == ["-m", "uvicorn"] and args[i + 2] in MODULES
        for i in range(len(args) - 2)
    )
    dashboard = any(
        arg in (str(ROOT / "node_modules/.bin/vite"), str(ROOT / "node_modules/vite/bin/vite.js"))
        for arg in args
    )
    wrapper = " ".join(args).startswith(("npm run dev", "npm start", "npm run start"))
    return service or dashboard or wrapper


def processes():
    return {
        int(path.name): process
        for path in Path("/proc").iterdir()
        if path.name.isdigit() and (process := read_process(int(path.name)))
    }


def stack_processes():
    snapshot = processes()
    found = {pid: p for pid, p in snapshot.items() if belongs_to_stack(p)}
    # Include shell/npm children, even when their command line has no project name.
    while True:
        children = {pid: p for pid, p in snapshot.items() if p.parent in found}
        if children.keys() <= found.keys():
            return found
        found.update(children)


def alive(process):
    current = read_process(process.pid)
    return current is not None and current.started == process.started


def send(process, signum):
    if alive(process):
        try:
            os.kill(process.pid, signum)
        except ProcessLookupError:
            pass


def wait_for_exit(group, seconds):
    deadline = time.monotonic() + seconds
    while any(alive(p) for p in group) and time.monotonic() < deadline:
        time.sleep(0.1)


def shutdown_configs(found):
    """Capture the actual arm configuration before terminating its process."""
    candidates = [p for p in found.values() if "workbench_arm_controller.app:create_app" in p.args]
    if not candidates:
        candidates = [p for p in found.values() if is_launcher(p)]
    environments = []
    for process in candidates:
        raw = Path(f"/proc/{process.pid}/environ").read_bytes()
        env = dict(item.decode().split("=", 1) for item in raw.split(b"\0") if b"=" in item)
        if "--mode" in process.args:
            env["WORKBENCH_MODE"] = process.args[process.args.index("--mode") + 1]
        environments.append(env)
    if not environments:
        environments = [os.environ]
    configs = {}
    for env in environments:
        mode = env.get("WORKBENCH_MODE", "hardware")
        if mode == "mock":
            continue
        local = ROOT / ".runtime/hardware/workbench-config.json"
        path = Path(
            env.get(
                "WORKBENCH_CONFIG", local if local.exists() else ROOT / "config/development/pi.json"
            )
        )
        config = json.loads(path.read_text())
        config["mode"] = env.get("WORKBENCH_MODE", config.get("mode", "hardware"))
        arm = config["arm"]
        if config["mode"] == "hardware" and arm.get("backend") == "pca9685":
            configs[(arm.get("i2c_bus", 1), arm.get("i2c_address", 0x40))] = config
    return list(configs.values())


def release_pwm(configs):
    from workbench_arm_controller.pwm_shutdown import shutdown_pwm

    for config in configs:
        shutdown_pwm(config)
        print("All arm PWM outputs verified full-off; arm released.", flush=True)


def main():
    if not Path("/proc/self/stat").exists():
        raise SystemExit("Workbench stop requires Linux /proc (including Raspberry Pi OS).")
    found = stack_processes()
    errors = []
    try:
        configs = shutdown_configs(found)
    except Exception as exc:
        configs = []
        errors.append(f"Could not determine PWM hardware configuration: {exc}")
    if not found:
        try:
            release_pwm(configs)
        except Exception as exc:
            errors.append(f"PWM shutdown failed: {exc}")
        if errors:
            raise SystemExit("; ".join(errors) + ". CUT PHYSICAL SERVO POWER.")
        print("Workbench is already stopped.")
        return
    print(f"Stopping {len(found)} workbench processes…", flush=True)
    launchers = [p for p in found.values() if is_launcher(p)]
    for process in launchers:
        send(process, signal.SIGTERM)
    wait_for_exit(list(found.values()), 20 if launchers else 0)
    # Orphans: stop the coordinator while arm monitoring is still running.
    coordinators = [p for p in found.values() if "workbench_coordinator.app:create_app" in p.args]
    for process in coordinators:
        send(process, signal.SIGTERM)
    wait_for_exit(coordinators, 5)
    found.update(stack_processes())
    for process in found.values():
        send(process, signal.SIGTERM)
    wait_for_exit(list(found.values()), 10)
    remaining = [p for p in found.values() if alive(p)]
    for process in remaining:
        print(f"Force stopping PID {process.pid}", flush=True)
        send(process, signal.SIGKILL)
    wait_for_exit(remaining, 2)
    if any(alive(p) for p in found.values()) or stack_processes():
        errors.append("Some workbench processes are still running")
    try:
        release_pwm(configs)
    except Exception as exc:
        errors.append(f"PWM shutdown failed: {exc}")
    if errors:
        raise SystemExit("; ".join(errors) + ". CUT PHYSICAL SERVO POWER.")
    print("Workbench stopped.")


if __name__ == "__main__":
    main()
