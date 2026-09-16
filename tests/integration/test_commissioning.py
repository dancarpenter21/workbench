"""Synthetic records only: these fixtures are not measured hardware calibrations."""

import builtins
import json

import pytest
from workbench_arm_controller.commissioning import (
    Calibration,
    Snapshot,
    Trial,
    main,
    read_json,
    save_snapshot,
    summarize,
)


def evidence():
    return {
        "observer": "synthetic test operator",
        "observed_at": "2026-09-15T14:00:00+00:00",
        "method": "synthetic fixture, no physical observations",
        "reference": "test fixture only",
    }


def calibration(domain="simulated"):
    return Calibration.model_validate(
        {
            "domain": domain,
            "assembly_id": "synthetic fixture",
            "conditions": "test values, no hardware",
            "joints": [
                {
                    "name": f"test_joint_{i}",
                    "channel": i,
                    "min_us": 1101,
                    "max_us": 1901,
                    "increasing_pulse_direction": "synthetic positive direction",
                    "evidence": evidence(),
                }
                for i in range(6)
            ],
        }
    )


def completed_trial(domain="simulated", tool="a", **changes):
    return Trial.model_validate(
        {
            "domain": domain,
            "scenario": "delivery",
            "tool_id": tool,
            "operation_id": "synthetic operation",
            "outcome": "pass",
            "started_at": "2026-09-15T13:59:00+00:00",
            "ended_at": "2026-09-15T14:00:00+00:00",
            "observations": "Synthetic success",
            "source_observation": "empty",
            "tray_observation": "requested fixture",
            "successful_delivery": True,
            "wrong_tool_delivery": False,
            "evidence": evidence(),
            **changes,
        }
    )


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_templates_leave_every_hardware_value_unknown_and_never_import_devices(
    tmp_path, monkeypatch, capsys
):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"smbus2", "serial", "picamera2"}:
            pytest.fail(f"Imported hardware library: {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    path = tmp_path / "working.json"
    assert main(["init", "calibration", "--domain", "physical", "--output", str(path)]) == 0
    record = read_json(path)
    assert record["domain"] == "physical"
    assert record["assembly_id"] is None
    assert record["poses"] == {}
    assert len(record["joints"]) == 6
    assert all(joint["channel"] is None and joint["min_us"] is None for joint in record["joints"])
    assert main(["check", "--input", str(path)]) == 0
    report = Calibration.model_validate(record).report()
    assert not report["pulse_data_complete"]
    assert not report["hardware_qualified"] and not report["automated_retrieval_enabled"]
    assert len(report["missing_record_fields"]) > 0
    original_bytes = path.read_bytes()
    with pytest.raises(SystemExit):
        main(["init", "trial", "--domain", "simulated", "--output", str(path)])
    assert path.read_bytes() == original_bytes
    capsys.readouterr()


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", True),
        ("channel", 16),
        ("min_us", float("nan")),
        ("min_us", 499),
        ("max_us", float("inf")),
        ("max_us", 1100),
    ],
)
def test_rejects_invalid_supplied_joint_values(field, value):
    record = calibration().model_dump()
    record["joints"][0][field] = value
    with pytest.raises(ValueError):
        Calibration.model_validate(record)


@pytest.mark.parametrize("field", ["name", "channel"])
def test_rejects_duplicate_assignments_even_in_incomplete_notebooks(field):
    record = Calibration(domain="physical").model_dump()
    record["joints"][0][field] = record["joints"][1][field] = 0 if field == "channel" else "joint"
    with pytest.raises(ValueError, match="unique"):
        Calibration.model_validate(record)


