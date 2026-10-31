"""Analytic geometry and fake-output tests only; no physical hardware access."""

import json
import math
import sys
from dataclasses import replace

import pytest
from movement_fixtures import geometry_data, movement_session
from workbench_arm_controller.movement import (
    ArmGeometry,
    MovementRejected,
    MovementSession,
    main,
)


def neutral():
    return dict.fromkeys(range(6), 1500)


def start(session):
    session.move(neutral(), neutral(), lambda: None)


def test_neutral_reference_and_analytic_forward_kinematics():
    model = ArmGeometry.from_dict(geometry_data())
    assert model.clearances((0, 0, 0, 0))[-1] == 89
    assert model.clearances((math.pi / 2, 0, 0, 0))[-1] == pytest.approx(309)
    # A 90-degree rotation of the elbow affects only its descendants.
    assert model.clearances((0, math.pi / 2, 0, 0))[0] == 94
    assert model.clearances((0, math.pi / 2, 0, 0))[-1] == pytest.approx(209)


def test_base_yaw_and_full_opening_claw_are_height_invariant():
    model = ArmGeometry.from_dict(geometry_data())
    result = model.check_pose(neutral())
    for base in (500, 1500, 2500):
        for claw in (500, 1500, 2500):
            assert model.check_pose({**neutral(), 5: base, 0: claw}) == result


def test_general_axis_wrist_rotation_includes_finger_thickness():
    data = geometry_data()
    data["joints"][-1]["axis"] = [1, 0, 0]
    data["capsules"][-1]["b_mm"] = [240, 30, 100]
    model = ArmGeometry.from_dict(data)
    assert model.clearances((0, 0, 0, -math.pi / 2))[-1] == pytest.approx(59)


def test_positive_neutral_axes_follow_right_hand_rotation():
    data = geometry_data()
    for joint in data["joints"]:
        joint["axis"] = [1, 0, 0] if joint["channel"] == 1 else [0, 1, 0]
    model = ArmGeometry.from_dict(data)
    # Positive +Y rotation takes the arm's +X reach downward; negative raises it.
    assert model.clearances((math.pi / 2, 0, 0, 0))[-1] == pytest.approx(-151)
    assert model.clearances((-math.pi / 2, 0, 0, 0))[-1] == pytest.approx(309)
    # A finger offset toward +Y moves upward under positive claw-mount roll (+X).
    for endpoint in ("a_mm", "b_mm"):
        data["capsules"][-1][endpoint][1] = 30
    model = ArmGeometry.from_dict(data)
    assert model.clearances((0, 0, 0, math.pi / 2))[-1] == pytest.approx(119)
    assert model.clearances((0, 0, 0, -math.pi / 2))[-1] == pytest.approx(59)


def test_reversed_mapping_and_no_extrapolation():
    data = geometry_data()
    data["joints"][0]["pulse_angle_deg"] = [[1000, 45], [1500, 0], [2000, -40]]
    model = ArmGeometry.from_dict(data)
    assert model.joints[0].angle(1750) == pytest.approx(math.radians(-20))
    with pytest.raises(ValueError, match="outside measured"):
        model.joints[0].angle(999)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(calibrated=False),
        lambda d: d.update(base_axis_perpendicular_to_table=False),
        lambda d: d.update(reference_pulse_us=1498),
        lambda d: d.update(clearance_mm=0),
        lambda d: d.update(geometry_error_mm=float("nan")),
        lambda d: d.update(max_boxes=0),
        lambda d: d["joints"][0].update(channel=5),
        lambda d: d["joints"][0].update(axis=[0, 0, 0]),
        lambda d: d["joints"][0].update(pulse_angle_deg=[[500, -90], [1500, 1], [2500, 90]]),
        lambda d: d["joints"][0].update(pulse_angle_deg=[[500, 90], [1500, 0], [2500, 90]]),
        lambda d: d["joints"][0].update(error_deg=-1),
        lambda d: d["capsules"].pop(),
        lambda d: d["capsules"][0].update(parent_channel=True),
        lambda d: d["capsules"][0].update(radius_mm=0),
    ],
)
def test_invalid_geometry_is_rejected(mutation):
    data = geometry_data()
    mutation(data)
    with pytest.raises(ValueError):
        ArmGeometry.from_dict(data)


def test_safe_endpoints_do_not_prove_clear_intermediate_motion():
    data = geometry_data()
    # Claw is above its joint at neutral. +/- 90-degree endpoints are safe,
    # but the sweep via 180 degrees takes it below the table.
    for joint in data["joints"]:
        joint["origin_mm"] = [0, 0, 100]
        joint["error_deg"] = 0
    for capsule in data["capsules"]:
        capsule["a_mm"] = capsule["b_mm"] = [0, 0, 100]
    data["capsules"][-1]["a_mm"] = data["capsules"][-1]["b_mm"] = [0, 0, 220]
    model = ArmGeometry.from_dict(data)
    for angle in (math.pi / 2, 3 * math.pi / 2):
        assert model.check_motion(((angle, angle), (0, 0), (0, 0), (0, 0))).accepted
    result = model.check_motion(((math.pi / 2, 3 * math.pi / 2), (0, 0), (0, 0), (0, 0)))
    assert not result.accepted and result.component == "claw"


