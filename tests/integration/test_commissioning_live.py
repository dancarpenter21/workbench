"""Commissioning tests use mock mode and fake buses only."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_base_servo_test import FakeBus
from test_claw_servo_test import SensorBus
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.device_lock import DeviceLock
from workbench_arm_controller.servo_trial import SingleServoOutput
from workbench_common import Store, settings
from workbench_contracts import (
    CommissionAcquire,
    CommissionAttemptSelect,
    CommissionCommand,
    CommissionCreate,
    CommissionEdit,
    CommissionFeedback,
    CommissionFinish,
    CommissionMark,
    CommissionPrepare,
    CommissionReady,
    CommissionStep,
)


@pytest.fixture
def trial(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    clock = [0.0]
    store = Store("commissioning-test", tmp_path)
    control = LiveCommissioning(cfg, clock=lambda: clock[0])
    control.attach(store)
    draft = control.create(CommissionCreate(assembly_id="Factory arm", observer="Operator"))
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base"))
    yield control, clock, store
    control.close()
    store.close()


def command(control, model=CommissionCommand, **kwargs):
    control.monitor.poll()
    return model(token=control.token or "expired", sequence=control.sequence + 1, **kwargs)


def ready(control):
    control.prepare(command(control, CommissionPrepare, power_off_supported_isolated=True))
    control.ready(command(control, CommissionReady, settled_and_clear=True))


def feedback(control, **overrides):
    pending = control.trial["pending_command"]
    values = dict(
        command_id=pending["command_id"],
        joint_id=pending["joint_id"],
        expected_requested_us=pending["requested_us"],
        outcome="settled_clear",
        observed_direction="positive",
        physical_frame="Clockwise viewed from above",
    )
    values.update(overrides)
    return control.feedback(command(control, CommissionFeedback, **values))


def mark(control, kind):
    control.mark(
        command(
            control,
            CommissionMark,
            kind=kind,
            expected_requested_us=control.trial["requested_us"],
            settled_and_clear=True,
        )
    )


def finish(control, reviewed=False):
    control.stop()
    control.finish(
        CommissionFinish(
            trial_id=control.trial["trial_id"],
            power_off_and_supported=True,
            observations_reviewed=reviewed,
        )
    )


def test_observed_record_and_disabled_export(trial):
    control, clock, _ = trial
    draft_id = control.trial["draft_id"]
    ready(control)
    mark(control, "initial")
    control.step(command(control, CommissionStep, direction=-1))
    feedback(control)
    mark(control, "minimum")
    clock[0] += 0.26
    control.step(command(control, CommissionStep, direction=1))
    feedback(control)
    clock[0] += 0.26
    control.step(command(control, CommissionStep, direction=1))
    feedback(control)
    mark(control, "maximum")
    draft = control.read(draft_id)
    control.edit(
        draft_id,
        CommissionEdit(
            revision=draft.revision,
            joint_id="base",
            direction="Clockwise as viewed from above",
            notes="Factory assembled; supported",
        ),
    )
    finish(control, reviewed=True)
    draft = control.read(draft_id)
    control.select_attempt(
        draft_id,
        CommissionAttemptSelect(
            revision=draft.revision, joint_id="base", attempt_id=draft.joints[0].attempts[-1].id
        ),
    )
    export = control.export(draft_id)
    assert export.record.joints[0].state == "observed"
    assert export.record.domain == "simulated"
    assert export.record.joints[0].attempts[-1].observations["minimum"].requested_us == 1490
    assert export.record.joints[-1].observations == {}
    actions = [e.action for e in export.record.history]
    assert actions.index("prepare_intent") < actions.index("prepare_result")
    assert actions.index("step_intent") < actions.index("step_result")
    assert not control.reserved
    edited = control.edit(
        draft_id,
        CommissionEdit(
            revision=export.record.revision,
            joint_id="base",
            direction="Correction",
            notes="Review again",
        ),
    )
    assert edited.joints[0].state == "needs_review"


def test_readiness_replay_bounds_rate_and_marks(trial):
    control, clock, _ = trial
    with pytest.raises(HTTPException):
        control.step(command(control, CommissionStep, direction=1))
    ready(control)
    value = command(control, CommissionStep, direction=1)
    control.step(value)
    with pytest.raises(HTTPException):
        control.step(value)
    assert control.trial["requested_us"] == 1510
    feedback(control)
    with pytest.raises(HTTPException) as error:
        control.step(command(control, CommissionStep, direction=1))
    assert error.value.status_code == 429
    for _ in range(4):
        clock[0] += 0.26
        control.step(command(control, CommissionStep, direction=1))
        feedback(control)
    clock[0] += 0.26
    with pytest.raises(HTTPException):
        control.step(command(control, CommissionStep, direction=1))
    assert control.trial["requested_us"] == 1550
    with pytest.raises(HTTPException):
        control.mark(
            command(
                control,
                CommissionMark,
                kind="initial",
                expected_requested_us=1500,
                settled_and_clear=True,
            )
        )
    mark(control, "minimum")
    with pytest.raises(HTTPException):
        mark(control, "maximum")
    assert "maximum" not in control.read(control.trial["draft_id"]).joints[0].observations


def test_expiry_restart_reservation_and_stale_finish(trial):
    control, clock, store = trial
    ready(control)
    old = dict(control.trial)
    clock[0] = 1
    control.tick()
    assert control.reserved and control.status().stage == "awaiting_power_off"
    assert control.status().requested_us == 1500
    with pytest.raises(HTTPException):
        control.heartbeat(command(control))
    restarted = LiveCommissioning(control.config, clock=lambda: clock[0])
    restarted.attach(store)
    assert restarted.output is None and restarted.token is None
    assert restarted.status().requested_us is None
    finish(restarted, reviewed=True)
    assert restarted.read(old["draft_id"]).joints[0].state == "needs_review"
    restarted.acquire(CommissionAcquire(draft_id=old["draft_id"], joint_id="claw"))
    with pytest.raises(HTTPException):
        restarted.finish(CommissionFinish(trial_id=old["trial_id"], power_off_and_supported=True))
    assert restarted.trial["joint_id"] == "claw"
    restarted.close()
    control.trial = None  # The restarted controller now owns this saved state.


def test_hardware_gate_domain_and_fault_latching(tmp_path):
    from movement_fixtures import movement_session

    cfg = settings()
    cfg.update(mode="hardware", manual={})
    cfg["arm"].update(backend="pca9685")
    store = Store("trial", tmp_path)
    outputs = []

    class Output:
        def __init__(self, *args, **kwargs):
            self.steps = []
            self.disabled = False
            self.movement = movement_session()
            neutral = dict.fromkeys(range(6), 1500)
            self.movement.move(neutral, neutral, lambda: None)
            outputs.append(self)

        def prepare_all(self):
            from workbench_arm_controller.manual import JOINT_ORDER

            return dict.fromkeys(JOINT_ORDER, 1500)

        def write_joint(self, channel, pulse, minimum, maximum):
            self.steps.append(pulse)
            raise OSError("Injected bus fault")

        def disable(self):
            self.disabled = True

        def close(self):
            pass

    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(voltage=2))
    control = LiveCommissioning(cfg, output_factory=Output, monitor=monitor)
    control.attach(store)
    draft = control.create(CommissionCreate(assembly_id="Physical arm", observer="Operator"))
    assert control.available
    assert not outputs
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="claw", trial_type="all_holding"))
    ready(control)
    with pytest.raises(HTTPException) as error:
        control.step(command(control, CommissionStep, direction=1))
    assert error.value.status_code == 503
    assert control.status().requested_us is None and control.reserved
    assert outputs[0].steps == [1510]
    with pytest.raises(HTTPException):
        control.step(command(control, CommissionStep, direction=1))
    finish(control)
    assert outputs[0].disabled
    cfg["mode"] = "mock"
    mock = LiveCommissioning(cfg)
    mock.attach(store)
    with pytest.raises(HTTPException):
        mock.read(draft.id)
    store.close()


@pytest.mark.parametrize("channel", range(6))
def test_isolated_neutral_output_and_process_lock_but_no_unguarded_movement(channel):
    from workbench_arm_controller.movement import MovementRejected

    bus = FakeBus()
    bus.close = lambda: None
    output = SingleServoOutput(
        bus_number=987, bus_factory=lambda _: bus, check_current=lambda: None
    )
    try:
        output.prepare(channel)
        assert all(bus.registers[0x09 + 4 * c] & 0x10 for c in range(16) if c != channel)
        assert not bus.registers[0x09 + 4 * channel] & 0x10
        writes = len(bus.writes)
        with pytest.raises(RuntimeError):
            DeviceLock(987, 0x40)
        assert len(bus.writes) == writes
        with pytest.raises(MovementRejected, match="all_six"):
            output.write(1510)
        assert len(bus.writes) == writes
    finally:
        before = len(bus.writes)
        output.close()
        assert len(bus.writes) == before  # Closing retains target, even after a fault.
    with DeviceLock(987, 0x40):
        pass


@pytest.mark.asyncio
async def test_api_reservation_excludes_manual_retrieval_and_recovery(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    arm = cfg["services"]["arm-controller"]
    draft = (
        await bus.post(
            url + "/commissioning/drafts",
            json={"assembly_id": "Mock factory arm", "observer": "Test"},
        )
    ).json()
    session = (
        await bus.post(
            url + "/commissioning/acquire", json={"draft_id": draft["id"], "joint_id": "base"}
        )
    ).json()
    token = session["token"]
    for base, route, body in [
        (arm, "/manual/prepare", {"power_disconnected_and_arm_supported": True}),
        (arm, "/manual/session", {"ready_acknowledged": True}),
        (arm, "/recover", {"acknowledged": True}),
        (url, "/requests", {"text": "Get screwdriver", "operation_id": str(uuid4())}),
    ]:
        response = await bus.post(base + route, json=body)
        assert response.status_code == 409, response.text
    response = await bus.post(
        url + "/commissioning/prepare",
        json={"token": token, "sequence": 1, "power_off_supported_isolated": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "prepared"
    response = await bus.post(
        url + "/commissioning/step", json={"token": token, "sequence": 2, "direction": 1}
    )
    assert response.status_code == 409
    await bus.post(url + "/stop")
    status = (await bus.get(url + "/commissioning/status")).json()
    assert status["reserved"] and status["stage"] == "awaiting_power_off"
    response = await bus.post(
        url + "/commissioning/finish",
        json={"trial_id": status["trial_id"], "power_off_and_supported": True},
    )
    assert response.status_code == 200, response.text
    assert not response.json()["reserved"]
    response = await bus.get(url + "/commissioning/drafts/" + draft["id"] + "/export")
    assert response.json()["record"]["id"] == draft["id"]


@pytest.mark.asyncio
async def test_api_heartbeat_expiry_and_reload(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    draft = (
        await bus.post(
            url + "/commissioning/drafts", json={"assembly_id": "Mock", "observer": "Test"}
        )
    ).json()
    session = (
        await bus.post(
            url + "/commissioning/acquire", json={"draft_id": draft["id"], "joint_id": "base"}
        )
    ).json()
    await asyncio.sleep(0.35)
    response = await bus.post(
        url + "/commissioning/heartbeat", json={"token": session["token"], "sequence": 1}
    )
    assert response.status_code == 409
    status = (await bus.get(url + "/commissioning/status")).json()
    assert status["reserved"]
    assert status["requested_us"] is None


def test_physical_record_cannot_resume_or_finish_on_a_different_device(tmp_path):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(backend="pca9685", commissioning_enabled=True, i2c_bus=1)
    store = Store("device-identity", tmp_path)
    control = LiveCommissioning(cfg)
    control.attach(store)
    draft = control.create(CommissionCreate(assembly_id="Factory arm", observer="Operator"))
    cfg["arm"]["i2c_bus"] = 2
    with pytest.raises(HTTPException, match="Restore the recorded"):
        control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base"))
    assert not control.reserved
    cfg["arm"]["i2c_bus"] = 1
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base"))
    control.stop()
    cfg["arm"]["i2c_bus"] = 2
    with pytest.raises(HTTPException, match="Restore the recorded"):
        finish(control)
    assert control.reserved and control.output is None
    control.close()
    store.close()
