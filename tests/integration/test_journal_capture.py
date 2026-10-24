"""Passive export of durable physical-sample fixtures, never hardware access."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_off_baseline import reading
from workbench_arm_controller.journal_capture import record_window
from workbench_common import Store


def export(tmp_path, *, missing=(), fault=False, restart=False):
    epoch = datetime.now(timezone.utc)
    store = Store("arm", tmp_path)
    store.append("claw:service", reading(sampled_at=(epoch - timedelta(seconds=1)).isoformat()))
    for index in range(20):
        if index in missing:
            continue
        sample = reading(sampled_at=(epoch + timedelta(seconds=index / 10)).isoformat())
        if fault and index == 8:
            sample.update(healthy=False, fault="INA219 failure")
        if restart and index >= 8:
            sample["monitor_session_id"] = "restarted"
        store.append("claw:service", sample)
    store.append("commissioning:simulated:other", reading(simulated=True))
    before = len(store.journal("claw:service"))
    store.close()
    directory = tmp_path / "observation"
    result = record_window(
        tmp_path / "arm.sqlite3",
        directory,
        start_at=epoch.isoformat(),
        duration=2,
        wall_clock=lambda: epoch + timedelta(seconds=2),
        sleep=lambda _: pytest.fail("Backfill should complete immediately"),
    )
    reopened = Store("arm", tmp_path)
    assert len(reopened.journal("claw:service")) == before
    reopened.close()
    return result, [
        json.loads(line) for line in (directory / "samples.jsonl").read_text().splitlines()
    ]


def test_read_only_journal_backfill_recovers_entire_observation_window(tmp_path):
    report, events = export(tmp_path)
    assert report["status"] == "complete" and report["sample_count"] == 20
    assert report["domain"] == "physical" and report["issue_count"] == 0
    assert report["summary"]["proposed_bounds"]["max_abs_current_a"] is None
    assert len([e for e in events if e["record_type"] == "sample"]) == 20
    assert all(e["source_record_id"] > 1 for e in events if e["record_type"] == "sample")


@pytest.mark.parametrize(
    "arguments,kind",
    [
        ({"missing": (5, 6, 7, 8)}, "gap"),
        ({"missing": (0, 1, 2, 3)}, "edge_gap"),
        ({"missing": (16, 17, 18, 19)}, "edge_gap"),
        ({"fault": True}, "invalid"),
        ({"restart": True}, "setup_change"),
    ],
)
def test_gaps_faults_and_restarts_remain_in_logs_and_prevent_new_bounds(tmp_path, arguments, kind):
    report, events = export(tmp_path, **arguments)
    assert report["status"] == "observed_with_gaps" and report["issue_count"] > 0
    assert report["summary"]["proposed_bounds"] is None
    assert any(e["record_type"] == kind for e in events)
