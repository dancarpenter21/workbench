"""Continuous INA219 interlock. Raw shunt voltage is never treated as calibrated amperes."""

import math
import time
from queue import Empty, Full, Queue
from threading import Event, Thread
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


def reading_is_healthy(sample):
    """Reading validity is independent of a previously latched motion interlock."""
    return (
        sample is not None
        and (sample.reading_healthy if sample.reading_healthy is not None else sample.healthy)
        and sample.age_seconds is not None
        and 0 <= sample.age_seconds < 0.3
        and all(
            value is not None and math.isfinite(value) for value in (sample.bus_v, sample.shunt_mv)
        )
    )


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
        self.telemetry = Queue(maxsize=256)
        self.logging_stop = Event()
        self.logging_thread = None
        self.logging_error = ""
        self.read_seconds = 0.0
        self.sample_gap_seconds = 0.0
        self.worker_delay_seconds = 0.0
        self.lock_wait_seconds = 0.0
        self.persistence_seconds = 0.0
        self.stream = "claw:service"
        self.last = None
        self.last_attempt = None
        self.last_read_ok = False
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

    def start_logging(self):
        """Persist samples outside the arm I/O lock; queue overflow is a fault."""
        self.logging_thread = Thread(target=self._log_samples, daemon=True)
        self.logging_thread.start()

    def _log_samples(self):
        while not self.logging_stop.is_set() or not self.telemetry.empty():
            try:
                stream, payload = self.telemetry.get(timeout=0.1)
            except Empty:
                continue
            started = self.clock()
            try:
                self.store.append(stream, payload)
            except Exception as exc:
                self.logging_error = f"Current telemetry persistence failed: {exc}"
            finally:
                self.persistence_seconds = self.clock() - started
                self.telemetry.task_done()

    def trip(self, reason):
        if not self.fault:
            self.shutdown_required = True
            self.fault = f"{reason}. CUT PHYSICAL SERVO POWER; support the arm."

    def poll(self, force=False):
        if not self.enabled:
            return
        if self.logging_error:
            self.trip(self.logging_error)
            return False
        if not force and self.last_attempt is not None and self.clock() - self.last_attempt < 0.1:
            return
        self.last_read_ok = False
        started = self.clock()
        self.last_attempt = started
        try:
            if self.simulated:
                sample = {
                    "bus_v": 5.0,
                    "shunt_mv": 0.0,
                    "current_a": 0.0 if self.shunt is not None else None,
                }
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
            self.read_seconds = self.clock() - started
            self.sample_gap_seconds = 0.0 if self.last is None else started - self.last
            self.last = started
            self.last_read_ok = True
            if self.store:
                payload = self.status().model_dump(mode="json")
                if self.logging_thread:
                    try:
                        self.telemetry.put_nowait((self.stream, payload))
                    except Full as exc:
                        raise RuntimeError("Current telemetry queue full") from exc
                else:
                    self.store.append(self.stream, payload)
            return True
        except Exception as exc:
            self.last_read_ok = False
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
        """Clear a fault as part of disconnected-power output preparation."""
        self._clear_fault(require_power_off=True)

    def reset_fault(self):
        """Explicit operator reset with a fresh valid sample; never commands output."""
        self._clear_fault(require_power_off=False)

    def _clear_fault(self, *, require_power_off):
        if self.config_error or not self.poll(force=True):
            raise RuntimeError(self.fault)
        if self.last is None or self.clock() - self.last >= 0.3:
            raise RuntimeError(self.fault or "INA219 unavailable")
        check_current(self.sample)
        if (
            require_power_off
            and not self.simulated
            and (
                self.off_baseline_error
                or (self.off_bounds and self.status().power_state != "off")
                or (not self.off_bounds and self.sample["bus_v"] > 0.5)
            )
        ):
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
        reading_healthy = self.last_read_ok and age is not None and age < 0.3
        healthy = not self.fault and reading_healthy
        power_state = "unknown"
        if reading_healthy and self.off_bounds:
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
        elif reading_healthy and self.simulated:
            power_state = "off" if self.sample["shunt_mv"] == 0 else "energized"
        reading_state = (
            "unavailable"
            if self.last_attempt is None
            else "error"
            if not self.last_read_ok
            else "stale"
            if not reading_healthy
            else "ok"
        )
        return ClawCurrentStatus(
            read_seconds=self.read_seconds,
            sample_gap_seconds=self.sample_gap_seconds,
            worker_delay_seconds=self.worker_delay_seconds,
            lock_wait_seconds=self.lock_wait_seconds,
            persistence_seconds=self.persistence_seconds,
            telemetry_pending=self.telemetry.qsize(),
            reading_state=reading_state,
            reset_required=bool(self.fault),
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
            reading_healthy=reading_healthy,
            fault=self.fault,
            sampled_at=self.sampled_at,
            age_seconds=age,
            scale_verified=self.verified,
            shunt_ohms=self.shunt if not self.config_error else None,
            verification_evidence=self.evidence if isinstance(self.evidence, str) else "",
            **self.sample,
        )

    def close(self):
        self.logging_stop.set()
        if self.logging_thread:
            self.logging_thread.join(timeout=2)
        if self.bus:
            self.bus.close()
            self.bus = None
