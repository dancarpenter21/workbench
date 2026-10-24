"""Continuous INA219 interlock. Raw shunt voltage is never treated as calibrated amperes."""

import math
import time
from uuid import uuid4

from workbench_contracts import ClawCurrentStatus, OffCurrentBounds, now


def read_sample(bus, address, shunt_ohms=None):
    def word(register):
        value = bus.read_word_data(address, register)
        if type(value) is not int or not 0 <= value <= 0xFFFF:
            raise ValueError("Invalid INA219 register reading")
        return ((value & 0xFF) << 8) | (value >> 8)

    if word(0) & 7 != 7:
        raise ValueError("INA219 must be in continuous shunt and bus conversion mode")
    shunt = word(1)
    if shunt & 0x8000:
        shunt -= 0x10000
    voltage = word(2)
    if not voltage & 2 or voltage & 1:
        raise ValueError("INA219 conversion unavailable or overflow")
    shunt_v = shunt * 0.00001
    return {
        "bus_v": (voltage >> 3) * 0.004,
        "shunt_mv": shunt_v * 1000,
        "current_a": None if shunt_ohms is None else shunt_v / shunt_ohms,
    }


def check_current(sample, limit=2.5):
    for name in ("bus_v", "shunt_mv"):
        if not math.isfinite(sample[name]):
            raise ValueError("Invalid INA219 reading")
    current = sample["current_a"]
    if current is not None and (not math.isfinite(current) or abs(current) >= limit):
        raise ValueError(f"Current limit reached or invalid: {current} A; cutoff {limit} A")


