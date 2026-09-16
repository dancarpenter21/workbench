"""Synthetic fixtures exercise decisions, never qualify physical motion."""

from dataclasses import replace

import pytest
from workbench_arm_controller.rehearsal import (
    RehearsalController,
    RehearsalPlan,
    SimulationObservation,
)
from workbench_common import Store


def plan_args():
    joints = [f"synthetic_{i}" for i in range(6)]
    return {
        "calibration": {
            "calibrated": True,
            "channels": {
                j: {"channel": i, "min_us": 1100, "max_us": 1900} for i, j in enumerate(joints)
            },
        },
        "poses": {
            "a": dict.fromkeys(joints, 1400),
            "b": dict.fromkeys(joints, 1500),
            "c": dict.fromkeys(joints, 1600),
        },
        "transitions": [("a", "b"), ("b", "c")],
        "max_delta_us": dict.fromkeys(joints, 110),
        "evidence_timeout_seconds": 10,
        "observation_max_age_seconds": 2,
    }


@pytest.fixture
def bench(tmp_path):
    store = Store("rehearsal", tmp_path)
    now = [100.0]
    controller = RehearsalController(RehearsalPlan(**plan_args()), store, clock=lambda: now[0])
    yield controller, now, store
    store.close()


def observation(controller, now, pose="a", operation=None):
    now[0] += 0.1
    return SimulationObservation(controller.session_id, pose, now[0], True, operation)


def ready(bench):
    controller, now, _ = bench
    controller.recover(observation(controller, now), acknowledged=True)
    return controller


def test_startup_has_no_pose_command_or_hardware_readiness(bench):
    controller, now, _ = bench
    status = controller.snapshot()
    assert status["state"] == "recovery_required"
    assert status["mode"] == "simulation"
    assert not status["hardware_ready"] and not status["measured_feedback"]
    assert status["simulated_command"] is None
    assert status["simulated_checkpoint"] is None
    with pytest.raises(ValueError, match="requires recovery"):
        controller.request("one", "b")
    with pytest.raises(ValueError, match="acknowledgement"):
        controller.recover(observation(controller, now), acknowledged=False)


def test_command_and_elapsed_time_never_supply_completion(bench):
    controller = ready(bench)
    _, now, store = bench
    status = controller.request("one", "b")
    assert status["state"] == "awaiting_evidence"
    assert status["simulated_checkpoint"] is None and status["completion_evidence"] is None
    assert len(status["simulated_command"]) == 6
    assert store.get("rehearsal:operation:one")["target_pose"] == "b"
    now[0] += 1
    assert controller.poll()["state"] == "awaiting_evidence"
    now[0] += 20
    status = controller.poll()
    assert status["state"] == "fault" and status["recovery_required"]
    assert status["completion_evidence"] is None
    with pytest.raises(ValueError, match="No active"):
        controller.observe(observation(controller, now, "b", "one"))
    assert controller.snapshot()["state"] == "fault"


def test_independent_simulated_evidence_completes_one_transition_only(bench):
    controller = ready(bench)
    _, now, store = bench
    controller.request("one", "b")
    with pytest.raises(ValueError, match="busy"):
        controller.request("two", "c")
    status = controller.observe(observation(controller, now, "b", "one"))
    assert status["state"] == "completed"
    assert status["completion_evidence"]["kind"] == "simulated_checkpoint"
    assert not status["hardware_ready"]
    assert store.get("rehearsal:operation:two") is None
    controller.request("two", "c")
    assert controller.snapshot()["state"] == "awaiting_evidence"


@pytest.mark.parametrize(
    "change", ["old", "future", "wrong_operation", "old_session", "moving", "nan"]
)
def test_invalid_evidence_cannot_complete_motion(bench, change):
    controller = ready(bench)
    _, now, _ = bench
    controller.request("one", "b")
    event = observation(controller, now, "b", "one")
    changes = {
        "old": {"captured_at": now[0] - 3},
        "future": {"captured_at": now[0] + 1},
        "wrong_operation": {"operation_id": "other"},
        "old_session": {"session_id": "previous"},
        "moving": {"stationary": False},
        "nan": {"captured_at": float("nan")},
    }
    with pytest.raises(ValueError, match="Evidence"):
        controller.observe(replace(event, **changes[change]))
    assert controller.snapshot()["state"] == "awaiting_evidence"
    assert controller.snapshot()["completion_evidence"] is None


def test_precommand_or_command_dictionary_is_not_completion_evidence(bench):
    controller = ready(bench)
    _, now, _ = bench
    event = observation(controller, now, "b", "one")
    status = controller.request("one", "b")
    with pytest.raises(ValueError, match="Evidence"):
        controller.observe(event)
    with pytest.raises(ValueError, match="SimulationObservation"):
        controller.observe(status["simulated_command"])


def test_wrong_observed_checkpoint_latches_fault(bench):
    controller = ready(bench)
    _, now, _ = bench
    controller.request("one", "b")
    status = controller.observe(observation(controller, now, "c", "one"))
    assert status["state"] == "fault"
    with pytest.raises(ValueError, match="requires recovery"):
        controller.request("two", "b")


