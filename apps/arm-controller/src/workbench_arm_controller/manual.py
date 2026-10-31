"""Single-owner manual pulse jogging. Commands are never measured positions."""

import math
import time
from uuid import uuid4

from fastapi import HTTPException
from workbench_contracts import ManualJoint, ManualSession, ManualStatus

from .assembly_profile import intersect_limits
from .claw_current import ClawCurrentMonitor
from .movement import MovementRejected, MovementSession
from .pca9685 import Pca9685Output, PulsePlan

JOINT_ORDER = ("base", "joint_2", "joint_3", "joint_4", "joint_5", "claw")
WATCHDOG_SECONDS = 0.3


def mock_configuration():
    return {
        "channels": {
            name: {
                "channel": 5 - index,
                "label": "Base" if index == 0 else "Claw" if index == 5 else f"Joint {index + 1}",
                "min_us": 1000,
                "max_us": 2000,
                "initial_us": 1500,
                "max_rate_us_per_second": 100,
                "direction": "Positive increases pulse width (simulated)",
            }
            for index, name in enumerate(JOINT_ORDER)
        },
    }


def free_play_configuration():
    config = mock_configuration()
    for joint in config["channels"].values():
        low, high = intersect_limits(joint["channel"], 500, 2500)
        joint.update(min_us=low, max_us=high, direction="Positive increases pulse width")
    return config


