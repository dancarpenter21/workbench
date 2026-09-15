"""Durable, offline rehearsal of motion decisions. Never imports a device driver.

Checkpoint observations are explicitly simulated; they contain no measured joint
positions. This module is not a selectable application backend.
"""

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from types import MappingProxyType
from uuid import uuid4

from workbench_arm_controller.pca9685 import PulsePlan


def positive(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")
    return value


class RehearsalPlan:
    """Explicit pulse bounds and allowed edges, not a physical trajectory model.

    All limits must be supplied. Synthetic test limits are only simulation inputs.
    Pulse deltas bound command changes; they do not bound joint speed or travel.
    """

    def __init__(
        self,
        calibration,
        poses,
        transitions,
        max_delta_us,
        *,
        evidence_timeout_seconds,
        observation_max_age_seconds,
    ):
        self.pulses = PulsePlan(calibration)
        if not isinstance(poses, dict) or not poses:
            raise ValueError("Provide named rehearsal poses")
        self.poses = {}
        for name, pose in poses.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Invalid pose name")
            self.pulses.encode(pose)
            self.poses[name] = dict(pose)
        if not isinstance(max_delta_us, dict) or set(max_delta_us) != set(self.pulses.channels):
            raise ValueError("Provide an explicit command delta bound for each joint")
        self.max_delta_us = {
            joint: positive(value, "Command delta") for joint, value in max_delta_us.items()
        }
        self.transitions = set()
        for edge in transitions:
            if (
                not isinstance(edge, (list, tuple))
                or len(edge) != 2
                or any(not isinstance(name, str) or name not in self.poses for name in edge)
                or edge[0] == edge[1]
            ):
                raise ValueError("Each transition must join two different named poses")
            self.transitions.add(tuple(edge))
        if not self.transitions:
            raise ValueError("Provide allowed directed transitions")
        for start, target in self.transitions:
            # Include quantization in the command-change check.
            before = {p.joint: p.commanded_us for p in self.pulses.encode(self.poses[start])}
            after = {p.joint: p.commanded_us for p in self.pulses.encode(self.poses[target])}
            if any(abs(after[j] - before[j]) > self.max_delta_us[j] for j in before):
                raise ValueError(f"Transition {start} -> {target} exceeds a command delta bound")
        self.timeout = positive(evidence_timeout_seconds, "Evidence timeout")
        self.max_age = positive(observation_max_age_seconds, "Observation maximum age")
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "calibration": calibration,
                    "poses": self.poses,
                    "transitions": sorted(self.transitions),
                    "max_delta_us": self.max_delta_us,
                    "timeout": self.timeout,
                    "max_age": self.max_age,
                },
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        # Prevent edits through the supplied/returned collections after review.
        self.poses = MappingProxyType(
            {name: MappingProxyType(pose) for name, pose in self.poses.items()}
        )
        self.transitions = frozenset(self.transitions)
        self.max_delta_us = MappingProxyType(self.max_delta_us)


@dataclass(frozen=True)
class SimulationObservation:
    session_id: str
    pose_id: str
    captured_at: float  # Same monotonic clock as this rehearsal session.
    stationary: bool
    operation_id: str | None = None


