"""Operator-observed single-servo trials; PWM readback is not position feedback."""

import json
import math
import time
from uuid import uuid4

from fastapi import HTTPException
from workbench_contracts import (
    CommissionDraft,
    CommissionEvent,
    CommissionExport,
    CommissionJoint,
    CommissionObservation,
    CommissionSession,
    CommissionSetup,
    CommissionStatus,
    now,
)

from .claw_current import ClawCurrentMonitor
from .manual import JOINT_ORDER
from .servo_trial import SingleServoOutput, encode_pulse


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
        self.available = config.get("profile", "workbench") == "workbench" and (
            self.simulated
            or (
                config["arm"].get("backend") == "pca9685"
                and config["arm"].get("commissioning_enabled") is True
            )
        )
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
        self.trial = store.get(self.prefix + "trial")
        if self.trial:
            self.trial.update(
                stage="awaiting_power_off",
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
        draft.revision += 1
        self.store.put(self.prefix + "draft:" + draft.id, draft.model_dump(mode="json"))
        return draft

    def setup(self):
        return CommissionSetup(
            backend="mock" if self.simulated else "pca9685",
            i2c_bus=None if self.simulated else self.config["arm"].get("i2c_bus", 1),
            i2c_address=None if self.simulated else self.config["arm"].get("i2c_address", 0x40),
        )

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
            claw_current=self.monitor.status(),
            available=self.available,
            simulated=self.simulated,
            reserved=self.reserved,
            **(self.trial or {}),
            message=(
                f"Trial settings: 1500 us initial, {window[0]}–{window[1]} us selected window, at most 10 us per step. "
                "Physical position unknown."
                if self.available
                else "Commissioning disabled: select PCA9685 and enable arm.commissioning_enabled."
            ),
        )

    def acquire(self, request):
        if not self.available or self.reserved:
            raise HTTPException(409, "Commissioning unavailable or arm reserved")
        draft = self.read(request.draft_id)
        self.check_setup(draft)
        joint = self.joint(draft, request.joint_id)
        for item in draft.joints if request.trial_type == "all_holding" else [joint]:
            item.state = "in_progress"
            # A fresh preparation cannot silently reuse observations.
            item.observations = {}
        self.save(draft)
        self.trial = dict(
            trial_type=request.trial_type,
            targets={},
            encoded_targets={},
            windows={name: [1450, 1550] for name in JOINT_ORDER},
            trial_id=str(uuid4()),
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
        arm = self.config["arm"]
        return self.factory(arm.get("i2c_bus", 1), arm.get("i2c_address", 0x40))

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
        if (
            self.trial.get("trial_type") != "all_holding"
            or request.joint_id != self.trial["joint_id"]
        ):
            raise HTTPException(409, "Select a joint in all-servos-holding mode first")
        return self.apply_target(request.pulse_us)

    def step(self, request):
        self.authenticate(request, "ready")
        if request.direction not in (-1, 1):
            raise HTTPException(422, "Step direction must be -1 or 1")
        return self.apply_target(self.trial["requested_us"] + request.direction * 10)

    def apply_target(self, target):
        if self.clock() - self.last_step < 0.25:
            raise HTTPException(429, "Wait at least 250 ms between steps")
        joint = self.trial["joint_id"]
        low, high = self.trial.get("windows", {}).get(joint, [1450, 1550])
        if (
            not math.isfinite(target)
            or not low <= target <= high
            or abs(target - self.trial["requested_us"]) > 10
        ):
            raise HTTPException(
                422, "Target must be inside trial window and within 10 us of current request"
            )
        try:
            self.monitor.require()
            self.event("step_intent", f"Requested {target} us")
            self.monitor.require()
            self.last_step = self.clock()
            if self.simulated:
                actual = encode_pulse(target, low, high)[1]
            elif self.trial.get("trial_type") == "all_holding":
                actual = self.output.write_joint(5 - JOINT_ORDER.index(joint), target, low, high)
            else:
                actual = self.output.write(target)
            self.trial.update(requested_us=target, encoded_us=actual)
            self.trial.setdefault("targets", {})[joint] = target
            self.trial.setdefault("encoded_targets", {})[joint] = actual
            self.persist()
            self.event(
                "step_result", f"Requested {target} us; encoded {actual} us; position unmeasured"
            )
        except Exception as exc:
            self.fault(exc)
        return self.status()

    def mark(self, request):
        self.authenticate(request, "ready")
        if request.expected_requested_us != self.trial["requested_us"]:
            raise HTTPException(409, "Requested target changed; observe again before marking")
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
        return self.status()

    def stop(self, reason="Operator stopped trial; last PWM target retained"):
        if self.trial and self.trial["stage"] != "awaiting_power_off":
            self.token = None
            self.trial["stage"] = "awaiting_power_off"
            self.persist()
            self.event("stop", reason)
        return self.status()

    def stop_trial(self, request):
        self.match_trial(request.trial_id)
        return self.stop()

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
            complete = len(joint.observations) == 3 and bool(joint.direction.strip())
            joint.state = (
                "observed"
                if complete and request.observations_reviewed and not faulted
                else "needs_review"
            )
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
        channels = {}
        for joint in draft.joints:
            channels[joint.id] = dict(
                channel=joint.channel,
                label=joint.id,
                direction=joint.direction,
                max_rate_us_per_second=None,
                **{
                    key: (
                        joint.observations[mark].requested_us
                        if mark in joint.observations
                        else None
                    )
                    for key, mark in [
                        ("min_us", "minimum"),
                        ("max_us", "maximum"),
                        ("initial_us", "initial"),
                    ]
                },
            )
        return CommissionExport(
            record=draft,
            telemetry=self.store.journal(self.prefix + "samples:" + draft.id),
            manual_config=dict(
                enabled=False,
                commissioned=False,
                evidence=f"{self.domain} draft {draft.id} revision {draft.revision}; individual observations only",
                channels=channels,
            ),
        )

    def close(self):
        self.stop("Service shutdown; attempting PWM shutdown; disconnect physical servo power")
        if self.output:
            try:
                self.output.disable()
            finally:
                self.output.close()
                self.output = None
