"""Guard integration with simulated current callbacks and in-memory PWM registers."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from movement_fixtures import geometry_data, movement_session
from test_base_servo_test import FakeBus
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.manual import JOINT_ORDER, ManualControl, mock_configuration
from workbench_arm_controller.movement import MovementRejected
from workbench_arm_controller.pca9685 import Pca9685Output
from workbench_arm_controller.servo_trial import SingleServoOutput, write_pulse
from workbench_common import Store, settings
from workbench_contracts import CommissionAcquire, CommissionCreate


def calibration():
    return {
        "calibrated": True,
        "channels": {
            name: {"channel": 5 - i, "min_us": 500, "max_us": 2500}
            for i, name in enumerate(JOINT_ORDER)
        },
    }


@pytest.fixture
def output_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workbench_arm_controller.device_lock.Path", lambda name: tmp_path / Path(name).name
    )
    outputs = []

    def create(kind, *, geometry=True):
        bus = FakeBus()
        bus.close = lambda: None
        sensor = {"fail": False, "checks": 0}

        def simulated_current_check():
            sensor["checks"] += 1
            if sensor["fail"]:
                raise RuntimeError("Simulated missing ammeter reading")

        kwargs = dict(
            bus_factory=lambda _: bus,
            check_current=simulated_current_check,
            movement=movement_session() if geometry else None,
        )
        output = (
            Pca9685Output(calibration(), **kwargs)
            if kind == "pose"
            else SingleServoOutput(**kwargs)
        )
        outputs.append(output)
        if kind == "pose":
            output.write_pose(dict.fromkeys(JOINT_ORDER, 1500))
        else:
            output.prepare_all()
        return output, bus, sensor

    yield create
    for output in outputs:
        output.close()


def move(output, kind, channel, pulse):
    if kind == "pose":
        targets = {name: output.movement.requested[5 - i] for i, name in enumerate(JOINT_ORDER)}
        targets[JOINT_ORDER[5 - channel]] = pulse
        output.write_pose(targets)
    else:
        output.write_joint(channel, pulse, 500, 2500)


@pytest.mark.parametrize("kind", ["pose", "trial"])
def test_collision_rejection_has_no_writes_and_preserves_holding(kind, output_factory):
    output, bus, _ = output_factory(kind)
    before = list(bus.writes)
    assumed = dict(output.movement.commanded)
    with pytest.raises(MovementRejected, match="clearance"):
        move(output, kind, 4, 600)
    assert bus.writes == before and output.movement.commanded == assumed
    assert not output.faulted and not output.movement.faulted
    move(output, kind, 5, 1600)  # A normal rejection does not latch a hardware fault.


@pytest.mark.parametrize("kind", ["pose", "trial"])
def test_quantized_success_updates_assumed_position(kind, output_factory):
    output, _, sensor = output_factory(kind)
    before_checks = sensor["checks"]
    move(output, kind, 4, 1600)
    encoded = output.movement.commanded[4]
    assert encoded != 1600
    angle = output.movement.model.joints[0].angle(encoded)
    assert output.movement.ranges[0] == (angle, angle)
    assert sensor["checks"] > before_checks
    move(output, kind, 4, 1500)
    assert output.movement.ranges[0][1] < angle  # No accumulation of earlier targets in v1.


@pytest.mark.parametrize("kind", ["pose", "trial"])
def test_missing_geometry_allows_startup_but_no_further_motion(kind, output_factory):
    output, bus, _ = output_factory(kind, geometry=False)
    before = list(bus.writes)
    with pytest.raises(MovementRejected, match="geometry_missing"):
        move(output, kind, 5, 1510)
    assert bus.writes == before


@pytest.mark.parametrize("kind", ["pose", "trial"])
def test_sensor_fault_during_preflight_attempts_shutdown_and_prevents_retry(kind, output_factory):
    output, bus, sensor = output_factory(kind)
    sensor["fail"] = True
    before = len(bus.writes)
    with pytest.raises(RuntimeError):
        move(output, kind, 4, 1600)
    assert output.movement.faulted and not output.movement.commanded
    assert bus.writes[before:] == [(0xFD, 0x10)]
    sensor["fail"] = False
    with pytest.raises(RuntimeError):
        move(output, kind, 4, 1500) if kind == "trial" else output.write_pose(
            dict.fromkeys(JOINT_ORDER, 1500)
        )


def test_trial_readback_failure_invalidates_assumed_position(output_factory):
    output, bus, _ = output_factory("trial")
    original = bus.read_byte_data
    bus.read_byte_data = lambda address, register: (
        0 if register == 0x18 else original(address, register)
    )
    with pytest.raises(ValueError, match="readback"):
        move(output, "trial", 4, 1510)
    assert output.faulted and output.movement.faulted and not output.movement.commanded
    assert bus.writes[-1] == (0xFD, 0x10)


@pytest.mark.parametrize("channel", range(6))
def test_legacy_raw_writer_cannot_bypass_guard(channel):
    bus = FakeBus()
    with pytest.raises(MovementRejected, match="guarded_all_holding"):
        write_pulse(bus, 0x40, 1510, channel)
    assert not bus.writes


@pytest.mark.parametrize("configured", [False, True])
def test_manual_free_play_ignores_geometry_but_preserves_current_interlock(
    tmp_path, monkeypatch, configured
):
    monkeypatch.setattr(
        "workbench_arm_controller.device_lock.Path", lambda name: tmp_path / Path(name).name
    )
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    data = geometry_data(domain="physical")
    data["clearance_mm"] = 80
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(data))
    cfg["arm"]["movement_geometry"] = str(path)
    cfg["manual"] = {**mock_configuration(), "bounds_context": {"fixture": True}}
    for joint in cfg["manual"]["channels"].values():
        joint["max_rate_us_per_second"] = 1000
    if not configured:
        cfg.pop("manual")
        cfg["arm"].pop("movement_geometry")
    monitor = ClawCurrentMonitor({**cfg, "mode": "mock"})
    bus = FakeBus()
    bus.close = lambda: None
    monkeypatch.setattr(
        "workbench_arm_controller.manual.Pca9685Output",
        lambda *a, **kw: Pca9685Output(*a, **kw, bus_factory=lambda _: bus),
    )
    clock = [0.0]
    control = ManualControl(cfg, monitor=monitor, clock=lambda: clock[0])
    try:
        control.prepare()
        control.acquire()
        targets, commanded = dict(control.targets), dict(control.commanded)
        writes = list(bus.writes)
        control.joint, control.speed = "joint_2", -1
        for step in range(3):
            clock[0] = (step + 1) * 0.02
            control.tick()
        assert control.token is not None and control.prepared and not control.recovery_required
        assert control.targets != targets and control.commanded != commanded
        assert bus.writes != writes
        assert "Free play" in control.status().message
        assert control.movement.last_result.reason == "manual_free_play_no_table_check"
        monitor.trip("Simulated sensor fault")
        control.tick()
        assert control.token is None and control.recovery_required
        assert bus.writes[-1] == (0xFD, 0x10)
    finally:
        control.close()


def test_commissioning_rejection_preserves_ready_session_and_assumed_ok_advances(
    tmp_path, monkeypatch
):
    from test_commissioning_live import ready

    monkeypatch.setattr(
        "workbench_arm_controller.device_lock.Path", lambda name: tmp_path / Path(name).name
    )
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(geometry_data(domain="physical")))
    cfg["arm"]["movement_geometry"] = str(path)
    monitor = ClawCurrentMonitor({**cfg, "mode": "mock"})
    bus = FakeBus()
    bus.close = lambda: None
    clock = [0.0]
    store = Store("movement-test", tmp_path)
    control = LiveCommissioning(
        cfg,
        monitor=monitor,
        clock=lambda: clock[0],
        output_factory=lambda *a, **kw: SingleServoOutput(*a, **kw, bus_factory=lambda _: bus),
    )
    control.attach(store)
    try:
        draft = control.create(CommissionCreate(assembly_id="Synthetic fixture", observer="Test"))
        control.acquire(
            CommissionAcquire(draft_id=draft.id, joint_id="joint_2", trial_type="all_holding")
        )
        ready(control)
        before, targets = list(bus.writes), dict(control.trial["targets"])
        token = control.token
        with pytest.raises(HTTPException) as error:
            control.apply_target(500, assume_ok=True, direct_limits=(500, 2500))
        assert error.value.status_code == 409
        assert control.token == token and control.trial["stage"] == "ready"
        assert control.trial["targets"] == targets and bus.writes == before
        assert not control.output.faulted
        result = control.apply_target(1600, assume_ok=True, direct_limits=(500, 2500))
        assert result.stage == "ready"
        angle = control.output.movement.model.joints[0].angle(result.encoded_us)
        assert control.output.movement.ranges[0] == (angle, angle)
    finally:
        control.close()
        store.close()