class RehearsalController:
    """Single-owner, synchronous state machine using the existing durable Store.

    Start/restart requires new inspection and simulated starting evidence. Intent
    commits before a simulated command is returned. Submitted IDs are never reused.
    Time may fault a motion, but can never complete one. There is no retry method.
    """

    KEY = "rehearsal:status"

    def __init__(self, plan, store, *, clock=time.monotonic):
        self.plan, self.store, self.clock = plan, store, clock
        self.session_id = str(uuid4())
        self.started_at = self.clock()
        previous = store.get(self.KEY, {})
        interrupted = previous.get("state") == "awaiting_evidence"
        status = {
            "mode": "simulation",
            "hardware_ready": False,
            "measured_feedback": False,
            "session_id": self.session_id,
            "plan_sha256": plan.fingerprint,
            "state": "fault" if interrupted else "recovery_required",
            "recovery_required": True,
            "operation_id": previous.get("operation_id") if interrupted else None,
            "simulated_checkpoint": None,
            "simulated_command": None,
            "completion_evidence": None,
            "target_pose": None,
            "requested_at": None,
            "deadline": None,
            "message": "Restart interrupted rehearsal"
            if interrupted
            else "Inspect before rehearsal",
        }
        self._save(status)

    def snapshot(self):
        return self.store.get(self.KEY)

    def _save(self, status, intent=None):
        values = {self.KEY: status}
        operation_id = status["operation_id"]
        if operation_id is not None:
            key = f"rehearsal:operation:{operation_id}"
            record = intent if intent is not None else self.store.get(key)
            if record is not None and record.get("result", {}).get("state") in {
                None,
                "awaiting_evidence",
            }:
                values[key] = {**record, "result": status}
        self.store.put_many(values)
        return self.snapshot()

    def _observation(self, observation, *, operation_id, after):
        if not isinstance(observation, SimulationObservation):
            raise ValueError("Only explicit SimulationObservation evidence is accepted")
        now = self.clock()
        captured = observation.captured_at
        if (
            observation.session_id != self.session_id
            or observation.operation_id != operation_id
            or observation.pose_id not in self.plan.poses
            or observation.stationary is not True
            or isinstance(captured, bool)
            or not isinstance(captured, (int, float))
            or not math.isfinite(captured)
            or not after < captured <= now
            or now - captured > self.plan.max_age
        ):
            raise ValueError(
                "Evidence must be fresh, stationary and tied to this session/operation"
            )
        return asdict(observation)

    def recover(self, observation, *, acknowledged):
        status = self.snapshot()
        if status["state"] == "awaiting_evidence":
            raise ValueError("Stop or fault the active rehearsal before recovery")
        if acknowledged is not True:
            raise ValueError("Explicit inspection acknowledgement is required")
        checkpoint = self._observation(observation, operation_id=None, after=self.started_at)
        # Evidence from before a stop/fault cannot clear it.
        if checkpoint["captured_at"] <= status.get("blocked_at", self.started_at):
            raise ValueError("Recovery needs evidence after the stop/fault")
        status.update(
            state="ready",
            recovery_required=False,
            operation_id=None,
            simulated_checkpoint=checkpoint,
            simulated_command=None,
            completion_evidence=None,
            target_pose=None,
            requested_at=None,
            deadline=None,
            message="Ready for one explicit simulated transition",
        )
        return self._save(status)

    def request(self, operation_id, target_pose):
        status = self.snapshot()
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("Provide a new operation ID")
        if self.store.get(f"rehearsal:operation:{operation_id}") is not None:
            raise ValueError("Operation already submitted; do not replay")
        if status["state"] not in {"ready", "completed"} or status["recovery_required"]:
            raise ValueError("Rehearsal is busy or requires recovery")
        checkpoint = status["simulated_checkpoint"]
        if self.clock() - checkpoint["captured_at"] > self.plan.max_age:
            self.fault("Starting checkpoint became stale")
            raise ValueError("Starting checkpoint became stale; inspect and recover")
        if (checkpoint["pose_id"], target_pose) not in self.plan.transitions:
            raise ValueError("Transition has not been explicitly allowed")
        commands = [asdict(p) for p in self.plan.pulses.encode(dict(self.plan.poses[target_pose]))]
        now = self.clock()
        intent = {
            "operation_id": operation_id,
            "plan_sha256": self.plan.fingerprint,
            "from_pose": checkpoint["pose_id"],
            "target_pose": target_pose,
            "requested_at": now,
            "session_id": self.session_id,
        }
        status.update(
            state="awaiting_evidence",
            operation_id=operation_id,
            simulated_checkpoint=None,
            simulated_command=commands,
            completion_evidence=None,
            target_pose=target_pose,
            requested_at=now,
            deadline=now + self.plan.timeout,
            message="Simulated command only; waiting for independent simulated evidence",
        )
        return self._save(status, intent)  # No command escapes a failed commit.

    def observe(self, observation):
        status = self.poll()
        if status["state"] != "awaiting_evidence":
            raise ValueError("No active rehearsal awaiting evidence")
        checkpoint = self._observation(
            observation, operation_id=status["operation_id"], after=status["requested_at"]
        )
        if checkpoint["pose_id"] != status["target_pose"]:
            return self.fault("Simulated observation did not match the requested checkpoint")
        status.update(
            state="completed",
            simulated_checkpoint=checkpoint,
            completion_evidence={"kind": "simulated_checkpoint", **checkpoint},
            message="Simulated checkpoint confirmed; no physical completion claim",
        )
        return self._save(status)

    def poll(self):
        status = self.snapshot()
        if status["state"] == "awaiting_evidence" and self.clock() >= status["deadline"]:
            return self.fault("Evidence timeout; motion outcome unknown")
        return status

    def _block(self, state, message):
        status = self.snapshot()
        status.update(
            state=state,
            recovery_required=True,
            blocked_at=self.clock(),
            simulated_checkpoint=None,
            completion_evidence=None,
            message=message,
        )
        return self._save(status)

    def stop(self):
        return self._block("stop_unconfirmed", "Commands inhibited; physical stop is unconfirmed")

    def fault(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A fault reason is required")
        return self._block("fault", reason)
