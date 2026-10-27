"""Stop discovery and escalation tests use fake processes, never physical devices."""

import importlib.util
import signal
import sys
from pathlib import Path

import pytest


@pytest.fixture
def stop_module(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts/stop.py"
    spec = importlib.util.spec_from_file_location("workbench_stop", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stop(stop_module, monkeypatch):
    monkeypatch.setattr(stop_module, "shutdown_configs", lambda found: [])
    return stop_module


def test_discovers_orphans_and_children_without_other_checkouts(stop, monkeypatch):
    root = stop.ROOT
    processes = {
        10: stop.Process(10, 1, "1", ["uv", "run", "python", "scripts/dev.py"], root),
        11: stop.Process(11, 10, "2", ["sh", "-c", "vite"], root),
        12: stop.Process(12, 11, "3", ["node", "worker.js"], root),
        20: stop.Process(
            20,
            1,
            "4",
            [
                "python",
                "-m",
                "uvicorn",
                "workbench_arm_controller.app:create_app",
            ],
            root,
        ),
        30: stop.Process(
            30, 1, "5", ["node", str(root / "node_modules/.bin/vite")], root / "apps/dashboard"
        ),
        40: stop.Process(
            40,
            1,
            "6",
            ["python", "-m", "uvicorn", "workbench_arm_controller.app:create_app"],
            Path("/other"),
        ),
        50: stop.Process(50, 1, "7", ["python", "scripts/stop.py"], root),
        60: stop.Process(60, 1, "8", ["python", "-m", "uvicorn", "other.app:create_app"], root),
    }
    monkeypatch.setattr(stop, "processes", lambda: processes)
    assert set(stop.stack_processes()) == {10, 11, 12, 20, 30}


def test_reused_pid_is_never_signalled(stop, monkeypatch):
    original = stop.Process(10, 1, "1", [], stop.ROOT)
    replacement = stop.Process(10, 1, "2", [], stop.ROOT)
    monkeypatch.setattr(stop, "read_process", lambda pid: replacement)
    monkeypatch.setattr(stop.os, "kill", lambda *args: pytest.fail("Signalled a reused PID"))
    stop.send(original, signal.SIGTERM)


def test_orphan_shutdown_orders_coordinator_first_and_escalates(stop, monkeypatch):
    coordinator = stop.Process(10, 1, "1", ["workbench_coordinator.app:create_app"], stop.ROOT)
    arm = stop.Process(20, 1, "2", ["workbench_arm_controller.app:create_app"], stop.ROOT)
    live = {10: coordinator, 20: arm}
    signals = []
    monkeypatch.setattr(stop, "stack_processes", lambda: dict(live))
    monkeypatch.setattr(stop, "alive", lambda process: process.pid in live)
    monkeypatch.setattr(stop, "wait_for_exit", lambda *args: None)

    def send(process, signum):
        signals.append((process.pid, signum))
        if signum == signal.SIGKILL:
            live.pop(process.pid, None)

    monkeypatch.setattr(stop, "send", send)
    stop.main()
    assert signals[0] == (10, signal.SIGTERM)
    assert (20, signal.SIGTERM) in signals
    assert signals[-2:] == [(10, signal.SIGKILL), (20, signal.SIGKILL)]


def test_already_stopped_succeeds(stop, monkeypatch, capsys):
    monkeypatch.setattr(stop, "stack_processes", lambda: {})
    stop.main()
    assert "already stopped" in capsys.readouterr().out


def test_pwm_release_runs_after_processes_exit(stop, monkeypatch):
    process = stop.Process(20, 1, "2", [], stop.ROOT)
    live = {20: process}
    config = {"mode": "hardware", "arm": {"backend": "pca9685"}}
    monkeypatch.setattr(stop, "stack_processes", lambda: dict(live))
    monkeypatch.setattr(stop, "shutdown_configs", lambda found: [config])
    monkeypatch.setattr(stop, "alive", lambda p: p.pid in live)
    monkeypatch.setattr(stop, "send", lambda p, signum: live.pop(p.pid, None))
    monkeypatch.setattr(stop, "wait_for_exit", lambda *args: None)
    released = []

    def release(configs):
        assert not live
        released.extend(configs)

    monkeypatch.setattr(stop, "release_pwm", release)
    stop.main()
    assert released == [config]


def test_pwm_shutdown_failure_never_reports_success(stop, monkeypatch, capsys):
    monkeypatch.setattr(stop, "stack_processes", lambda: {})

    def fail(configs):
        raise OSError("Injected I2C failure")

    monkeypatch.setattr(stop, "release_pwm", fail)
    with pytest.raises(SystemExit, match="CUT PHYSICAL SERVO POWER"):
        stop.main()
    assert "already stopped" not in capsys.readouterr().out


def test_mock_process_config_never_selects_physical_outputs(stop_module, monkeypatch):
    process = stop_module.Process(
        20, 1, "2", ["workbench_arm_controller.app:create_app"], stop_module.ROOT
    )
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"WORKBENCH_MODE=mock\0")
    assert stop_module.shutdown_configs({20: process}) == []


def test_shutdown_uses_running_arm_bus_and_address(stop_module, monkeypatch, tmp_path):
    import json

    config = {
        "mode": "hardware",
        "arm": {
            "backend": "pca9685",
            "i2c_bus": 987,
            "i2c_address": 0x42,
        },
    }
    path = tmp_path / "hardware.json"
    path.write_text(json.dumps(config))
    env = f"WORKBENCH_MODE=hardware\0WORKBENCH_CONFIG={path}\0".encode()
    monkeypatch.setattr(Path, "read_bytes", lambda path: env)
    process = stop_module.Process(
        20, 1, "2", ["workbench_arm_controller.app:create_app"], stop_module.ROOT
    )
    assert stop_module.shutdown_configs({20: process}) == [config]


def test_native_ros_owners_are_scoped_to_this_checkout(stop):
    executable = (
        stop.ROOT
        / "ros/install/workbench_arm_controller/lib/workbench_arm_controller/arm_controller"
    )
    owner = stop.Process(70, 1, "10", ["python3", str(executable), "--ros-args"], stop.ROOT)
    assert stop.belongs_to_stack(owner)
    foreign = stop.Process(
        71, 1, "11", ["python3", "/other/ros/install/node/lib/node/run"], stop.ROOT
    )
    assert not stop.belongs_to_stack(foreign)
    launch = stop.Process(
        72, 1, "12", ["ros2", "launch", "workbench_bringup", "workbench.launch.py"], stop.ROOT
    )
    assert stop.belongs_to_stack(launch) and stop.is_ros_launch(launch)