def test_pose_review_checks_pulse_plan_and_evidence_but_never_qualifies_hardware():
    record = calibration().model_dump()
    record["poses"] = {
        "fixture": {
            "requested_us": {joint["name"]: 1500 for joint in record["joints"]},
            "reviewed": True,
            "conditions": "synthetic fixture only",
            "evidence": evidence(),
        }
    }
    report = Calibration.model_validate(record).report()
    assert report["pulse_data_complete"] and not report["missing_record_fields"]
    assert report["reviewed_poses"] == ["fixture"]
    assert not report["hardware_qualified"] and not report["automated_retrieval_enabled"]
    record["poses"]["fixture"]["requested_us"]["test_joint_0"] = 2000
    with pytest.raises(ValueError, match="outside measured limits"):
        Calibration.model_validate(record)
    record["poses"]["fixture"]["requested_us"]["test_joint_0"] = 1500
    record["poses"]["fixture"]["evidence"]["observer"] = None
    with pytest.raises(ValueError, match="Reviewed poses"):
        Calibration.model_validate(record)


def test_snapshot_freezes_data_checks_digest_and_never_overwrites(tmp_path):
    record = calibration()
    path = tmp_path / "calibration.snapshot.json"
    saved = save_snapshot(record, path)
    original = path.read_bytes()
    record.joints[0].min_us = 1200
    assert Snapshot.model_validate(read_json(path)).record.joints[0].min_us == 1101
    with pytest.raises(FileExistsError):
        save_snapshot(record, path)
    assert path.read_bytes() == original
    tampered = saved.model_dump()
    tampered["record"]["conditions"] = "edited after snapshot"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        Snapshot.model_validate(tampered)


def test_partial_unreviewed_poses_keep_unknown_targets_unset_and_check_supplied_bounds():
    record = calibration().model_dump()
    record["poses"] = {"draft": {"requested_us": {"test_joint_0": None}}}
    assert Calibration.model_validate(record).report()["unreviewed_poses"] == ["draft"]
    record["poses"]["draft"]["requested_us"]["test_joint_0"] = 2000
    with pytest.raises(ValueError, match="outside measured limits"):
        Calibration.model_validate(record)
    record["poses"]["draft"]["requested_us"] = {"unknown": 1500}
    with pytest.raises(ValueError, match="unassigned joint"):
        Calibration.model_validate(record)
    record["poses"]["draft"] = {
        "requested_us": {"test_joint_0": None},
        "reviewed": True,
        "conditions": "synthetic",
        "evidence": evidence(),
    }
    with pytest.raises(ValueError, match="exactly the six"):
        Calibration.model_validate(record)


@pytest.mark.parametrize("outcome,scenario", [("incomplete", "delivery"), ("fail", "stop")])
def test_known_wrong_tool_events_are_disclosed_outside_completed_deliveries(
    outcome, scenario, tmp_path
):
    trial = completed_trial(
        outcome=outcome,
        scenario=scenario,
        successful_delivery=False,
        wrong_tool_delivery=True,
        failure_reason="synthetic fault",
    )
    snapshot = save_snapshot(trial, tmp_path / "wrong.json", calibration())
    report = summarize([snapshot], ["a", "b", "c"])["counts"]["simulated"]
    assert report["wrong_tool_deliveries"] == 1
    assert sum(report["completed_deliveries"].values()) == 0
    assert not report["recorded_delivery_target_met"]


def test_trial_snapshot_embeds_calibration_and_rejects_missing_or_mixed_domain(tmp_path):
    trial = completed_trial()
    saved = save_snapshot(trial, tmp_path / "trial.json", calibration())
    assert Snapshot.model_validate(read_json(tmp_path / "trial.json")) == saved
    assert saved.calibration.joints[0].min_us == 1101
    for supplied in (None, calibration("physical")):
        with pytest.raises(ValueError, match="same evidence domain"):
            save_snapshot(trial, tmp_path / "invalid.json", supplied)
    assert not (tmp_path / "invalid.json").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"successful_delivery": None},
        {"wrong_tool_delivery": None},
        {"evidence": {}},
        {"started_at": "2026-09-15T14:00:00"},
        {"ended_at": "2026-09-14T14:00:00Z"},
        {"outcome": "fail", "failure_reason": None},
        {"wrong_tool_delivery": True},
        {"source_observation": None},
        {"timings_ms": {"motion": float("nan")}},
    ],
)
def test_completed_trials_require_observations_not_command_or_elapsed_time(changes):
    with pytest.raises(ValueError):
        completed_trial(**changes)


