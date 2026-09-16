"""Offline calibration notebooks and write-once trial evidence; never device control."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pca9685 import PulsePlan

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Number = Annotated[float, Field(allow_inf_nan=False)]
PulseWidth = Annotated[Number, Field(ge=500, le=2500)]
Domain = Literal["physical", "simulated"]


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Evidence timestamps need a timezone offset")
    return parsed


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Record):
    observer: Text | None = None
    observed_at: str | None = None
    method: Text | None = None
    reference: Text | None = None

    @model_validator(mode="after")
    def check_time(self):
        if self.observed_at is not None:
            timestamp(self.observed_at)
        return self

    def complete(self):
        return all(value is not None for value in self.model_dump().values())


class Joint(Record):
    name: Text | None = None
    channel: Annotated[int, Field(ge=0, le=15)] | None = None
    min_us: PulseWidth | None = None
    max_us: PulseWidth | None = None
    increasing_pulse_direction: Text | None = None
    evidence: Evidence = Field(default_factory=Evidence)

    @model_validator(mode="after")
    def check_bounds(self):
        if self.min_us is not None and self.max_us is not None and self.min_us >= self.max_us:
            raise ValueError("Joint min_us must be below max_us")
        return self


class Pose(Record):
    requested_us: dict[Text, PulseWidth | None] = Field(default_factory=dict)
    reviewed: bool = False
    conditions: Text | None = None
    evidence: Evidence = Field(default_factory=Evidence)


class Calibration(Record):
    schema_version: Literal[1] = 1
    kind: Literal["calibration"] = "calibration"
    domain: Domain
    assembly_id: Text | None = None
    conditions: Text | None = None
    joints: Annotated[list[Joint], Field(min_length=6, max_length=6)] = Field(
        default_factory=lambda: [Joint() for _ in range(6)]
    )
    poses: dict[Text, Pose] = Field(default_factory=dict)

    def pulse_plan(self):
        if any(
            value is None
            for joint in self.joints
            for value in (joint.name, joint.channel, joint.min_us, joint.max_us)
        ):
            return None
        # This local validation flag is never exported as an operational calibration.
        return PulsePlan(
            {
                "calibrated": True,
                "channels": {
                    joint.name: joint.model_dump(include={"channel", "min_us", "max_us"})
                    for joint in self.joints
                },
            }
        )

    @model_validator(mode="after")
    def check_mapping_and_poses(self):
        for field in ("name", "channel"):
            values = [getattr(joint, field) for joint in self.joints]
            known = [value for value in values if value is not None]
            if len(known) != len(set(known)):
                raise ValueError(f"Joint {field} values must be unique when known")
        plan = self.pulse_plan()
        named_joints = {joint.name: joint for joint in self.joints if joint.name is not None}
        for pose in self.poses.values():
            for name, value in pose.requested_us.items():
                if name not in named_joints:
                    raise ValueError(f"Pose references an unassigned joint name: {name}")
                joint = named_joints[name]
                if value is not None and (
                    (joint.min_us is not None and value < joint.min_us)
                    or (joint.max_us is not None and value > joint.max_us)
                ):
                    raise ValueError(f"Pulse outside measured limits for {name}")
            if pose.reviewed and (
                plan is None or pose.conditions is None or not pose.evidence.complete()
            ):
                raise ValueError("Reviewed poses need complete pulse data, conditions and evidence")
            if pose.reviewed:
                plan.encode(pose.requested_us)
        return self

    def report(self):
        missing = []
        for name in ("assembly_id", "conditions"):
            if getattr(self, name) is None:
                missing.append(name)
        for index, joint in enumerate(self.joints, 1):
            for name, value in joint.model_dump(exclude={"evidence"}).items():
                if value is None:
                    missing.append(f"joint[{index}].{name}")
            if not joint.evidence.complete():
                missing.append(f"joint[{index}].evidence")
        return {
            "domain": self.domain,
            "pulse_data_complete": self.pulse_plan() is not None,
            "missing_record_fields": missing,
            "reviewed_poses": [name for name, pose in self.poses.items() if pose.reviewed],
            "unreviewed_poses": [name for name, pose in self.poses.items() if not pose.reviewed],
            "hardware_qualified": False,
            "automated_retrieval_enabled": False,
        }


class Trial(Record):
    schema_version: Literal[1] = 1
    kind: Literal["trial"] = "trial"
    domain: Domain
    trial_id: Text = Field(default_factory=lambda: str(uuid4()))
    scenario: Text | None = None
    tool_id: Text | None = None
    operation_id: Text | None = None
    started_at: str | None = None
    ended_at: str | None = None
    outcome: Literal["not_run", "incomplete", "pass", "fail"] = "not_run"
    evidence: Evidence = Field(default_factory=Evidence)
    spoken_request: Text | None = None
    transcript: Text | None = None
    observations: Text | None = None
    source_observation: Text | None = None
    tray_observation: Text | None = None
    successful_delivery: bool | None = None
    wrong_tool_delivery: bool | None = None
    stop_recovery_notes: Text | None = None
    failure_reason: Text | None = None
    timings_ms: dict[
        Literal["recognition", "inference", "motion", "total"],
        Annotated[Number, Field(ge=0)] | None,
    ] = Field(
        default_factory=lambda: dict.fromkeys(("recognition", "inference", "motion", "total"))
    )

    @model_validator(mode="after")
    def check_result(self):
        start = timestamp(self.started_at) if self.started_at is not None else None
        end = timestamp(self.ended_at) if self.ended_at is not None else None
        if end is not None and (start is None or end < start):
            raise ValueError("Trial end requires a start and cannot precede it")
        completed = self.outcome in {"pass", "fail"}
        if completed and (
            start is None
            or end is None
            or self.scenario is None
            or self.observations is None
            or not self.evidence.complete()
        ):
            raise ValueError("Completed trials need times, scenario, observations and evidence")
        if self.outcome == "fail" and self.failure_reason is None:
            raise ValueError("Failed trials need a failure_reason")
        if self.successful_delivery is True and self.wrong_tool_delivery is True:
            raise ValueError("A wrong-tool delivery cannot also be a successful delivery")
        if self.outcome == "not_run" and (
            start is not None
            or end is not None
            or self.successful_delivery is not None
            or self.wrong_tool_delivery is not None
        ):
            raise ValueError("A not_run trial cannot contain execution times or delivery results")
        if completed and self.scenario == "delivery":
            for name in (
                "tool_id",
                "operation_id",
                "source_observation",
                "tray_observation",
                "successful_delivery",
                "wrong_tool_delivery",
            ):
                if getattr(self, name) is None:
                    raise ValueError(f"Completed delivery trials require {name}")
            if self.outcome == "pass" and (
                self.successful_delivery is not True or self.wrong_tool_delivery is not False
            ):
                raise ValueError("Passing delivery requires success and no wrong-tool delivery")
        return self


class Snapshot(Record):
    schema_version: Literal[1] = 1
    saved_at: str
    record: Calibration | Trial
    calibration: Calibration | None = None
    sha256: str

    @model_validator(mode="after")
    def check_integrity(self):
        timestamp(self.saved_at)
        if self.sha256 != digest(self.model_dump(exclude={"sha256"})):
            raise ValueError("Snapshot SHA-256 mismatch; preserve the original evidence")
        if isinstance(self.record, Trial):
            if self.calibration is None or self.calibration.domain != self.record.domain:
                raise ValueError("Trial needs a frozen calibration from the same evidence domain")
        elif self.calibration is not None:
            raise ValueError("Calibration snapshots cannot contain another calibration")
        return self


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def read_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def write_new(path, value):
    path = Path(path)
    encoded = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def load_record(path):
    value = read_json(path)
    if not isinstance(value, dict) or value.get("kind") not in {"calibration", "trial"}:
        raise ValueError("Expected a calibration or trial notebook")
    return (Calibration if value["kind"] == "calibration" else Trial).model_validate(value)


def save_snapshot(record, output, calibration=None):
    # Revalidate mutable notebook models and normalize numbers before hashing.
    record = type(record).model_validate(record.model_dump())
    if calibration is not None:
        calibration = Calibration.model_validate(calibration.model_dump())
    payload = {
        "schema_version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "record": record.model_dump(),
        "calibration": calibration.model_dump() if calibration is not None else None,
    }
    snapshot = Snapshot.model_validate({**payload, "sha256": digest(payload)})
    write_new(output, snapshot.model_dump())
    return snapshot


def summarize(snapshots, tools):
    if len(tools) != 3 or len(set(tools)) != 3:
        raise ValueError("Specify the three distinct tools for this acceptance cohort")
    counts = {
        domain: {
            "completed_deliveries": dict.fromkeys(tools, 0),
            "successes": 0,
            "wrong_tool_deliveries": 0,
            "incomplete_or_not_run": 0,
            "other_scenarios": 0,
        }
        for domain in ("physical", "simulated")
    }
    trial_ids, operations, calibrations = set(), set(), {"physical": set(), "simulated": set()}
    for snapshot in snapshots:
        trial = snapshot.record
        if not isinstance(trial, Trial):
            raise ValueError("Summary inputs must all be trial snapshots")
        if trial.trial_id in trial_ids:
            raise ValueError(f"Duplicate trial_id: {trial.trial_id}")
        trial_ids.add(trial.trial_id)
        if trial.operation_id is not None:
            operation = (trial.domain, trial.operation_id)
            if operation in operations:
                raise ValueError(f"Repeated operation_id: {trial.operation_id}")
            operations.add(operation)
        bucket = counts[trial.domain]
        # Disclose a known wrong-tool event even if the trial was interrupted or
        # was entered under a component/fault scenario instead of delivery.
        bucket["wrong_tool_deliveries"] += int(trial.wrong_tool_delivery is True)
        # Unknown results are never converted to failures, passes or zero wrong deliveries.
        if trial.outcome in {"not_run", "incomplete"}:
            bucket["incomplete_or_not_run"] += 1
        elif trial.scenario != "delivery":
            bucket["other_scenarios"] += 1
        else:
            if trial.tool_id not in tools:
                raise ValueError(f"Tool outside the selected acceptance cohort: {trial.tool_id}")
            calibrations[trial.domain].add(digest(snapshot.calibration.model_dump()))
            bucket["completed_deliveries"][trial.tool_id] += 1
            bucket["successes"] += int(trial.outcome == "pass" and trial.successful_delivery)
    for domain, bucket in counts.items():
        bucket["calibration_versions"] = len(calibrations[domain])
        bucket["recorded_delivery_target_met"] = (
            all(count == 10 for count in bucket["completed_deliveries"].values())
            and bucket["successes"] >= 27
            and bucket["wrong_tool_deliveries"] == 0
            and bucket["incomplete_or_not_run"] == 0
            and len(calibrations[domain]) == 1
        )
    return {"counts": counts, "hardware_qualified": False, "automated_retrieval_enabled": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Write an editable notebook with unknown values unset")
    init.add_argument("kind", choices=("calibration", "trial"))
    init.add_argument("--domain", choices=("physical", "simulated"), required=True)
    init.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check", help="Validate notebook structure and report missing data")
    check.add_argument("--input", type=Path, required=True)
    seal = commands.add_parser("snapshot", help="Freeze a notebook into a new hashed evidence file")
    seal.add_argument("--input", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    seal.add_argument("--calibration", type=Path, help="Required calibration snapshot for trials")
    summary = commands.add_parser("summary", help="Count a selected cohort, separated by domain")
    summary.add_argument("--tools", nargs=3, required=True)
    summary.add_argument("snapshots", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            model = Calibration if args.kind == "calibration" else Trial
            write_new(args.output, model(domain=args.domain).model_dump())
            result = {"notebook": str(args.output.resolve())}
        elif args.command == "summary":
            result = summarize(
                [Snapshot.model_validate(read_json(path)) for path in args.snapshots], args.tools
            )
        else:
            record = load_record(args.input)
            if args.command == "check":
                result = (
                    record.report()
                    if isinstance(record, Calibration)
                    else {
                        "domain": record.domain,
                        "outcome": record.outcome,
                        "hardware_qualified": False,
                        "automated_retrieval_enabled": False,
                    }
                )
            else:
                calibration = None
                if args.calibration is not None:
                    source = Snapshot.model_validate(read_json(args.calibration))
                    if not isinstance(source.record, Calibration):
                        raise ValueError("--calibration must name a calibration snapshot")
                    calibration = source.record
                snapshot = save_snapshot(record, args.output, calibration)
                result = {"snapshot": str(args.output.resolve()), "sha256": snapshot.sha256}
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
