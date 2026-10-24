"""Repeatable commissioning tests: simulated controls and injected sensors only."""

import pytest
from fastapi import HTTPException
from test_commissioning_live import command, feedback, finish, mark, ready
from test_commissioning_live import trial as trial
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_contracts import (
    CommissionAcquire,
    CommissionAttemptSelect,
    CommissionFeedback,
    CommissionSelect,
    CommissionStep,
    CommissionTarget,
    CommissionWindow,
)


def test_pending_feedback_blocks_all_mutations_and_rejects_mismatch(trial):
    control, _, _ = trial
    ready(control)
    result = control.step(command(control, CommissionStep, direction=1))
    assert result.stage == "awaiting_feedback"
    pending = result.pending_command.model_dump(mode="json")
    for action in [
        lambda: control.step(command(control, CommissionStep, direction=-1)),
        lambda: mark(control, "initial"),
        lambda: control.select(command(control, CommissionSelect, joint_id="claw")),
        lambda: control.window(
            command(
                control, CommissionWindow, joint_id="base", min_us=1400, max_us=1600, reviewed=True
            )
        ),
        lambda: feedback(control, command_id="wrong"),
        lambda: feedback(control, joint_id="claw"),
        lambda: feedback(control, expected_requested_us=1500),
    ]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    control.heartbeat(command(control))
    assert control.status().pending_command.model_dump(mode="json") == pending
    valid = command(
        control,
        CommissionFeedback,
        command_id=pending["command_id"],
        joint_id="base",
        expected_requested_us=1510,
        outcome="settled_clear",
        observed_direction="none",
        physical_frame="Clockwise viewed from above",
    )
    control.feedback(valid)
    assert control.status().stage == "ready"
    with pytest.raises(HTTPException):
        control.feedback(valid)
    draft = control.read(control.trial["draft_id"])
    assert draft.joints[0].attempts[-1].feedback[0].observed_direction == "none"


@pytest.mark.parametrize("outcome", ["uncertain", "unsafe"])
def test_unresolved_feedback_persists_and_requires_power_off(trial, outcome):
    control, _, store = trial
    ready(control)
    control.step(command(control, CommissionStep, direction=1))
    feedback(control, outcome=outcome, notes="Observed unexpected response")
    assert control.token is None
    assert store.get(control.prefix + "trial")["stage"] == "awaiting_power_off"
    if outcome == "unsafe":
        assert "CUT PHYSICAL SERVO POWER" in control.monitor.fault
    finish(control, reviewed=True)
    joint = control.drafts()[0].joints[0]
    assert joint.state == "needs_review"
    assert joint.attempts[-1].ended_at
    assert joint.attempts[-1].feedback[-1].outcome == outcome


def test_pending_restart_never_replays_and_attempt_cannot_be_reviewed(trial):
    control, _, store = trial
    ready(control)
    control.step(command(control, CommissionStep, direction=1))
    restarted = LiveCommissioning(control.config)
    restarted.attach(store)
    assert restarted.token is None and restarted.output is None
    assert restarted.status().stage == "awaiting_power_off"
    finish(restarted, reviewed=True)
    assert restarted.drafts()[0].joints[0].attempts[-1].state == "needs_review"
    control.trial = None
    restarted.close()


def test_attempt_selection_preserves_prior_review_and_requires_revision(trial):
    control, clock, _ = trial
    draft_id = control.trial["draft_id"]
    ready(control)
    mark(control, "initial")
    for direction, kind in [(-1, "minimum"), (1, None), (1, "maximum")]:
        control.step(command(control, CommissionStep, direction=direction))
        feedback(control)
        if kind:
            mark(control, kind)
        clock[0] += 0.26
    draft = control.read(draft_id)
    joint = draft.joints[0]
    joint.direction = "Clockwise viewed from above"
    control.save(draft)
    finish(control, reviewed=True)
    draft = control.read(draft_id)
    attempt = draft.joints[0].attempts[-1]
    with pytest.raises(HTTPException):
        control.select_attempt(
            draft_id, CommissionAttemptSelect(revision=0, joint_id="base", attempt_id=attempt.id)
        )
    control.select_attempt(
        draft_id,
        CommissionAttemptSelect(revision=draft.revision, joint_id="base", attempt_id=attempt.id),
    )
    assert control.export(draft_id).manual_config["channels"]["base"]["min_us"] == 1490
    control.acquire(CommissionAcquire(draft_id=draft_id, joint_id="base"))
    finish(control)
    joint = control.read(draft_id).joints[0]
    assert len(joint.attempts) == 2
    assert joint.selected_attempt_id == attempt.id
    assert joint.attempts[0].observations == attempt.observations
    assert control.export(draft_id).manual_config["channels"]["base"]["min_us"] == 1490
    with pytest.raises(HTTPException):
        control.select_attempt(
            draft_id,
            CommissionAttemptSelect(
                revision=control.read(draft_id).revision,
                joint_id="base",
                attempt_id=joint.attempts[1].id,
            ),
        )


