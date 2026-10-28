"""Bounded, single-joint execution on the existing monitored output owner.

All methods run under the arm motion lock. tick runs in the same I/O worker as
current monitoring, never in an inference worker or a browser heartbeat.
"""

import time
from uuid import uuid4

from fastapi import HTTPException
from workbench_contracts import HarnessArmStatus, HarnessLease, HarnessOperation


class HarnessExecutor:
    def __init__(self, manual, *, clock=time.monotonic):
        self.manual = manual
        self.clock = clock
        self.store = None
        self.token = None
        self.operation = None
        self.targets = []
        self.limits = {}
        self.claw = {}
        self.deadline = self.heartbeat_until = self.started = self.updated = 0.0
        self.message = "Prepare outputs in Jog, then enable a supervised harness session."
        self.submissions = 0

    @property
    def active(self):
        return self.token is not None

    def attach(self, store):
        self.store = store
        previous = store.get("harness:active_operation")
        if previous:
            operation = self.lookup(previous)
            operation.state = "interrupted"
            operation.message = "Controller restarted; inspect hardware and recover. No replay."
            self.operation = operation
            self.persist()
            self.manual.recovery_required = True
        if store.get("harness:session_active"):
            self.manual.recovery_required = True
        store.put("harness:session_active", False)

    def bounds(self, bounds):
        result = {}
        expected = "simulated" if self.manual.simulated else "physical"
        if bounds.domain != expected:
            raise HTTPException(409, "Harness bounds domain mismatch")
        for joint in self.manual.joints:
            saved = bounds.joints.get(joint.id)
            if saved and saved.minimum and saved.maximum:
                low = max(saved.minimum.requested_us, joint.min_us)
                high = min(saved.maximum.requested_us, joint.max_us)
                if low < high:
                    result[joint.id] = (low, high)
        return result

    def enable(self, request, bounds):
        m = self.manual
        if self.active or m.token or not m.prepared or m.recovery_required or m.error:
            raise HTTPException(409, "Prepare/recover outputs and end existing sessions first")
        if not m.simulated and m.output is None:
            raise HTTPException(409, "Physical output is not prepared")
        m.check_current()
        if not m.monitor.status().scale_verified and not request.acknowledge_unverified_scale:
            raise HTTPException(
                409, "Explicit supervised unverified-current-scale acknowledgement required"
            )
        self.limits = self.bounds(bounds)
        self.claw = {"open_claw": request.claw_open_us, "close_claw": request.claw_close_us}
        if request.claw_open_us == request.claw_close_us:
            raise HTTPException(422, "Open and close targets must differ")
        for target in self.claw.values():
            self.validate_target("claw", target)
        # No homing or initialization: the owner must already know every held target.
        if set(m.commanded) != set(m.targets):
            raise HTTPException(409, "Commanded position is unknown; prepare outputs first")
        self.store.put("harness:session_active", True)
        self.token = uuid4()
        self.submissions = 0
        self.deadline = self.clock() + 300
        self.heartbeat_until = self.clock() + 3
        self.message = (
            "Supervised harness enabled; pulse bounds do not provide collision avoidance."
        )
        return HarnessLease(token=self.token)

    def validate_target(self, joint, target):
        if joint not in self.limits:
            raise HTTPException(409, f"Save both pulse bounds for {joint} before harness motion")
        low, high = self.limits[joint]
        if not low <= target <= high:
            raise HTTPException(422, f"Target for {joint} outside saved bounds [{low}, {high}]")
        current = self.manual.targets[joint]
        if not low <= current <= high:
            raise HTTPException(409, f"Current commanded {joint} target is outside saved bounds")

    def authenticate(self, token):
        if self.active and self.clock() >= min(self.deadline, self.heartbeat_until):
            self.stop("Operator session expired; inspect and recover.", recovery=True)
        if not self.active or token != self.token:
            raise HTTPException(409, "Harness session ended; enable again explicitly")

    def heartbeat(self, request):
        self.authenticate(request.token)
        self.heartbeat_until = self.clock() + 3
        return self.status()

    def lookup(self, operation_id):
        saved = self.store.get(f"harness:operation:{operation_id}")
        if not saved:
            raise HTTPException(404, "Unknown harness operation")
        return HarnessOperation.model_validate(saved)

    def submit(self, request):
        self.authenticate(request.token)
        existing = self.store.get(f"harness:operation:{request.operation_id}")
        if existing:
            result = HarnessOperation.model_validate(existing)
            if result.steps != request.steps:
                raise HTTPException(409, "Operation ID already used for different steps")
            return result
        if self.operation and self.operation.state == "running":
            raise HTTPException(409, "A harness sequence is running")
        if self.submissions >= 20:
            self.stop("Session operation budget exhausted", recovery=True)
            raise HTTPException(409, self.message)
        targets = []
        estimated = 0.0
        positions = dict(self.manual.targets)
        for step in request.steps:
            joint = step.joint_id if step.action == "move_joint" else "claw"
            target = step.target_pulse_us if step.action == "move_joint" else self.claw[step.action]
            self.validate_target(joint, target)
            rate = min(
                100, next(j.max_rate_us_per_second for j in self.manual.joints if j.id == joint)
            )
            estimated += abs(target - positions[joint]) / rate
            positions[joint] = target
            targets.append((joint, target, rate))
        if estimated > 55 or estimated > self.deadline - self.clock():
            raise HTTPException(
                422, "Sequence exceeds remaining session or 60-second motion budget"
            )
        self.manual.check_current()
        self.operation = HarnessOperation(
            operation_id=request.operation_id,
            state="running",
            steps=request.steps,
            simulated=self.manual.simulated,
            message="Executing; positions are commanded, not measured",
        )
        self.persist()  # Durable intent before any physical output.
        self.targets = targets
        self.started = self.updated = self.clock()
        self.submissions += 1
        return self.operation

    def persist(self):
        if self.operation:
            self.operation.commanded = dict(self.manual.commanded)
            self.store.put_many(
                {
                    f"harness:operation:{self.operation.operation_id}": self.operation.model_dump(
                        mode="json"
                    ),
                    "harness:active_operation": str(self.operation.operation_id)
                    if self.operation.state == "running"
                    else None,
                }
            )

    def stop(self, message="Harness ended; monitored holding retained.", *, recovery=False):
        self.token = None
        self.message = message
        self.manual.hold(message, recovery=recovery)
        if self.operation and self.operation.state == "running":
            self.operation.state = "stopped"
            self.operation.message = message
            self.persist()
        if self.store:
            self.store.put("harness:session_active", False)

    def tick(self):
        if not self.active:
            return
        m = self.manual
        if not m.prepared or m.recovery_required or m.monitor.fault:
            self.stop("Arm/current fault; cut physical servo power and inspect.", recovery=True)
            return
        if self.clock() >= min(self.deadline, self.heartbeat_until):
            self.stop("Operator session expired; inspect and recover.", recovery=True)
            return
        if not self.operation or self.operation.state != "running":
            return
        if self.clock() - self.started >= 60:
            self.stop("Sequence timed out; inspect and recover.", recovery=True)
            return
        try:
            m.monitor.require()
            joint, target, rate = self.targets[self.operation.completed_steps]
            elapsed = max(0, min(self.clock() - self.updated, 0.02))
            self.updated = self.clock()
            previous = m.targets[joint]
            delta = max(-rate * elapsed, min(rate * elapsed, target - previous))
            pose = {**m.targets, joint: previous + delta}
            pulses = m.plan.encode(pose)
            if any(m.commanded.get(p.joint) != p.commanded_us for p in pulses):
                if m.output:
                    pulses = m.output.write_pose(pose)
                m.commanded = {p.joint: p.commanded_us for p in pulses}
            m.targets = pose
            if abs(pose[joint] - target) < 1e-8:
                self.operation.completed_steps += 1
                if self.operation.completed_steps == len(self.targets):
                    self.operation.state = "completed"
                    self.operation.message = (
                        "Targets commanded; physical arrival and grip unverified"
                    )
                self.persist()
        except Exception:
            # Outer arm worker latches the fault and attempts shutdown of every output.
            self.operation.state = "failed"
            self.operation.message = "Execution failed; cut physical servo power and inspect"
            self.token = None
            self.persist()
            raise

    def status(self, bounds=None):
        limits = self.limits if self.active or bounds is None else self.bounds(bounds)
        joints = [
            j.model_copy(
                update={
                    "min_us": limits[j.id][0],
                    "max_us": limits[j.id][1],
                    "max_rate_us_per_second": min(100, j.max_rate_us_per_second),
                    "commanded_us": self.manual.commanded.get(j.id),
                }
            )
            for j in self.manual.joints
            if j.id in limits
        ]
        return HarnessArmStatus(
            active=self.active,
            simulated=self.manual.simulated,
            prepared=self.manual.prepared,
            recovery_required=self.manual.recovery_required,
            message=self.message,
            joints=joints,
            claw_current=self.manual.monitor.status(),
            operation=self.operation,
            remaining_seconds=max(0, self.deadline - self.clock()) if self.active else 0,
        )
