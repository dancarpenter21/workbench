"""Export an arm-off observation window from the service's durable journal.

Read-only SQLite access can backfill acquisition samples even when a status HTTP
request is delayed. Sensor faults and acquisition gaps are retained as evidence;
this module never touches I2C, PWM, motion ownership, or recovery.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from workbench_common import ROOT

from .off_baseline import summarize, validate_sample


def record_window(
    database,
    directory,
    *,
    start_at,
    duration=1800,
    observer="operator",
    stream="claw:service",
    wall_clock=lambda: datetime.now(timezone.utc),
    sleep=time.sleep,
    setup=None,
):
    start_at = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    if start_at.tzinfo is None:
        raise ValueError("Start time must include its timezone")
    end_at = start_at + timedelta(seconds=duration)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "baseline_id": directory.name,
        "read_only": True,
        "setup": setup or {},
        "source": str(Path(database).resolve()),
        "stream": stream,
        "observer": observer,
        "declared_state": "arm_power_off",
        "started_at": start_at.isoformat(),
        "scheduled_end_at": end_at.isoformat(),
        "requested_duration_seconds": duration,
        "acquisition_method": "Read-only copy of existing service journal; backfill and follow",
    }
    report = {**metadata, "status": "in_progress", "summary": None}
    raw_path = directory / "samples.jsonl"
    valid = []
    previous = None
    last_id = 0
    issues = 0
    seen = set()
    domains = set()
    source_identity = None
    with raw_path.open("x", buffering=1) as log:

        def event(kind, **values):
            log.write(
                json.dumps(
                    {
                        "record_type": kind,
                        "baseline_id": directory.name,
                        "recorded_at": wall_clock().isoformat(),
                        **values,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
            log.flush()

        event("start", metadata=metadata)
        try:
            uri = Path(database).resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1) as db:
                while True:
                    rows = db.execute(
                        "SELECT id, payload FROM journal WHERE stream = ? AND id > ? ORDER BY id LIMIT 1000",
                        (stream, last_id),
                    ).fetchall()
                    for record_id, payload in rows:
                        last_id = record_id
                        sample = json.loads(payload)
                        timestamp = sample.get("sampled_at")
                        if not timestamp:
                            if wall_clock() >= start_at:
                                issues += 1
                                event(
                                    "invalid",
                                    source_record_id=record_id,
                                    reading=sample,
                                    error="Missing acquisition timestamp",
                                )
                            continue
                        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if not start_at <= stamp < end_at:
                            continue
                        if timestamp in seen:
                            # Fault records can repeat the last acquisition: retain them.
                            if sample.get("healthy") and not sample.get("fault"):
                                continue
                        seen.add(timestamp)
                        event("sample", source_record_id=record_id, reading=sample)
                        domains.add("simulated" if sample.get("simulated", True) else "physical")
                        try:
                            validate_sample(sample)
                            for field in ("i2c_bus", "sensor_address", "pwm_address"):
                                if (
                                    setup
                                    and field in setup
                                    and sample.get(field) is not None
                                    and sample[field] != setup[field]
                                ):
                                    raise ValueError("Source setup differs from declared setup")
                            identity = tuple(
                                sample.get(field)
                                for field in (
                                    "scale_verified",
                                    "shunt_ohms",
                                    "verification_evidence",
                                    "monitor_session_id",
                                    "i2c_bus",
                                    "sensor_address",
                                    "pwm_address",
                                )
                            )
                            if source_identity is not None and identity != source_identity:
                                issues += 1
                                event("setup_change", source_record_id=record_id)
                            source_identity = identity
                            if previous and not 0 < (stamp - previous).total_seconds() < 0.3:
                                issues += 1
                                event(
                                    "gap",
                                    source_record_id=record_id,
                                    seconds=(stamp - previous).total_seconds(),
                                )
                            previous = stamp
                            valid.append(sample)
                        except (ValueError, TypeError) as exc:
                            issues += 1
                            event("invalid", source_record_id=record_id, error=str(exc))
                    if len(rows) == 1000:
                        continue
                    if wall_clock() >= end_at:
                        break
                    sleep(min(0.2, max(0, (end_at - wall_clock()).total_seconds())))
            span = (
                (
                    datetime.fromisoformat(valid[-1]["sampled_at"].replace("Z", "+00:00"))
                    - datetime.fromisoformat(valid[0]["sampled_at"].replace("Z", "+00:00"))
                ).total_seconds()
                if valid
                else 0
            )
            if (
                len(valid) < max(2, duration * 4)
                or span < duration * 0.9
                or domains != {"physical"}
            ):
                issues += 1
                event("incomplete", error="Insufficient physical sample/time coverage")
            if valid:
                first = datetime.fromisoformat(valid[0]["sampled_at"].replace("Z", "+00:00"))
                last = datetime.fromisoformat(valid[-1]["sampled_at"].replace("Z", "+00:00"))
                if (first - start_at).total_seconds() >= 0.3 or (
                    end_at - last
                ).total_seconds() >= 0.3:
                    issues += 1
                    event(
                        "edge_gap", error="Start or end of observation window lacks fresh samples"
                    )
            summary = summarize(valid) if len(valid) >= 2 else None
            if issues and summary:
                summary["proposed_bounds"] = None
                summary["qualification"] = (
                    "Observation has gaps or invalid readings; review evidence before establishing bounds"
                )
            report.update(
                status="complete" if not issues else "observed_with_gaps",
                domain="physical" if domains == {"physical"} else "mixed_or_unknown",
                issue_count=issues,
                sample_count=len(valid),
                source_span_seconds=span,
                summary=summary,
            )
            event(
                "complete",
                status=report["status"],
                issue_count=issues,
                sample_count=len(valid),
                summary=summary,
            )
        except BaseException as exc:
            report.update(
                status="rejected", error=str(exc), sample_count=len(valid), issue_count=issues
            )
            event("rejected", error=str(exc))
            raise
        finally:
            log.flush()
            os.fsync(log.fileno())
            report["ended_at"] = wall_clock().isoformat()
            report["samples_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            with (directory / "report.json").open("x") as output:
                json.dump(report, output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / ".runtime/hardware/arm.sqlite3")
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/development/pi.json")
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--arm-off-confirmed", action="store_true")
    parser.add_argument("--observer", default="operator")
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / ".runtime/commissioning/off-baselines"
    )
    args = parser.parse_args(argv)
    if not args.arm_off_confirmed or not 10 <= args.duration <= 3600:
        parser.error("--arm-off-confirmed and duration 10–3600 seconds required")
    directory = args.output_root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-journal-" + uuid4().hex[:8]
    )
    print(f"Read-only backfill and follow of service samples: {directory}", flush=True)
    arm = json.loads(args.config.read_text())["arm"]
    setup = {
        "i2c_bus": arm.get("i2c_bus", 1),
        "sensor_address": 0x41,
        "pwm_address": arm.get("i2c_address", 0x40),
        "config": str(args.config.resolve()),
    }
    report = record_window(
        args.database,
        directory,
        start_at=args.start_at,
        duration=args.duration,
        observer=args.observer,
        setup=setup,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