def test_elapsed_duration_and_supplied_pulses_cannot_fill_missing_trial_evidence():
    trial = Trial(domain="physical", outcome="incomplete", timings_ms={"motion": 2000})
    assert trial.successful_delivery is None and trial.wrong_tool_delivery is None
    with pytest.raises(ValueError):
        Trial.model_validate({**trial.model_dump(), "commanded_us": {"joint": 1500}})
    with pytest.raises(ValueError):
        completed_trial(outcome="not_run")


def test_summary_keeps_physical_simulated_incomplete_and_wrong_tool_results_separate(tmp_path):
    snapshots = []
    for domain in ("simulated", "physical"):
        for tool in ("a", "b", "c"):
            for index in range(10):
                trial = completed_trial(domain, tool, operation_id=f"{tool}-{index}")
                if domain == "physical" and tool == "a" and index == 0:
                    trial = completed_trial(
                        domain,
                        tool,
                        operation_id="a-0",
                        outcome="fail",
                        successful_delivery=False,
                        wrong_tool_delivery=True,
                        failure_reason="synthetic wrong-tool result",
                    )
                snapshots.append(
                    save_snapshot(
                        trial, tmp_path / f"{domain}-{tool}-{index}.json", calibration(domain)
                    )
                )
    summary = summarize(snapshots, ["a", "b", "c"])
    assert summary["counts"]["simulated"]["recorded_delivery_target_met"]
    physical = summary["counts"]["physical"]
    assert physical["successes"] == 29 and physical["wrong_tool_deliveries"] == 1
    assert not physical["recorded_delivery_target_met"]
    assert not summary["hardware_qualified"] and not summary["automated_retrieval_enabled"]
    snapshots.append(
        save_snapshot(
            Trial(domain="simulated", outcome="incomplete"),
            tmp_path / "incomplete.json",
            calibration(),
        )
    )
    assert not summarize(snapshots, ["a", "b", "c"])["counts"]["simulated"][
        "recorded_delivery_target_met"
    ]


def test_summary_rejects_duplicate_trials_operations_and_mixed_calibration_qualification(tmp_path):
    trial = completed_trial()
    snapshot = save_snapshot(trial, tmp_path / "one.json", calibration())
    with pytest.raises(ValueError, match="Duplicate trial_id"):
        summarize([snapshot, snapshot], ["a", "b", "c"])
    other = save_snapshot(completed_trial(), tmp_path / "two.json", calibration())
    with pytest.raises(ValueError, match="Repeated operation_id"):
        summarize([snapshot, other], ["a", "b", "c"])
    changed = calibration()
    changed.conditions = "different fixture setup"
    other = save_snapshot(
        completed_trial(operation_id="different"), tmp_path / "three.json", changed
    )
    report = summarize([snapshot, other], ["a", "b", "c"])["counts"]["simulated"]
    assert report["calibration_versions"] == 2
    assert not report["recorded_delivery_target_met"]


def test_cli_snapshot_round_trip_and_duplicate_json_key_rejection(tmp_path, capsys):
    notebook = write_json(tmp_path / "working.json", calibration().model_dump())
    frozen = tmp_path / "calibration.json"
    assert main(["snapshot", "--input", str(notebook), "--output", str(frozen)]) == 0
    trial = write_json(tmp_path / "trial-working.json", completed_trial().model_dump())
    result = tmp_path / "trial.json"
    assert (
        main(
            [
                "snapshot",
                "--input",
                str(trial),
                "--output",
                str(result),
                "--calibration",
                str(frozen),
            ]
        )
        == 0
    )
    assert main(["summary", "--tools", "a", "b", "c", str(result)]) == 0
    notebook.write_text('{"kind":"calibration", "kind":"trial"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["check", "--input", str(notebook)])
    capsys.readouterr()
