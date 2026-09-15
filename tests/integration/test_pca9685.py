import json
import sys
from uuid import uuid4

import httpx
import pytest
from workbench_arm_controller.app import create_app
from workbench_arm_controller.pca9685 import Pca9685Output, PulsePlan, main
from workbench_common import Store, settings


def calibration():
    # Synthetic bench fixture, never a recommended physical calibration.
    return {
        "calibrated": True,
        "channels": {
            f"joint_{i}": {"channel": i, "min_us": 1101, "max_us": 1901} for i in range(6)
        },
    }


def pose():
    return {f"joint_{i}": 1500 for i in range(6)}


class FakeBus:
    def __init__(self, number):
        self.number = number
        self.writes = []
        self.closed = False
        self.fail_channel = None

    def write_byte_data(self, address, register, value):
        self.writes.append((address, register, [value]))

    def write_i2c_block_data(self, address, register, values):
        self.writes.append((address, register, values))
        if register == self.fail_channel:
            raise OSError("injected bus failure")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("edge", [1101, 1901])
def test_quantization_stays_inside_measured_limits(edge):
    plan = PulsePlan(calibration())
    assert plan.prescale == 121
    commands = plan.encode({joint: edge for joint in pose()})
    assert [c.channel for c in commands] == list(range(6))
    assert all(1101 <= c.commanded_us <= 1901 for c in commands)
    assert all(abs(c.commanded_us - edge) < plan.tick_us for c in commands)


@pytest.mark.parametrize(
    "change", ["disabled", "duplicate", "missing", "nan", "outside", "boolean"]
)
def test_bad_calibration_never_opens_bus(change):
    cfg = calibration()
    if change == "disabled":
        cfg["calibrated"] = False
    elif change == "duplicate":
        cfg["channels"]["joint_5"]["channel"] = 0
    elif change == "missing":
        del cfg["channels"]["joint_5"]
    elif change == "nan":
        cfg["channels"]["joint_5"]["min_us"] = float("nan")
    elif change == "outside":
        cfg["channels"]["joint_5"]["max_us"] = 2501
    else:
        cfg["channels"]["joint_5"]["channel"] = True
    with pytest.raises(ValueError):
        Pca9685Output(cfg, bus_factory=lambda _: pytest.fail("Opened hardware"))


def test_output_initializes_off_and_only_reports_sent_pulses():
    output = Pca9685Output(calibration(), bus_factory=FakeBus)
    assert output.bus.writes[0] == (0x40, 0xFD, [0x10])
    assert not any(0x06 <= register <= 0x45 for _, register, _ in output.bus.writes)
    commands = output.write_pose(pose())
    assert output.commanded == commands
    for command, (_, register, data) in zip(commands, output.bus.writes[-6:], strict=True):
        assert register == 0x06 + command.channel * 4
        assert data[:2] == [0, 0]
        assert data[2] | data[3] << 8 == command.ticks
    output.close()
    assert output.bus.writes[-1] == (0x40, 0xFD, [0x10])
    assert output.bus.closed
    with pytest.raises(RuntimeError, match="closed"):
        output.write_pose(pose())


@pytest.mark.parametrize("bad", [0, 2500, float("nan"), float("inf"), True])
def test_last_invalid_target_prevents_all_pose_writes(bad):
    output = Pca9685Output(calibration(), bus_factory=FakeBus)
    before = list(output.bus.writes)
    invalid = {**pose(), "joint_5": bad}
    with pytest.raises(ValueError, match="limits"):
        output.write_pose(invalid)
    assert output.bus.writes == before
    output.close()


def test_partial_write_fault_invalidates_command_cache_and_never_retries():
    output = Pca9685Output(calibration(), bus_factory=FakeBus)
    output.write_pose(pose())
    output.bus.fail_channel = 0x06 + 4 * 2
    with pytest.raises(RuntimeError, match="unknown"):
        output.write_pose({joint: 1600 for joint in pose()})
    count = len(output.bus.writes)
    assert output.commanded is None
    with pytest.raises(RuntimeError, match="faulted"):
        output.write_pose(pose())
    assert len(output.bus.writes) == count
    output.close()


def test_initialization_failure_closes_bus():
    bus = FakeBus(1)

    def fail(*args):
        raise OSError("unavailable")

    bus.write_byte_data = fail
    with pytest.raises(OSError):
        Pca9685Output(calibration(), bus_factory=lambda _: bus)
    assert bus.closed


def test_cli_is_offline_and_labels_output_as_commands(tmp_path, monkeypatch, capsys):
    cfg, target = tmp_path / "calibration.json", tmp_path / "pose.json"
    cfg.write_text(json.dumps(calibration()))
    target.write_text(json.dumps(pose()))
    monkeypatch.setitem(sys.modules, "smbus2", None)
    monkeypatch.setattr(sys, "argv", ["pwm", "--calibration", str(cfg), "--pose", str(target)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run"
    assert result["measured_feedback"] is False
    assert len(result["commands"]) == 6


@pytest.mark.parametrize("backend", ["pca9685", "misspelled"])
async def test_unsupported_hardware_is_unready_and_cannot_move_or_recover(
    backend, tmp_path, monkeypatch
):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = backend
    # Neither an existing calibration flag nor offline record/rehearsal results
    # can remove the separate PCA9685 application gate.
    cfg["workbench"]["arm"]["calibrated"] = True
    monkeypatch.setitem(sys.modules, "smbus2", None)
    monkeypatch.setattr(
        "workbench_arm_controller.app.SerialArm",
        lambda _: pytest.fail("Unsupported backend opened serial hardware"),
    )
    store = Store("arm", tmp_path)
    # A previously completed operation cannot make this backend appear ready.
    store.put("status", {"state": "completed", "message": "Old session"})
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            health = (await client.get("/health")).json()
            assert not health["ready"]
            status = (await client.get("/status")).json()
            assert status["state"] == "fault" and status["recovery_required"]
            assert (
                await client.post(
                    "/execute",
                    json={
                        "operation_id": str(uuid4()),
                        "tool_id": "pliers",
                    },
                )
            ).status_code == 503
            assert (await client.post("/recover", json={"acknowledged": True})).status_code == 503
            stopped = (await client.post("/stop")).json()
            assert "Simulated" not in stopped["message"]
    store.close()


async def test_pi_profile_can_still_run_mock_without_device_libraries(tmp_path, monkeypatch):
    cfg = settings()
    cfg["mode"] = "mock"
    cfg["arm"]["backend"] = "pca9685"
    monkeypatch.setitem(sys.modules, "smbus2", None)
    store = Store("arm", tmp_path)
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).json()["ready"]
            assert (await client.get("/status")).json()["state"] == "idle"
    store.close()