def test_stop_blocks_commands_and_requires_new_inspection_evidence(bench):
    controller = ready(bench)
    _, now, _ = bench
    controller.request("one", "b")
    event = observation(controller, now, "a")
    with pytest.raises(ValueError, match="Stop or fault"):
        controller.recover(event, acknowledged=True)
    stopped = controller.stop()
    assert stopped["state"] == "stop_unconfirmed"
    assert stopped["recovery_required"] and stopped["completion_evidence"] is None
    with pytest.raises(ValueError, match="after the stop"):
        controller.recover(event, acknowledged=True)
    with pytest.raises(ValueError, match="requires recovery"):
        controller.request("two", "b")
    controller.recover(observation(controller, now), acknowledged=True)
    with pytest.raises(ValueError, match="do not replay"):
        controller.request("one", "b")
    controller.request("two", "b")


def test_reopen_durable_store_invalidates_evidence_and_preserves_submitted_ids(tmp_path):
    now = [100.0]
    plan = RehearsalPlan(**plan_args())
    with_store = Store("rehearsal", tmp_path)
    first = RehearsalController(plan, with_store, clock=lambda: now[0])
    first.recover(observation(first, now), acknowledged=True)
    first.request("one", "b")
    old_event = observation(first, now)
    with_store.close()
    store = Store("rehearsal", tmp_path)
    try:
        second = RehearsalController(plan, store, clock=lambda: now[0])
        status = second.snapshot()
        assert status["state"] == "fault" and status["simulated_command"] is None
        assert store.get("rehearsal:operation:one")["result"]["state"] == "fault"
        with pytest.raises(ValueError, match="Evidence"):
            second.recover(old_event, acknowledged=True)
        second.recover(observation(second, now), acknowledged=True)
        with pytest.raises(ValueError, match="do not replay"):
            second.request("one", "b")
    finally:
        store.close()


def test_failed_intent_commit_returns_no_command_and_does_not_change_state(bench, monkeypatch):
    controller = ready(bench)
    _, _, store = bench
    before = controller.snapshot()

    def fail(_):
        raise OSError("injected disk failure")

    monkeypatch.setattr(store, "put_many", fail)
    with pytest.raises(OSError, match="disk failure"):
        controller.request("one", "b")
    assert controller.snapshot() == before
    assert store.get("rehearsal:operation:one") is None


def test_unreviewed_transition_and_stale_start_never_create_intent(bench):
    controller = ready(bench)
    _, now, store = bench
    with pytest.raises(ValueError, match="explicitly allowed"):
        controller.request("one", "c")
    now[0] += 3
    with pytest.raises(ValueError, match="stale"):
        controller.request("one", "b")
    assert store.get("rehearsal:operation:one") is None
    assert controller.snapshot()["recovery_required"]


@pytest.mark.parametrize("next_event", ["stop", "stale_start"])
def test_terminal_operation_evidence_survives_later_blocks(bench, next_event):
    controller = ready(bench)
    _, now, store = bench
    controller.request("one", "b")
    controller.observe(observation(controller, now, "b", "one"))
    result = store.get("rehearsal:operation:one")
    if next_event == "stop":
        controller.stop()
    else:
        now[0] += 3
        with pytest.raises(ValueError, match="stale"):
            controller.request("two", "c")
    assert controller.snapshot()["recovery_required"]
    assert store.get("rehearsal:operation:one") == result
    assert result["result"]["completion_evidence"]["kind"] == "simulated_checkpoint"


def test_fault_diagnosis_is_preserved_when_stop_follows(bench):
    controller = ready(bench)
    _, _, store = bench
    controller.request("one", "b")
    controller.fault("injected control loss")
    controller.stop()
    assert store.get("rehearsal:operation:one")["result"]["message"] == "injected control loss"


def test_plan_copies_inputs_and_disallows_collection_edits():
    args = plan_args()
    plan = RehearsalPlan(**args)
    args["poses"]["b"]["synthetic_5"] = 1800
    assert plan.poses["b"]["synthetic_5"] == 1500
    with pytest.raises(TypeError):
        plan.poses["b"]["synthetic_5"] = 1800
    with pytest.raises(TypeError):
        plan.max_delta_us["synthetic_5"] = 999
    assert isinstance(plan.transitions, frozenset)


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf"), None])
def test_no_implicit_command_limits_or_timing_defaults(bad):
    args = plan_args()
    args["max_delta_us"]["synthetic_5"] = bad
    with pytest.raises(ValueError, match="finite and positive"):
        RehearsalPlan(**args)


def test_bound_applies_to_quantized_commands_and_every_directed_edge():
    args = plan_args()
    args["transitions"].append(("a", "c"))
    with pytest.raises(ValueError, match="delta bound"):
        RehearsalPlan(**args)
    args = plan_args()
    args["poses"]["b"]["synthetic_5"] = 2200
    with pytest.raises(ValueError, match="limits"):
        RehearsalPlan(**args)