def test_feedback_storage_failure_inhibits_motion(trial, monkeypatch):
    control, _, _ = trial
    ready(control)
    control.step(command(control, CommissionStep, direction=1))
    monkeypatch.setattr(control, "save", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises((HTTPException, OSError)):
        feedback(control)
    assert control.token is None
    assert control.trial["stage"] == "awaiting_power_off"
    monkeypatch.undo()


def test_write_result_storage_failure_attempts_shutdown(trial, monkeypatch):
    control, _, _ = trial
    ready(control)
    persist = control.persist
    disabled = []
    failed = False

    def fail_after_write():
        nonlocal failed
        if control.trial["requested_us"] == 1510 and not failed:
            failed = True
            raise OSError("Injected result persistence failure")
        persist()

    monkeypatch.setattr(control, "persist", fail_after_write)
    monkeypatch.setattr(control, "emergency_disable", lambda: disabled.append(True))
    with pytest.raises(HTTPException):
        control.step(command(control, CommissionStep, direction=1))
    assert disabled == [True]
    assert control.token is None
    assert control.status().requested_us is None
    assert control.status().stage == "awaiting_power_off"


def test_interrupted_legacy_trial_can_finish_without_invented_feedback(trial):
    control, _, store = trial
    draft_id = control.trial["draft_id"]
    data = store.get(control.prefix + "draft:" + draft_id)
    for joint in data["joints"]:
        joint.pop("attempts", None)
        joint.pop("selected_attempt_id", None)
    store.put(control.prefix + "draft:" + draft_id, data)
    restarted = LiveCommissioning(control.config)
    restarted.attach(store)
    finish(restarted, reviewed=True)
    attempt = restarted.read(draft_id).joints[0].attempts[-1]
    assert attempt.feedback == []
    assert attempt.state == "needs_review"
    control.trial = None
    restarted.close()


def test_initialize_commands_all_neutral_without_power_off_or_settled_claim(trial):
    control, _, _ = trial
    draft_id = control.trial["draft_id"]
    finish(control)
    result = control.initialize(CommissionAcquire(draft_id=draft_id, joint_id="claw"))
    assert result.status.stage == "ready"
    assert result.status.trial_type == "all_holding"
    assert result.status.targets == dict.fromkeys(
        [j.id for j in control.read(draft_id).joints], 1500
    )
    control.target(command(control, CommissionTarget, joint_id="claw", pulse_us=1490))
    feedback(control, observed_direction="unknown", physical_frame="Not recorded")
    events = control.read(draft_id).history
    assert any(e.action == "neutral_result" and "unmeasured" in e.detail for e in events)
    assert not any(e.action == "ready" for e in events)
    finish(control)
    restarted_session = control.initialize(CommissionAcquire(draft_id=draft_id, joint_id="base"))
    assert set(restarted_session.status.targets.values()) == {1500}
    assert len(restarted_session.status.targets) == 6


def test_initialize_never_clears_a_latched_sensor_fault(trial):
    control, _, _ = trial
    draft_id = control.trial["draft_id"]
    finish(control)
    control.monitor.trip("Injected missing sensor")
    with pytest.raises(RuntimeError, match="Injected missing sensor"):
        control.initialize(CommissionAcquire(draft_id=draft_id, joint_id="base"))
    assert not control.reserved
    assert control.output is None


def test_neutral_output_checks_current_throughout_initialization():
    from test_base_servo_test import FakeBus
    from workbench_arm_controller.servo_trial import SingleServoOutput

    bus = FakeBus()
    bus.close = lambda: None
    checks = []
    output = SingleServoOutput(bus_number=985, bus_factory=lambda _: bus)
    try:
        result = output.initialize_neutral(lambda: checks.append(len(bus.writes)))
        assert len(result) == 6
        assert len(checks) == 13
        assert checks[0] == 0
        assert all(checks[i + 1] > checks[i] for i in range(1, 12, 2))
    finally:
        output.disable()
        output.close()


@pytest.mark.asyncio
async def test_initialize_api_excludes_other_owners_and_initializes_six_targets(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    draft = (
        await bus.post(
            url + "/commissioning/drafts", json={"assembly_id": "Test", "observer": "Test"}
        )
    ).json()
    payload = {"draft_id": draft["id"], "joint_id": "base"}
    result = await bus.post(url + "/commissioning/initialize", json=payload)
    assert result.status_code == 200, result.text
    assert len(result.json()["status"]["targets"]) == 6
    repeated = await bus.post(url + "/commissioning/initialize", json=payload)
    assert repeated.status_code == 409