def test_asynchronous_progress_is_checked_even_when_synchronized_motion_is_clear():
    model = ArmGeometry.from_dict(geometry_data())
    # Equal opposing shoulder/elbow angles keep the distal arm horizontal, but
    # the elbow moving ahead of the shoulder can lower the claw through the table.
    assert model.check_motion(((0, 0), (0, 0), (0, 0), (0, 0))).accepted
    assert model.check_motion(((1.2, 1.2), (-1.2, -1.2), (0, 0), (0, 0))).accepted
    assert not model.check_motion(((0, 1.2), (-1.2, 0), (0, 0), (0, 0))).accepted


def test_exact_margin_is_rejected_and_error_allowance_changes_admission():
    model = ArmGeometry.from_dict(geometry_data())
    exact = replace(model, clearance=89, joints=tuple(replace(j, error=0) for j in model.joints))
    assert not exact.check_pose(neutral()).accepted
    nominal = replace(exact, clearance=88)
    assert nominal.check_pose(neutral()).accepted
    assert not replace(nominal, geometry_error=3).check_pose(neutral()).accepted
    assert not replace(nominal, joints=model.joints).check_pose(neutral()).accepted


def test_subdivision_certifies_clear_box_and_budget_exhaustion_rejects():
    model = ArmGeometry.from_dict(geometry_data())
    ranges = ((0, 2.0), (0, 0.3), (0, 0.2), (0, 0.1))
    result = model.check_motion(ranges)
    assert result.accepted and result.boxes_checked > 1
    assert result.clearance_lower_bound_mm > model.clearance
    result = replace(model, max_boxes=1).check_motion(ranges)
    assert not result.accepted and result.reason == "clearance_unproven_budget"


def test_unknown_start_and_missing_geometry_fail_closed_except_verified_startup():
    session = MovementSession()
    writes = []
    with pytest.raises(MovementRejected, match="1500"):
        session.move({4: 1510}, {4: 1508}, lambda: writes.append(True))
    start(session)
    with pytest.raises(MovementRejected, match="geometry_missing"):
        session.move({5: 1510}, {5: 1508}, lambda: writes.append(True))
    assert not writes and session.commanded == neutral()


def test_rejection_does_not_change_held_state_or_dispatch():
    session = movement_session()
    start(session)
    ranges = session.ranges
    with pytest.raises(MovementRejected):
        session.move({4: 500}, {4: 502}, lambda: pytest.fail("Unsafe movement dispatched"))
    assert session.commanded == neutral() and session.ranges == ranges
    assert not session.faulted


def test_successful_write_advances_v1_assumed_position_without_sensor_confirmation():
    session = movement_session()
    start(session)
    session.move({4: 1600}, {4: 1600}, lambda: None)
    assert session.ranges[0] == pytest.approx((math.radians(9),) * 2)
    session.move({4: 1550}, {4: 1550}, lambda: None)
    assert session.ranges[0] == pytest.approx((math.radians(4.5),) * 2)
    assert session.last_result.position_source == "assumed_from_successful_pwm_write"
    assert not session.last_result.measured_feedback


def test_partial_dispatch_failure_invalidates_state_and_never_retries():
    session = movement_session()
    start(session)

    def fail():
        raise OSError("partial write")

    with pytest.raises(OSError):
        session.move({4: 1600}, {4: 1600}, fail)
    assert session.faulted and not session.commanded
    with pytest.raises(MovementRejected, match="unknown_position"):
        session.move(neutral(), neutral(), lambda: pytest.fail("Retried after fault"))


def test_config_loading_rejects_simulated_model_in_hardware_mode(tmp_path):
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(geometry_data()))
    cfg = {"mode": "hardware", "arm": {"movement_geometry": str(path)}}
    session = MovementSession.from_config(cfg)
    assert session.model is None and "domain" in session.unavailable_reason
    cfg["mode"] = "mock"
    assert MovementSession.from_config(cfg).model is not None


def test_cli_is_offline_quantizes_and_reports_rejection(tmp_path, monkeypatch, capsys):
    model, calibration, target = (
        tmp_path / name for name in ("model.json", "pwm.json", "pose.json")
    )
    model.write_text(json.dumps(geometry_data()))
    calibration.write_text(
        json.dumps(
            {
                "calibrated": True,
                "channels": {
                    str(c): {"channel": c, "min_us": 500, "max_us": 2500} for c in range(6)
                },
            }
        )
    )
    monkeypatch.setitem(sys.modules, "smbus2", None)
    target.write_text(json.dumps({str(c): 600 if c == 4 else 1500 for c in range(6)}))
    args = ["--geometry", str(model), "--calibration", str(calibration), "--target", str(target)]
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run" and result["domain"] == "simulated"
    assert not result["accepted"] and not result["measured_feedback"]
    target.write_text(json.dumps({str(c): 1500 for c in range(6)}))
    assert main(args) == 0