class ManualControl:
    """All methods are serialized by the arm application's motion lock."""

    def __init__(self, config, *, clock=time.monotonic, monitor=None):
        self.monitor = monitor or ClawCurrentMonitor(config, clock=clock)
        self.config = config
        self.simulated = config["mode"] == "mock"
        self.clock = clock
        self.token = None
        self.sequence = -1
        self.speed = 0.0
        self.joint = None
        self.deadline = 0.0
        self.updated = clock()
        self.output = None
        self.prepared = False
        self.recovery_required = False
        self.error = ""
        self.message = "Enable manual control to jog one joint at a time."
        self.targets = {}
        self.commanded = {}
        self.joints = []
        self.plan = None
        self.movement = MovementSession(free_play=True)
        try:
            if not self.simulated and config["arm"].get("backend") != "pca9685":
                raise ValueError("Manual control requires the PCA9685 backend")
            manual = free_play_configuration()
            if not isinstance(manual, dict):
                raise ValueError("Manual configuration must be a JSON object")
            channels = manual["channels"]
            if set(channels) != set(JOINT_ORDER):
                raise ValueError(
                    "Provide the six manual joint identifiers in the documented mapping"
                )
            self.calibration = {"calibrated": True, "channels": channels}
            self.plan = PulsePlan(self.calibration)
            for index, name in enumerate(JOINT_ORDER):
                data = channels[name]
                if data["channel"] != 5 - index:
                    raise ValueError("Manual wiring must run from base channel 5 to claw channel 0")
                rate = data["max_rate_us_per_second"]
                if isinstance(rate, bool) or not math.isfinite(rate) or not 0 < rate <= 1000:
                    raise ValueError("Measured manual pulse rate must be in (0, 1000] us/s")
                if not isinstance(data["label"], str) or not data["label"].strip():
                    raise ValueError("Joint label required")
                self.targets[name] = data["initial_us"]
                if data["initial_us"] != 1500:
                    raise ValueError("The established startup is 1500 us on every channel")
                self.joints.append(
                    ManualJoint(
                        id=name,
                        **{
                            k: data[k]
                            for k in (
                                "label",
                                "channel",
                                "min_us",
                                "max_us",
                                "max_rate_us_per_second",
                                "direction",
                            )
                        },
                    )
                )
            self.plan.encode(self.targets)
            if self.simulated:
                self.prepared = True
                self.commanded = {p.joint: p.commanded_us for p in self.plan.encode(self.targets)}
            else:
                self.message = (
                    "Disconnect servo power and support the arm before preparing outputs."
                )
        except (KeyError, TypeError, ValueError) as exc:
            self.error = f"Manual control unavailable: {exc}"
            self.joints = []
            self.targets = {}

    def status(self, *, busy=False, recovery=False):
        message = self.error or self.message
        if busy:
            message = (
                "The arm is reserved; finish the current operation before enabling manual control."
            )
        if recovery or self.recovery_required:
            message = (
                self.error
                or self.message + " Inspect the arm and recover before enabling manual control."
            )
        return ManualStatus(
            claw_current=self.monitor.status(),
            simulated=self.simulated,
            available=not (self.error or busy or recovery or self.recovery_required),
            prepared=self.prepared,
            active=self.token is not None,
            recovery_required=recovery or self.recovery_required,
            message=message
            + " Free play: table-clearance checks are off; current monitoring remains active.",
            joints=[
                j.model_copy(update={"commanded_us": self.commanded.get(j.id)}) for j in self.joints
            ],
        )

    def prepare(self):
        if self.error:
            raise HTTPException(409, self.error)
        if self.token:
            raise HTTPException(409, "End manual control before preparing outputs")
        if self.simulated:
            self.monitor.prepare()
            self.targets = {
                name: data["initial_us"] for name, data in self.calibration["channels"].items()
            }
            self.commanded = {p.joint: p.commanded_us for p in self.plan.encode(self.targets)}
            self.prepared = True
            self.recover()
            return
        # Caller has explicitly confirmed disconnected servo power and supported arm.
        if self.output:
            self.output.close()
            self.output = None
        self.prepared = False
        self.commanded = {}
        try:
            self.monitor.prepare()
            self.movement = MovementSession(free_play=True)
            self.output = Pca9685Output(
                self.calibration,
                bus_number=self.config["arm"].get("i2c_bus", 1),
                address=self.config["arm"].get("i2c_address", 0x40),
                movement=self.movement,
                check_current=self.check_current,
            )
            self.targets = {
                name: data["initial_us"] for name, data in self.calibration["channels"].items()
            }
            self.monitor.require()
            pulses = self.output.write_pose(self.targets)
            self.commanded = {p.joint: p.commanded_us for p in pulses}
            self.prepared = True
            self.recovery_required = False
            self.message = (
                "Initial targets programmed. Apply power using the commissioned procedure."
            )
        except Exception as exc:
            self.fault(f"Preparation failed: {exc}. Use the physical cutoff and inspect the arm.")
            raise HTTPException(503, self.message) from exc

    def acquire(self):
        if self.token:
            raise HTTPException(409, "Manual control is owned by another session")
        if not self.status().available or not self.prepared:
            raise HTTPException(409, self.error or "Prepare outputs and recover before enabling")
        self.token = uuid4()
        self.sequence = -1
        self.speed = 0.0
        self.joint = None
        self.updated = self.clock()
        self.deadline = self.updated + WATCHDOG_SECONDS
        self.message = "Manual control enabled. Hold enable and a direction to jog."
        return ManualSession(token=self.token, status=self.status())

    def authenticate(self, token):
        if not self.token or token != self.token:
            raise HTTPException(409, "Manual session ended; enable again explicitly")
        if self.clock() >= self.deadline and self.speed != 0:
            self.hold("Manual input timed out; inspect the arm and recover.", recovery=True)
            raise HTTPException(409, self.message)

    def input(self, value):
        self.authenticate(value.token)
        if value.sequence <= self.sequence:
            raise HTTPException(409, "Stale manual input sequence")
        if value.joint_id not in self.targets:
            raise HTTPException(422, "Unknown manual joint")
        if self.joint != value.joint_id and value.speed != 0:
            raise HTTPException(409, "Select the joint with neutral input before jogging")
        # Account for the old input only up to arrival; never apply a new input retroactively.
        self.tick()
        if self.token is None:
            raise HTTPException(409, self.message)
        self.sequence = value.sequence
        self.joint = value.joint_id
        self.speed = value.speed
        self.deadline = self.clock() + WATCHDOG_SECONDS

    def position(self, value):
        self.authenticate(value.token)
        if value.sequence <= self.sequence:
            raise HTTPException(409, "Stale manual input sequence")
        joint = next((j for j in self.joints if j.id == value.joint_id), None)
        if joint is None or not joint.min_us <= value.pulse_us <= joint.max_us:
            raise HTTPException(422, "Invalid manual pulse target")
        self.speed = 0.0
        self.joint = value.joint_id
        self.sequence = value.sequence
        self.deadline = self.clock() + WATCHDOG_SECONDS
        targets = {**self.targets, value.joint_id: value.pulse_us}
        self.write_direct(targets)

    def reset(self, value):
        self.authenticate(value.token)
        self.speed = 0.0
        self.joint = None
        self.deadline = self.clock() + WATCHDOG_SECONDS
        self.write_direct(dict.fromkeys(self.targets, 1500))

    def write_direct(self, targets):
        try:
            self.monitor.require()
            pulses = self.output.write_pose(targets) if self.output else self.plan.encode(targets)
            self.commanded = {p.joint: p.commanded_us for p in pulses}
            self.targets = targets
        except Exception as exc:
            self.emergency_disable()
            self.fault(f"{exc}. Cut physical servo power and prepare again.")
            raise HTTPException(503, self.message) from exc

    def hold(self, message="Manual control ended; last PWM targets retained.", *, recovery=False):
        self.token = None
        self.speed = 0.0
        self.joint = None
        self.recovery_required |= recovery
        self.message = message

    def fault(self, message):
        self.movement.invalidate()
        self.hold(message, recovery=True)
        self.commanded = {}
        self.prepared = False

    def check_current(self):
        self.monitor.poll()
        self.monitor.require()

    def tick(self):
        now = self.clock()
        elapsed = max(0, min(now - self.updated, 0.02))
        self.updated = now
        if self.output and self.prepared:
            try:
                self.monitor.require()
            except RuntimeError as exc:
                self.emergency_disable()
                self.fault(str(exc))
                return
        if not self.token:
            return
        if now >= self.deadline and self.speed != 0:
            self.hold("Manual input timed out; inspect the arm and recover.", recovery=True)
            return
        if not self.speed or not self.joint:
            return
        joint = next(j for j in self.joints if j.id == self.joint)
        targets = dict(self.targets)
        targets[joint.id] = max(
            joint.min_us,
            min(
                joint.max_us,
                self.targets[joint.id] + self.speed * joint.max_rate_us_per_second * elapsed,
            ),
        )
        try:
            pulses = self.plan.encode(targets)
            if any(self.commanded.get(p.joint) != p.commanded_us for p in pulses):
                if self.output:
                    pulses = self.output.write_pose(targets)
                self.commanded = {p.joint: p.commanded_us for p in pulses}
            self.targets = targets
        except MovementRejected as exc:
            self.hold(str(exc) + ". Last PWM targets retained.")
        except Exception as exc:
            self.fault(f"{exc}. Output state unknown; use the physical cutoff and prepare again.")

    def recover(self):
        if not self.prepared or self.error:
            raise HTTPException(
                409, self.error or "Disconnect servo power and prepare outputs again"
            )
        self.recovery_required = False
        self.message = "Inspection acknowledged. Enable manual control explicitly."

    def emergency_disable(self):
        if self.output:
            self.output.disable()

    def close(self):
        self.hold()
        if self.output:
            self.output.close()
