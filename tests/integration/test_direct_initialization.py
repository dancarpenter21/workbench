"""Direct value entry records configuration and uses monitored explicit movements."""

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from workbench_arm_controller.app import create_app
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.manual import JOINT_ORDER
from workbench_common import Store, settings
from workbench_contracts import (
    CommissionAcquire,
    CommissionBoundsUpdate,
    CommissionCreate,
    CommissionLimitValues,
    CommissionTarget,
)


@pytest.fixture
def direct(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    clock = [0.0]
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0])
    store = Store("direct-values", tmp_path)
    monitor.store = store
    control = LiveCommissioning(cfg, clock=lambda: clock[0], monitor=monitor)
    control.attach(store)
    yield control, monitor, clock, store
    control.close()
    monitor.close()
    store.close()


def update(control, **changes):
    context = control.bounds_context()
    return CommissionBoundsUpdate(
        assembly_id=context.assembly_id,
        setup=context.setup,
        joints={
            name: CommissionLimitValues(minimum_us=1000, maximum_us=2000) for name in JOINT_ORDER
        },
    ).model_copy(update=changes)


def test_manual_limits_persist_without_polling_or_outputs(direct, monkeypatch):
    control, monitor, _, store = direct
    monkeypatch.setattr(monitor, "poll", lambda **_: pytest.fail("Saving polled hardware"))
    monkeypatch.setattr(control, "open_output", lambda: pytest.fail("Saving opened output"))
    saved = control.set_bounds(update(control))
    assert saved == control.bounds()
    assert all(j.minimum.source == "manual" for j in saved.joints.values())
    assert all(j.minimum.trial_id == "" for j in saved.joints.values())
    assert store.get(control.bounds_key(saved))
    assert control.trial is None


@pytest.mark.parametrize("minimum,maximum", [(1500, 1500), (499, 2000), (1000, 2501), (1600, 2000)])
def test_invalid_limits_are_rejected(minimum, maximum):
    with pytest.raises(ValidationError):
        CommissionLimitValues(minimum_us=minimum, maximum_us=maximum)


def test_wrong_setup_or_missing_joints_leaves_saved_bounds_unchanged(direct):
    control, _, _, _ = direct
    original = control.set_bounds(update(control))
    for request in [update(control, assembly_id="another arm"), update(control, joints={})]:
        with pytest.raises(HTTPException):
            control.set_bounds(request)
        assert control.bounds() == original


def initialize(control):
    draft = control.create(
        CommissionCreate(assembly_id=control.bounds().assembly_id, observer="Test")
    )
    return control.initialize(CommissionAcquire(draft_id=draft.id, joint_id="base"))


@pytest.mark.parametrize("saved", [False, True])
def test_direct_position_ignores_jog_limits_and_monitors_current(direct, saved):
    control, monitor, clock, _ = direct
    if saved:
        control.set_bounds(update(control))
    session = initialize(control)
    request = CommissionTarget(token=session.token, sequence=1, joint_id="base", pulse_us=1700)
    status = control.position(request)
    assert status.targets["base"] == 1700
    assert all(status.targets[name] == 1500 for name in JOINT_ORDER if name != "base")
    assert status.stage == "ready"
    clock[0] += 0.26
    status = control.position(request.model_copy(update={"sequence": 2, "pulse_us": 2100}))
    assert status.targets["base"] == 2100
    clock[0] += 0.26
    with pytest.raises(HTTPException) as error:
        control.position(request.model_copy(update={"sequence": 3, "pulse_us": 2501}))
    assert error.value.status_code == 422
    assert control.status().targets["base"] == 2100
    monitor.trip("Injected sensor failure")
    with pytest.raises(HTTPException):
        control.position(request.model_copy(update={"sequence": 4, "pulse_us": 1600}))
    assert control.status().stage == "awaiting_power_off"
    assert not control.token


@pytest.mark.asyncio
async def test_bounds_api_validates_and_saves_without_needing_motion_session(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("direct-api", tmp_path)
    app = create_app(cfg, store)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                bounds = (await client.get("/commissioning/bounds")).json()
                payload = {
                    "assembly_id": bounds["assembly_id"],
                    "setup": bounds["setup"],
                    "joints": {
                        name: {"minimum_us": 1000, "maximum_us": 2000} for name in JOINT_ORDER
                    },
                }
                result = await client.post("/commissioning/bounds", json=payload)
                assert result.status_code == 200, result.text
                assert result.json()["joints"]["base"]["minimum"]["source"] == "manual"
                assert not (await client.get("/commissioning/status")).json()["reserved"]
                payload["joints"]["base"]["minimum_us"] = 2200
                assert (await client.post("/commissioning/bounds", json=payload)).status_code == 422
    finally:
        store.close()


def test_explicit_initialize_replaces_restart_reservation_without_resetting_faults(direct):
    control, monitor, _, store = direct
    first = initialize(control)
    old_draft = control.trial["draft_id"]
    old_trial = control.trial["trial_id"]
    restarted = LiveCommissioning(control.config, clock=control.clock, monitor=monitor)
    restarted.attach(store)
    assert restarted.status().reserved
    assert not restarted.status().can_resume_powered
    monitor.trip("Injected sensor fault")
    with pytest.raises(RuntimeError, match="Injected sensor fault"):
        initialize(restarted)
    assert restarted.trial["trial_id"] == old_trial
    assert monitor.fault
    monitor.reset_fault()  # Explicit operator reset, never part of initialize.
    result = initialize(restarted)
    assert result.token != first.token
    assert result.status.stage == "ready"
    assert result.status.targets == dict.fromkeys(JOINT_ORDER, 1500)
    assert any(e.action == "superseded" for e in restarted.read(old_draft).history)
    restarted.close()
    control.trial = None


def test_initialize_never_steals_an_active_session(direct):
    control, _, _, _ = direct
    initialize(control)
    original = control.token
    with pytest.raises(HTTPException) as error:
        initialize(control)
    assert error.value.status_code == 409
    assert control.token == original


def test_partial_limits_preserve_other_joints_and_allow_single_endpoints(direct):
    control, _, _, _ = direct
    base = {"base": CommissionLimitValues(minimum_us=750, maximum_us=2200)}
    result = control.set_bounds(update(control, joints=base))
    assert result.joints["base"].minimum.requested_us == 750
    assert result.joints["base"].maximum.requested_us == 2200
    assert result.joints["claw"].minimum is None
    result = control.set_bounds(
        update(control, joints={"claw": CommissionLimitValues(minimum_us=900)})
    )
    assert result.joints["claw"].minimum.requested_us == 900
    assert result.joints["claw"].maximum is None
    assert result.joints["base"].maximum.requested_us == 2200
    result = control.set_bounds(
        update(control, joints={"claw": CommissionLimitValues(maximum_us=1800)})
    )
    assert result.joints["claw"].minimum.requested_us == 900
    assert result.joints["claw"].maximum.requested_us == 1800
    assert result == control.bounds()
