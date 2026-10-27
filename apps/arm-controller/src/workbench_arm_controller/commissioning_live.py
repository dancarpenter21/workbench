"""Operator-observed single-servo trials; PWM readback is not position feedback."""

import json
import math
import time
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from workbench_contracts import (
    CommissionAttempt,
    CommissionBounds,
    CommissionChecklist,
    CommissionDraft,
    CommissionEvent,
    CommissionExport,
    CommissionFeedbackRecord,
    CommissionJoint,
    CommissionJointBounds,
    CommissionObservation,
    CommissionPulseBound,
    CommissionSession,
    CommissionSetup,
    CommissionStatus,
    now,
)

from .claw_current import ClawCurrentMonitor
from .geometry_setup import GeometrySetup
from .manual import JOINT_ORDER
from .movement import MovementRejected, MovementSession
from .robot_model_setup import RobotModelSetup
from .servo_trial import TICK_US, SingleServoOutput, encode_pulse


class LiveCommissioning:
    """Called under the shared arm lock. Persist intent before every hardware write."""

    def __init__(
        self, config, *, clock=time.monotonic, output_factory=SingleServoOutput, monitor=None
    ):
        self.monitor = monitor or ClawCurrentMonitor(config, clock=clock)
        self.config = config
        self.clock = clock
        self.factory = output_factory
        self.simulated = config["mode"] == "mock"
        self.domain = "simulated" if self.simulated else "physical"
        self.available = self.simulated or config["arm"].get("backend") == "pca9685"
        self.prefix = "commissioning:" + self.domain + ":"
        self.trial = None
        self.token = None
        self.output = None
        self.sequence = -1
        self.deadline = 0
        self.last_step = float("-inf")

    @property
    def reserved(self):
        return self.trial is not None

    def attach(self, store):
        self.store = store
        self.geometry = GeometrySetup(self.config, store, self.bounds_context, self.bounds_key)
        self.robot_model = RobotModelSetup(self.config, store, self.bounds_context, self.bounds_key)
        self.trial = store.get(self.prefix + "trial")
        if self.trial:
            draft = self.read(self.trial["draft_id"])
            for joint in draft.joints:
                if (
                    self.trial.get("trial_type") == "all_holding"
                    or joint.id == self.trial["joint_id"]
                ):
                    if not any(a.trial_id == self.trial["trial_id"] for a in joint.attempts):
                        joint.attempts.append(
                            CommissionAttempt(
                                id=str(uuid4()),
                                trial_id=self.trial["trial_id"],
                                joint_id=joint.id,
                                observer=draft.observer,
                                conditions="Interrupted legacy trial; setup observations in history",
                                observations=dict(joint.observations),
                                direction=joint.direction,
                                notes=joint.notes,
                            )
                        )
            self.save(draft)
            self.trial.update(
                stage="awaiting_power_off",
                resume_stage=None,
                requested_us=None,
                encoded_us=None,
                targets={},
                encoded_targets={},
            )
            self.event(
                "interrupted",
                "Controller restarted; output state unknown; power-off finish required",
            )
            self.persist()

    def persist(self):
        self.store.put(self.prefix + "trial", self.trial)

    def drafts(self):
        return [self.read(key) for key in self.store.get(self.prefix + "drafts", [])]

    def read(self, draft_id):
        data = self.store.get(self.prefix + "draft:" + draft_id)
        if not data:
            raise HTTPException(404, "Commissioning draft not found in this domain")
        return CommissionDraft.model_validate(data)

    def save(self, draft):
        if self.trial and self.trial["draft_id"] == draft.id:
            for joint in draft.joints:
                for attempt in joint.attempts:
                    if attempt.trial_id == self.trial["trial_id"]:
                        attempt.observations = dict(joint.observations)
                        attempt.direction = joint.direction
                        attempt.notes = joint.notes
                        attempt.state = joint.state if joint.state != "untested" else "needs_review"
        draft.revision += 1
        self.store.put(self.prefix + "draft:" + draft.id, draft.model_dump(mode="json"))
        return draft

    def setup(self):
        return CommissionSetup(
            backend="mock" if self.simulated else "pca9685",
            i2c_bus=None if self.simulated else self.config["arm"].get("i2c_bus", 1),
            i2c_address=None if self.simulated else self.config["arm"].get("i2c_address", 0x40),
        )

    def bounds_context(self):
        assembly = getattr(self.monitor, "active_assembly_id", None) or "Assembly not named"
        if self.trial:
            assembly = self.read(self.trial["draft_id"]).assembly_id
        return CommissionBounds(
            domain=self.domain,
            assembly_id=assembly,
            setup=self.setup(),
            joints={name: CommissionJointBounds() for name in JOINT_ORDER},
        )

    def bounds_key(self, context):
        identity = json.dumps(
            [context.assembly_id, context.setup.model_dump(mode="json")], sort_keys=True
        )
        return self.prefix + "bounds:" + sha256(identity.encode()).hexdigest()

    def bounds(self):
        context = self.bounds_context()
        saved = self.store.get(self.bounds_key(context))
        return CommissionBounds.model_validate(saved) if saved else context

    def set_bounds(self, request):
        context = self.bounds_context()
        if request.assembly_id != context.assembly_id or request.setup != context.setup:
            raise HTTPException(409, "Arm setup changed; reload the values before saving")
        if not request.joints or not set(request.joints) <= set(JOINT_ORDER):
            raise HTTPException(422, "Provide limits for at least one known joint")
        context = self.bounds()
        edit_id = str(uuid4())
        for name, limits in request.joints.items():
            endpoints = context.joints[name]
            if limits.clear_minimum:
                endpoints.minimum = None
            if limits.clear_maximum:
                endpoints.maximum = None
            low = (
                limits.minimum_us
                if limits.minimum_us is not None
                else (endpoints.minimum.requested_us if endpoints.minimum else 500)
            )
            high = (
                limits.maximum_us
                if limits.maximum_us is not None
                else (endpoints.maximum.requested_us if endpoints.maximum else 2500)
            )
            if low >= high:
                raise HTTPException(422, f"{name}: minimum must be below maximum")
            if math.ceil(low / TICK_US) > math.floor(high / TICK_US):
                raise HTTPException(422, f"{name}: limits must contain a representable PWM pulse")
            for kind, value in (("minimum", limits.minimum_us), ("maximum", limits.maximum_us)):
                if value is not None:
                    setattr(
                        endpoints,
                        kind,
                        CommissionPulseBound(
                            requested_us=value,
                            encoded_us=encode_pulse(value, low, high)[1],
                            observed_at=now(),
                            trial_id="",
                            command_id=edit_id,
                            source="manual",
                        ),
                    )
        self.store.put(self.bounds_key(context), context.model_dump(mode="json"))
        return context

    def checklist(self):
        key = self.bounds_key(self.bounds_context()) + ":checklist"
        saved = self.store.get(key)
        return (
            CommissionChecklist.model_validate(saved)
            if saved
            else CommissionChecklist(context_key=key)
        )

    def update_checklist(self, request):
        if self.reserved:
            raise HTTPException(409, "Finish the powered trial before recording checklist reviews")
        checklist = self.checklist()
        if request.context_key != checklist.context_key:
            raise HTTPException(409, "Hardware setup changed; reload the checklist")
        checklist.completed[request.step] = request.completed
        checklist.updated_at = now()
        self.store.put(checklist.context_key, checklist.model_dump(mode="json"))
        return checklist

    @property
    def max_move_us(self):
        return 40 if not self.trial or self.trial.get("trial_type") == "all_holding" else 10

    def confirmed_target(self):
        if not self.trial or self.trial["stage"] != "ready":
            return None
        return self.trial.get("confirmed_commands", {}).get(self.trial["joint_id"])

    def check_setup(self, draft):
        if draft.setup != self.setup() and not (self.simulated and draft.setup is None):
            raise HTTPException(
                409,
                "Restore the recorded I2C setup, or create a new record after finishing the current trial",
            )

    def create(self, request):
        draft = CommissionDraft(
            id=str(uuid4()),
            domain=self.domain,
            assembly_id=request.assembly_id,
            observer=request.observer,
            setup=self.setup(),
            joints=[CommissionJoint(id=name, channel=5 - i) for i, name in enumerate(JOINT_ORDER)],
        )
        self.store.put_many(
            {
                self.prefix + "draft:" + draft.id: draft.model_dump(mode="json"),
                self.prefix + "drafts": [*self.store.get(self.prefix + "drafts", []), draft.id],
            }
        )
        return draft

    def joint(self, draft, joint_id=None):
        joint_id = joint_id or self.trial["joint_id"]
        for joint in draft.joints:
            if joint.id == joint_id:
                return joint
        raise HTTPException(422, "Unknown joint")

    def edit(self, draft_id, request):
        draft = self.read(draft_id)
        if request.revision != draft.revision:
            raise HTTPException(409, "Record changed; reload before saving")
        joint = self.joint(draft, request.joint_id)
        joint.direction, joint.notes = request.direction, request.notes
        joint.selected_attempt_id = None
        if joint.state == "observed":
            joint.state = "needs_review"
        draft.history.append(
            CommissionEvent(
                at=now(),
                joint_id=joint.id,
                action="edit",
                detail=json.dumps(
                    {
                        "direction": joint.direction,
                        "notes": joint.notes,
                        "state_after_edit": joint.state,
                    }
                ),
            )
        )
        return self.save(draft)

    def event(self, action, detail):
        draft = self.read(self.trial["draft_id"])
        joint = self.joint(draft)
        if action in ("fault", "interrupted"):
            joint.state = "needs_review"
        draft.history.append(
            CommissionEvent(
                at=now(),
                trial_id=self.trial["trial_id"],
                joint_id=joint.id,
                action=action,
                detail=detail,
            )
        )
        self.save(draft)
        self.store.append(
            self.prefix + "events:" + draft.id,
            {
                "at": now().isoformat(),
                "trial_id": self.trial["trial_id"],
                "joint_id": joint.id,
                "action": action,
                "detail": detail,
            },
        )

    def status(self):
        window = (
            (self.trial or {})
            .get("windows", {})
            .get((self.trial or {}).get("joint_id"), [1450, 1550])
        )
        return CommissionStatus(
            capture_command_id=(self.confirmed_target() or {}).get("command_id"),
            can_resume_powered=(self.trial or {}).get("resume_stage")
            in ("ready", "awaiting_feedback")
            and self.monitor.status().healthy,
            max_move_us=self.max_move_us,
            capture_ready=self.confirmed_target() is not None,
            claw_current=self.monitor.status(),
            available=self.available,
            simulated=self.simulated,
            reserved=self.reserved,
            **{
                key: value
                for key, value in (self.trial or {}).items()
                if key not in ("confirmed_commands", "resume_stage")
            },
            message=(
                f"Trial settings: 1500 us initial, {window[0]}–{window[1]} us selected window, at most {self.max_move_us} us per move. "
                "V1 position is assumed from successful PWM writes, not measured."
                if self.available
                else "Commissioning requires the PCA9685 backend."
            ),
        )

    def acquire(self, request):
        if not self.available or self.reserved:
            raise HTTPException(409, "Commissioning unavailable or arm reserved")
        draft = self.read(request.draft_id)
        self.check_setup(draft)
        active_assembly = getattr(self.monitor, "active_assembly_id", None)
        if active_assembly and active_assembly != draft.assembly_id:
            raise HTTPException(409, "Active current verification belongs to another assembly")
        joint = self.joint(draft, request.joint_id)
        trial_id = str(uuid4())
        for item in draft.joints if request.trial_type == "all_holding" else [joint]:
            item.state = "in_progress"
            # A fresh preparation cannot silently reuse observations.
            item.observations = {}
            item.attempts.append(
                CommissionAttempt(
                    id=str(uuid4()),
                    trial_id=trial_id,
                    joint_id=item.id,
                    observer=request.observer or draft.observer,
                    conditions=request.conditions,
                    current_scale=self.monitor.status().model_dump(mode="json"),
                    telemetry_reference=self.prefix + "samples:" + draft.id,
                )
            )
        self.save(draft)
        self.trial = dict(
            trial_type=request.trial_type,
            targets={},
            encoded_targets={},
            windows={name: [1450, 1550] for name in JOINT_ORDER},
            trial_id=trial_id,
            draft_id=draft.id,
            joint_id=joint.id,
            stage="acquired",
            requested_us=None,
            encoded_us=None,
        )
        self.monitor.stream = self.prefix + "samples:" + draft.id
        self.token = str(uuid4())
        self.sequence = -1
        self.deadline = self.clock() + 0.3
        self.last_step = float("-inf")
        self.persist()
        self.event(
            "acquire",
            f"{request.trial_type} reserved; no outputs programmed; setup "
            + json.dumps(
                draft.setup.model_dump(mode="json")
                if draft.setup
                else {"legacy_setup": "not recorded"}
            ),
        )
        return CommissionSession(token=self.token, status=self.status())

    def initialize(self, request):
        """A user Move starts a new all-holding session at the established neutral target.

        This records output commands, never a claim that measured position has settled.
        Existing sensor faults must be explicitly reset with power disconnected.
        """

        def check_current():
            if not self.monitor.poll(force=True):
                raise RuntimeError(self.monitor.fault)
            self.monitor.require()

        check_current()
        if self.reserved:
            if self.token or self.trial["stage"] != "awaiting_power_off":
                raise HTTPException(409, "Arm is busy; stop the current session first")
            self.event(
                "superseded",
                "Operator requested fresh neutral initialization after an interrupted session; "
                "previous physical position and observations remain unverified",
            )
            if self.output:
                self.output.close()
                self.output = None
            self.trial = None
            self.persist()
            check_current()
        self.acquire(request.model_copy(update={"trial_type": "all_holding"}))
        try:
            self.event(
                "neutral_intent", "User requested initialization of all six servos at 1500 us"
            )
            self.trial["stage"] = "awaiting_power_off"
            self.persist()
            check_current()
            if self.simulated:
                encoded = {name: encode_pulse(1500)[1] for name in JOINT_ORDER}
            else:
                self.output = self.open_output()
                encoded = self.output.initialize_neutral(check_current)
            check_current()
            self.trial.update(
                stage="ready",
                targets=dict.fromkeys(JOINT_ORDER, 1500),
                encoded_targets=encoded,
                requested_us=1500,
                encoded_us=encoded[self.trial["joint_id"]],
            )
            self.event(
                "neutral_result", "All six neutral targets written; physical position unmeasured"
            )
            self.persist()
            self.deadline = self.clock() + 0.3
        except Exception as exc:
            self.fault(exc)
        return CommissionSession(token=self.token, status=self.status())

    def tick(self):
        if self.token and self.clock() >= self.deadline:
            self.stop("Heartbeat expired; last PWM target retained")

    def authenticate(self, request, stage=None):
        self.tick()
        if not self.token or request.token != self.token or request.sequence <= self.sequence:
            raise HTTPException(409, "Expired, stale, or wrong commissioning session")
        self.sequence = request.sequence
        self.deadline = self.clock() + 0.3
        if stage and self.trial["stage"] != stage:
            raise HTTPException(409, "Trial is not in the required stage")

    def open_output(self):
        self.geometry.apply_active()
        arm = self.config["arm"]
        return self.factory(
            arm.get("i2c_bus", 1),
            arm.get("i2c_address", 0x40),
            movement=MovementSession.from_config(self.config),
            check_current=self.check_current,
        )

    def check_current(self):
        self.monitor.poll()
        self.monitor.require()

    def emergency_disable(self):
        if self.output:
            self.output.disable()

    def fault(self, exc):
        self.token = None
        try:
            self.emergency_disable()
        except Exception as shutdown:
            exc = RuntimeError(f"{exc}; PWM shutdown failed: {shutdown}; CUT SERVO POWER")
        self.trial.update(
            stage="awaiting_power_off",
            resume_stage=None,
            requested_us=None,
            encoded_us=None,
            targets={},
            encoded_targets={},
        )
        self.persist()
        self.event("fault", str(exc))
        raise HTTPException(
            503, f"{exc}. Output state unknown; CUT PHYSICAL SERVO POWER and finish trial"
        ) from exc

    def prepare(self, request):
        self.authenticate(request, "acquired")
        if (
            self.trial.get("trial_type") != "all_holding"
            and not request.power_off_supported_isolated
        ):
            raise HTTPException(422, "Isolated trial requires power isolation acknowledgment")
        self.event(
            "prepare_intent",
            f"Operator confirms power off, supported links, clearance; {self.trial.get('trial_type', 'isolated')} trial",
        )
        self.trial["stage"] = "awaiting_power_off"
        self.persist()
        try:
            self.monitor.prepare()
            all_holding = self.trial.get("trial_type") == "all_holding"
            names = JOINT_ORDER if all_holding else (self.trial["joint_id"],)
            if not self.simulated:
                self.output = self.open_output()
                self.monitor.require()
                if all_holding:
                    encoded = self.output.prepare_all()
                else:
                    encoded = {names[0]: self.output.prepare(5 - JOINT_ORDER.index(names[0]))}
            else:
                encoded = {name: encode_pulse(1500)[1] for name in names}
            self.trial.update(targets={name: 1500 for name in names}, encoded_targets=encoded)
            actual = encoded[self.trial["joint_id"]]
            self.trial.update(stage="prepared", requested_us=1500, encoded_us=actual)
            self.event(
                "prepare_result",
                f"Programmed {list(self.trial['targets'])}: requested 1500 us, encoded {actual} us",
            )
            self.persist()
            self.deadline = self.clock() + 0.3
        except Exception as exc:
            self.fault(exc)
        return self.status()

    def ready(self, request):
        self.authenticate(request, "prepared")
        self.monitor.require()
        self.trial["stage"] = "ready"
        self.event("ready", "Operator applied selected servo power and observed settled, clear arm")
        self.persist()
        return self.status()

    def heartbeat(self, request):
        self.authenticate(request)
        return self.status()

    def select(self, request):
        self.authenticate(request, "ready")
        if self.trial.get("trial_type") != "all_holding":
            raise HTTPException(409, "Joint switching requires all-servos-holding mode")
        self.joint(self.read(self.trial["draft_id"]), request.joint_id)
        self.trial.update(
            joint_id=request.joint_id,
            requested_us=self.trial["targets"][request.joint_id],
            encoded_us=self.trial["encoded_targets"][request.joint_id],
        )
        self.event("select", request.joint_id)
        self.persist()
        return self.status()

    def window(self, request):
        self.authenticate(request, "ready")
        if (
            self.trial.get("trial_type") != "all_holding"
            or request.joint_id != self.trial["joint_id"]
        ):
            raise HTTPException(409, "Select a joint in all-servos-holding mode first")
        low, high = request.min_us, request.max_us
        from .servo_trial import TICK_US

        if (
            not 500 <= low < high <= 2500
            or not low <= self.trial["requested_us"] <= high
            or not low <= self.trial["encoded_us"] <= high
            or math.ceil(low / TICK_US) > math.floor(high / TICK_US)
        ):
            raise HTTPException(422, "Window must contain current target and representable ticks")
        self.event(
            "window",
            json.dumps(
                {"joint_id": request.joint_id, "min_us": low, "max_us": high, "reviewed": True}
            ),
        )
        self.trial["windows"][request.joint_id] = [low, high]
        self.persist()
        return self.status()

    def target(self, request):
        self.authenticate(request, "ready")
        if request.joint_id != self.trial["joint_id"]:
            raise HTTPException(409, "Select the requested joint first")
        return self.apply_target(request.pulse_us, assume_ok=request.assume_ok)

    def position(self, request):
        """Explore pulses independently of saved Jog limits, with current monitoring."""
        self.select(request)
        return self.apply_target(request.pulse_us, assume_ok=True, direct_limits=(500, 2500))

    def step(self, request):
        self.authenticate(request, "ready")
        if request.direction not in (-1, 1):
            raise HTTPException(422, "Step direction must be -1 or 1")
        return self.apply_target(self.trial["requested_us"] + request.direction * 10)

    def apply_target(self, target, *, assume_ok=False, direct_limits=None):
        if self.clock() - self.last_step < 0.25:
            raise HTTPException(429, "Wait at least 250 ms between steps")
        joint = self.trial["joint_id"]
        low, high = self.trial.get("windows", {}).get(joint, [1450, 1550])
        if direct_limits is not None:
            low, high = direct_limits
        if (
            not math.isfinite(target)
            or not low <= target <= high
            or (
                direct_limits is None
                and abs(target - self.trial["requested_us"]) > self.max_move_us
            )
        ):
            raise HTTPException(
                422,
                f"Target must be inside {low}–{high} us"
                if direct_limits is not None
                else f"Target must be inside trial window and within {self.max_move_us} us of current request",
            )
        # Geometry rejection is a normal refusal, not an output fault. Check before
        # persisting intent or changing the currently held/confirmed command.
        if not self.simulated:
            channel = 5 - JOINT_ORDER.index(joint)
            try:
                result = self.output.movement.check(
                    {channel: target}, {channel: encode_pulse(target, low, high)[1]}
                )
            except Exception as exc:
                self.fault(exc)
            if not result.accepted:
                self.event("movement_rejected", str(MovementRejected(result)))
                raise HTTPException(409, str(MovementRejected(result)))
        try:
            self.monitor.require()
            self.trial.setdefault("confirmed_commands", {}).pop(joint, None)
            pending = dict(
                issued_at=now().isoformat(),
                command_id=str(uuid4()),
                joint_id=joint,
                previous_us=self.trial["requested_us"],
                requested_us=target,
            )
            self.trial.update(stage="awaiting_feedback", pending_command=pending)
            self.persist()
            self.event("step_intent", json.dumps(pending))
            self.monitor.require()
            self.last_step = self.clock()
            if self.simulated:
                actual = encode_pulse(target, low, high)[1]
            elif self.trial.get("trial_type") == "all_holding":
                actual = self.output.write_joint(5 - JOINT_ORDER.index(joint), target, low, high)
            else:
                actual = self.output.write(target)
            pending["encoded_us"] = actual
            pending["written_at"] = now().isoformat()
            self.trial.update(requested_us=target, encoded_us=actual)
            self.trial.setdefault("targets", {})[joint] = target
            self.trial.setdefault("encoded_targets", {})[joint] = actual
            self.persist()
            self.event("step_result", json.dumps({**pending, "physical_position": "unmeasured"}))
            if assume_ok and self.trial.get("trial_type") == "all_holding":
                self.monitor.require()
                self.event(
                    "assumed_ok",
                    json.dumps({**pending, "physical_observation": "not recorded"}),
                )
                self.trial.setdefault("confirmed_commands", {})[joint] = dict(pending)
                self.trial.update(stage="ready", pending_command=None)
                self.persist()
        except Exception as exc:
            self.fault(exc)
        return self.status()

    def feedback(self, request):
        self.authenticate(request)
        pending = self.trial.get("pending_command") or {}
        if self.trial["stage"] == "ready" and request.outcome == "unsafe":
            pending = self.confirmed_target() or {}
        elif self.trial["stage"] != "awaiting_feedback":
            raise HTTPException(409, "No movement awaiting feedback")
        if (
            request.command_id != pending.get("command_id")
            or request.joint_id != pending.get("joint_id")
            or request.expected_requested_us != pending.get("requested_us")
        ):
            raise HTTPException(409, "Feedback does not match the pending command")
        if not request.physical_frame.strip() or (
            request.outcome != "settled_clear" and not request.notes.strip()
        ):
            raise HTTPException(422, "Physical frame and notes for unresolved outcomes required")
        if request.outcome == "uncertain":
            self.token = None
            self.trial["stage"] = "awaiting_power_off"
        # Unsafe feedback inhibits output before attempting record storage.
        if request.outcome == "unsafe":
            self.monitor.trip("Operator reported binding or unsafe movement")
            try:
                self.emergency_disable()
            finally:
                self.token = None
                self.trial["stage"] = "awaiting_power_off"
        try:
            draft = self.read(self.trial["draft_id"])
            joint = self.joint(draft)
            record = CommissionFeedbackRecord(
                **pending,
                outcome=request.outcome,
                observed_direction=request.observed_direction,
                physical_frame=request.physical_frame,
                notes=request.notes,
            )
            attempt = next(a for a in joint.attempts if a.trial_id == self.trial["trial_id"])
            attempt.feedback.append(record)
            if request.outcome != "settled_clear":
                joint.state = "needs_review"
            self.save(draft)
            self.event("feedback", record.model_dump_json())
            self.trial["pending_command"] = None
            if request.outcome == "settled_clear":
                self.monitor.require()
                self.trial.setdefault("confirmed_commands", {})[request.joint_id] = dict(pending)
                self.trial["stage"] = "ready"
            elif request.outcome == "uncertain":
                self.event(
                    "stop", "Uncertain movement; inspect, support and disconnect servo power"
                )
            else:
                self.event("fault", "Unsafe movement reported; CUT PHYSICAL SERVO POWER")
            self.persist()
        except Exception as exc:
            self.fault(exc)
        return self.status()

    def select_attempt(self, draft_id, request):
        if self.reserved:
            raise HTTPException(409, "Finish the active trial before selecting an attempt")
        draft = self.read(draft_id)
        if request.revision != draft.revision:
            raise HTTPException(409, "Record changed; reload before selecting")
        joint = self.joint(draft, request.joint_id)
        attempt = next((a for a in joint.attempts if a.id == request.attempt_id), None)
        if not attempt or attempt.state != "observed" or attempt.ended_at is None:
            raise HTTPException(422, "Select a completed reviewed attempt")
        joint.selected_attempt_id = attempt.id
        joint.observations = dict(attempt.observations)
        joint.direction, joint.notes, joint.state = attempt.direction, attempt.notes, "observed"
        draft.history.append(
            CommissionEvent(at=now(), joint_id=joint.id, action="select_attempt", detail=attempt.id)
        )
        return self.save(draft)

    def mark(self, request):
        self.authenticate(request, "ready")
        self.monitor.require()
        if request.expected_requested_us != self.trial["requested_us"]:
            raise HTTPException(409, "Requested target changed; observe again before marking")
        bounds = None
        if self.trial.get("trial_type") == "all_holding" and request.kind != "initial":
            confirmed = self.confirmed_target()
            if (
                not confirmed
                or confirmed["requested_us"] != request.expected_requested_us
                or request.joint_id != self.trial["joint_id"]
                or request.command_id != confirmed["command_id"]
            ):
                raise HTTPException(409, "Confirm this joint's current command with OK first")
            bounds = self.bounds()
            endpoints = bounds.joints[self.trial["joint_id"]]
            setattr(
                endpoints,
                request.kind,
                CommissionPulseBound(
                    requested_us=confirmed["requested_us"],
                    encoded_us=confirmed["encoded_us"],
                    observed_at=now(),
                    trial_id=self.trial["trial_id"],
                    command_id=confirmed["command_id"],
                ),
            )
            if (
                endpoints.minimum
                and endpoints.maximum
                and endpoints.minimum.requested_us >= endpoints.maximum.requested_us
            ):
                raise HTTPException(422, "Saved minimum must be below maximum")
        draft = self.read(self.trial["draft_id"])
        joint = self.joint(draft)
        joint.observations[request.kind] = CommissionObservation(
            requested_us=self.trial["requested_us"],
            encoded_us=self.trial["encoded_us"],
            observed_at=now(),
            notes=request.notes,
        )
        marks = joint.observations
        lo, hi, initial = (marks.get(k) for k in ("minimum", "maximum", "initial"))
        if (
            (lo and hi and lo.requested_us >= hi.requested_us)
            or (initial and lo and initial.requested_us < lo.requested_us)
            or (initial and hi and initial.requested_us > hi.requested_us)
        ):
            raise HTTPException(422, "Observed minimum must be below maximum and contain initial")
        self.save(draft)
        self.event(
            "mark",
            json.dumps(
                {
                    "kind": request.kind,
                    "settled_and_clear": True,
                    "observation": joint.observations[request.kind].model_dump(mode="json"),
                }
            ),
        )
        if bounds:
            try:
                self.store.put(self.bounds_key(bounds), bounds.model_dump(mode="json"))
            except Exception as exc:
                self.fault(exc)
        return self.status()

    def stop(self, reason="Operator stopped trial; last PWM target retained"):
        if self.trial and self.trial["stage"] != "awaiting_power_off":
            self.trial["resume_stage"] = (
                self.trial["stage"]
                if self.trial.get("trial_type") == "all_holding"
                and self.trial["stage"] in ("ready", "awaiting_feedback")
                else None
            )
            self.token = None
            self.trial["stage"] = "awaiting_power_off"
            self.persist()
            self.event("stop", reason)
        return self.status()

    def stop_trial(self, request):
        self.match_trial(request.trial_id)
        return self.stop()

    def resume(self, request):
        """Explicitly reacquire retained targets without rewriting PWM or neutral."""
        self.match_trial(request.trial_id)
        stage = self.trial.get("resume_stage")
        if self.trial["stage"] != "awaiting_power_off" or stage not in (
            "ready",
            "awaiting_feedback",
        ):
            raise HTTPException(409, "Output state unknown; powered resume unavailable")
        self.check_setup(self.read(self.trial["draft_id"]))
        try:
            if not self.monitor.poll(force=True):
                raise RuntimeError(self.monitor.fault)
            self.monitor.require()
        except Exception as exc:
            self.fault(exc)
        self.event("resume", "Operator resumed powered trial; existing PWM targets retained")
        self.trial.update(stage=stage, resume_stage=None)
        self.token = str(uuid4())
        self.sequence = -1
        self.deadline = self.clock() + 0.3
        self.persist()
        return CommissionSession(token=self.token, status=self.status())

    def match_trial(self, trial_id):
        if not self.trial or self.trial["trial_id"] != trial_id:
            raise HTTPException(409, "Trial no longer current")

    def finish(self, request):
        self.match_trial(request.trial_id)
        if self.trial["stage"] != "awaiting_power_off":
            raise HTTPException(409, "Stop trial before power-off finish")
        self.check_setup(self.read(self.trial["draft_id"]))
        self.event(
            "finish_intent", "Operator confirms servo power disconnected and links supported"
        )
        try:
            if not self.simulated:
                if not self.output:
                    self.output = self.open_output()
                self.output.disable()
                self.output.close()
                self.output = None
        except Exception as exc:
            self.fault(exc)
        draft = self.read(self.trial["draft_id"])
        faulted = any(
            e.trial_id == self.trial["trial_id"] and e.action in ("fault", "interrupted")
            for e in draft.history
        )
        joints = (
            draft.joints if self.trial.get("trial_type") == "all_holding" else [self.joint(draft)]
        )
        for joint in joints:
            attempt = next(a for a in joint.attempts if a.trial_id == self.trial["trial_id"])
            unresolved = any(f.outcome != "settled_clear" for f in attempt.feedback)
            pending = self.trial.get("pending_command")
            complete = (
                len(joint.observations) == 3
                and bool(joint.direction.strip())
                and not unresolved
                and not (pending and pending["joint_id"] == joint.id)
            )
            joint.state = (
                "observed"
                if complete
                and (
                    joint.id in request.reviewed_joint_ids
                    or (request.observations_reviewed and len(joints) == 1)
                )
                and not faulted
                else "needs_review"
            )
            attempt.ended_at = now()
        self.save(draft)
        self.event(
            "finish_result",
            "Outputs disabled; observations saved. Combined arm pose and rates unqualified.",
        )
        self.trial = None
        self.token = None
        self.persist()
        return self.status()

    def export(self, draft_id):
        draft = self.read(draft_id)
        return CommissionExport(
            record=draft,
            telemetry=self.store.journal(self.prefix + "samples:" + draft.id),
        )

    def close(self):
        self.stop("Service shutdown; attempting PWM shutdown; disconnect physical servo power")
        if self.output:
            try:
                self.output.disable()
            finally:
                self.output.close()
                self.output = None