class ClawCurrentMonitor:
    """Called under the arm I/O lock, including while no browser owns motion."""

    def __init__(self, config, *, clock=time.monotonic, bus_factory=None):
        self.simulated = config["mode"] == "mock"
        self.enabled = self.simulated or config["arm"].get("backend") == "pca9685"
        self.bus_number = config["arm"].get("i2c_bus", 1)
        self.pwm_address = config["arm"].get("i2c_address", 0x40)
        self.session_id = str(uuid4())
        self.sample_sequence = 0
        self.clock = clock
        self.factory = bus_factory
        self.bus = None
        self.store = None
        self.stream = "claw:service"
        self.last = None
        self.last_attempt = None
        self.sample = {}
        self.sampled_at = None
        self.fault = ""
        self.shutdown_required = False
        settings = config["arm"].get("claw_current", {})
        self.off_bounds = None
        self.off_baseline_error = ""
        if settings.get("off_baseline") is not None:
            try:
                bounds = OffCurrentBounds.model_validate(settings["off_baseline"])
                if (
                    bounds.i2c_bus != self.bus_number
                    or bounds.pwm_address != self.pwm_address
                    or bounds.domain != ("simulated" if self.simulated else "physical")
                ):
                    raise ValueError("Off baseline does not match this sensor setup/domain")
                self.off_bounds = bounds
            except ValueError as exc:
                self.off_baseline_error = str(exc)
        self.shunt = settings.get("shunt_ohms")
        self.evidence = settings.get("verification_evidence", "")
        self.verified = settings.get("scale_verified") is True
        self.config_error = False
        if self.verified and (
            type(self.shunt) not in (int, float)
            or not math.isfinite(self.shunt)
            or self.shunt <= 0
            or not isinstance(self.evidence, str)
            or not self.evidence.strip()
        ):
            self.config_error = True
            self.trip("Verified current scale requires positive shunt resistance and evidence")
        if not self.verified:
            self.shunt = None

    def attach(self, store):
        self.store = store
        previous = store.get("claw_current_fault", "")
        if previous and not self.simulated:
            self.fault = previous
            self.shutdown_required = True

    def trip(self, reason):
        if not self.fault:
            self.shutdown_required = True
            self.fault = f"{reason}. CUT PHYSICAL SERVO POWER; support the arm."

    def poll(self, force=False):
        if not self.enabled:
            return
        if not force and self.last_attempt is not None and self.clock() - self.last_attempt < 0.1:
            return
        started = self.clock()
        self.last_attempt = started
        try:
            if self.simulated:
                sample = {"bus_v": 5.0, "shunt_mv": 0.0, "current_a": None}
            else:
                if self.bus is None:
                    if self.factory is None:
                        from smbus2 import SMBus

                        self.factory = SMBus
                    self.bus = self.factory(self.bus_number)
                sample = read_sample(self.bus, 0x41, self.shunt)
            self.sample = sample
            self.sampled_at = now()
            self.sample_sequence += 1
            check_current(sample)
            if self.clock() - started >= 0.3:
                raise ValueError("INA219 read exceeded freshness deadline")
            self.last = started
            if self.store:
                payload = self.status().model_dump(mode="json")
                self.store.append(self.stream, payload)
            return True
        except Exception as exc:
            self.trip(str(exc))
            return False

    def record_fault(self):
        """Call after attempting PWM shutdown; disk I/O must not delay that attempt."""
        if self.store:
            try:
                self.store.put("claw_current_fault", self.fault)
                self.store.append(self.stream, self.status().model_dump(mode="json"))
            except Exception:
                pass  # Keep the fault latched even when durable logging is unavailable.

    def require(self):
        if self.simulated and (self.last is None or self.clock() - self.last >= 0.3):
            self.poll(force=True)
        if self.fault:
            raise RuntimeError(self.fault)
        if self.last is None or self.clock() - self.last >= 0.3:
            self.trip("Missing or stale INA219 reading")
        if self.fault:
            raise RuntimeError(self.fault)

    def prepare(self):
        """Explicit power-off preparation is the only way to clear a fault."""
        if self.config_error or not self.poll(force=True):
            raise RuntimeError(self.fault)
        if self.last is None or self.clock() - self.last >= 0.3:
            raise RuntimeError(self.fault or "INA219 unavailable")
        check_current(self.sample)
        if not self.simulated and self.sample["bus_v"] > 0.5:
            raise RuntimeError("Claw supply present; disconnect ALL servo power before preparation")
        # A failed poll (including logging) must not be cleared with an older sample.
        if self.sampled_at is None or self.last < self.clock() - 0.3:
            raise RuntimeError(self.fault)
        if self.store:
            self.store.put("claw_current_fault", "")
        self.fault = ""
        self.shutdown_required = False

    def status(self):
        age = None if self.last is None else max(0, self.clock() - self.last)
        healthy = not self.fault and age is not None and age < 0.3
        power_state = "unknown"
        if healthy and self.off_bounds:
            power_state = (
                "off"
                if 0 <= self.sample["bus_v"] <= self.off_bounds.max_bus_v
                and (
                    abs(self.sample["shunt_mv"]) <= self.off_bounds.max_abs_shunt_mv
                    or math.isclose(
                        abs(self.sample["shunt_mv"]),
                        self.off_bounds.max_abs_shunt_mv,
                        rel_tol=0,
                        abs_tol=1e-12,
                    )
                )
                else "energized"
            )
        return ClawCurrentStatus(
            monitor_session_id=self.session_id,
            sample_sequence=self.sample_sequence,
            i2c_bus=None if self.simulated else self.bus_number,
            sensor_address=0x41,
            pwm_address=None if self.simulated else self.pwm_address,
            power_state=power_state,
            off_bounds=self.off_bounds,
            off_baseline_error=self.off_baseline_error,
            simulated=self.simulated,
            healthy=healthy,
            fault=self.fault,
            sampled_at=self.sampled_at,
            age_seconds=age,
            scale_verified=self.verified,
            shunt_ohms=self.shunt if not self.config_error else None,
            verification_evidence=self.evidence if isinstance(self.evidence, str) else "",
            **self.sample,
        )

    def close(self):
        if self.bus:
            self.bus.close()
            self.bus = None
