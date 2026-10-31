"""Restricted single-channel trial output. No measured position feedback."""

import math
import time

from .assembly_profile import intersect_limits
from .device_lock import DeviceLock
from .movement import ClearanceResult, MovementRejected, MovementSession

MIN_US = 1450
MAX_US = 1550
START_US = 1500
STEP_US = 10
PRESCALE = 121  # Approximately 50 Hz with the nominal 25 MHz internal oscillator.
TICK_US = (PRESCALE + 1) * 1_000_000 / 25_000_000


def encode_pulse(pulse_us, minimum=MIN_US, maximum=MAX_US):
    if (
        type(pulse_us) not in (int, float)
        or not math.isfinite(pulse_us)
        or not minimum <= pulse_us <= maximum
    ):
        raise ValueError(f"Requested pulse must be within {minimum}-{maximum} us")
    if math.ceil(minimum / TICK_US) > math.floor(maximum / TICK_US):
        raise ValueError("Pulse limits contain no representable PCA9685 tick")
    # Keep quantized output inside the trial window too.
    ticks = max(
        math.ceil(minimum / TICK_US), min(math.floor(maximum / TICK_US), round(pulse_us / TICK_US))
    )
    return ticks, ticks * TICK_US


def prepare(bus, address):
    """Disable every output before changing the shared PWM frequency."""
    if bus.read_byte_data(address, 0x00) & 0x40:
        raise ValueError("External clock enabled; refusing nominal internal-clock setup")
    bus.write_byte_data(address, 0xFD, 0x10)
    # ALL_LED full-off propagates to the individual channel registers.
    for channel in range(16):
        if not bus.read_byte_data(address, 0x09 + 4 * channel) & 0x10:
            raise ValueError(f"Could not verify channel {channel} full-off")
    bus.write_byte_data(address, 0x00, 0x30)  # Sleep + auto-increment, no group addresses.
    bus.write_byte_data(address, 0x01, 0x04)
    bus.write_byte_data(address, 0xFE, PRESCALE)
    bus.write_byte_data(address, 0x00, 0x20)
    time.sleep(0.005)
    if (
        bus.read_byte_data(address, 0x00) & 0x7F != 0x20
        or bus.read_byte_data(address, 0x01) != 0x04
        or bus.read_byte_data(address, 0xFE) != PRESCALE
    ):
        raise ValueError("PWM configuration readback failed")


def write_pulse(bus, address, pulse_us, channel=5, *, minimum=MIN_US, maximum=MAX_US):
    """Legacy neutral-only primitive. Other movement requires a complete guarded session."""
    encode_pulse(pulse_us, minimum, maximum)
    if pulse_us != START_US:
        raise MovementRejected(ClearanceResult(False, "use_guarded_all_holding_movement"))
    return _write_pulse(bus, address, pulse_us, channel, minimum=minimum, maximum=maximum)


def _write_pulse(bus, address, pulse_us, channel=5, *, minimum=MIN_US, maximum=MAX_US):
    if type(channel) is not int or not 0 <= channel <= 5:
        raise ValueError("Trial channel must be 0–5")
    minimum, maximum = intersect_limits(channel, minimum, maximum)
    ticks, actual_us = encode_pulse(pulse_us, minimum, maximum)
    register = 0x06 + 4 * channel
    data = [0, 0, ticks & 0xFF, ticks >> 8]
    bus.write_i2c_block_data(address, register, data)
    for offset, expected in enumerate(data):
        if bus.read_byte_data(address, register + offset) != expected:
            raise ValueError(f"Channel {channel} pulse register readback failed")
    return actual_us


class SingleServoOutput:
    def __init__(
        self, bus_number=1, address=0x40, *, bus_factory=None, movement=None, check_current=None
    ):
        if (
            type(bus_number) is not int
            or bus_number < 0
            or type(address) is not int
            or not 0x40 <= address <= 0x77
            or address == 0x70
        ):
            raise ValueError("Invalid PCA9685 bus or address")
        self.lock = DeviceLock(bus_number, address)
        self.closed = False
        self.faulted = False
        self.address = address
        self.channel = None
        self.movement = movement if movement is not None else MovementSession()
        self.check_current = check_current
        self.movement.check_current = self._require_current
        try:
            if bus_factory is None:
                from smbus2 import SMBus

                bus_factory = SMBus
            self.bus = bus_factory(bus_number)
        except Exception:
            self.lock.close()
            raise

    def prepare(self, channel):
        if type(channel) is not int or not 0 <= channel <= 5:
            raise ValueError("Trial channel must be 0–5")
        if self.closed or self.faulted or self.channel is not None:
            raise RuntimeError("Output requires a fresh power-off preparation")
        try:
            prepare(self.bus, self.address)
            self.channel = channel
            if channel == 0:
                self._require_current()
            result = write_pulse(self.bus, self.address, START_US, channel)
            if channel == 0:
                self._require_current()
            return result
        except Exception:
            self.faulted = True
            self.movement.invalidate()
            try:
                self.disable()
            except Exception:
                pass
            raise

    def write(self, pulse_us):
        if self.closed or self.faulted or self.channel is None:
            raise RuntimeError("Trial output unavailable; inspect and prepare again")
        return self.write_joint(self.channel, pulse_us, MIN_US, MAX_US)

    def _require_current(self):
        if self.check_current is None:
            raise RuntimeError("Claw current monitor required for powered claw output")
        self.check_current()

    def prepare_all(self):
        if self.check_current is None:
            raise RuntimeError("Claw current monitor required for all-holding startup")
        return self.initialize_neutral(self.check_current)

    def initialize_neutral(self, check_current):
        """Explicit operator initialization of all six outputs, with live ammeter checks."""
        if self.closed or self.faulted or self.channel is not None:
            raise RuntimeError("Fresh output owner required for neutral initialization")
        # Retain this monitor for subsequent movement as well as startup.
        if self.check_current is None:
            self.check_current = check_current
        targets = {}

        def dispatch():
            check_current()
            prepare(self.bus, self.address)
            self.channel = 5
            for channel, name in enumerate(
                ("claw", "joint_5", "joint_4", "joint_3", "joint_2", "base")
            ):
                check_current()
                targets[name] = _write_pulse(self.bus, self.address, START_US, channel)
                check_current()

        try:
            self.movement.move(
                dict.fromkeys(range(6), START_US),
                dict.fromkeys(range(6), encode_pulse(START_US)[1]),
                dispatch,
            )
            return targets
        except MovementRejected:
            raise
        except Exception:
            self.faulted = True
            self.movement.invalidate()
            try:
                self.disable()
            except Exception:
                pass
            raise

    def write_joint(self, channel, target, minimum, maximum):
        if self.closed or self.faulted or self.channel is None:
            raise RuntimeError("Trial output unavailable")
        minimum, maximum = intersect_limits(channel, minimum, maximum)
        actual = encode_pulse(target, minimum, maximum)[1]

        def dispatch():
            self._require_current()
            _write_pulse(self.bus, self.address, target, channel, minimum=minimum, maximum=maximum)
            self._require_current()

        try:
            self.movement.move({channel: target}, {channel: actual}, dispatch)
            return actual
        except MovementRejected:
            raise
        except Exception:
            self.faulted = True
            self.movement.invalidate()
            try:
                self.disable()
            except Exception:
                pass
            raise

    def disable(self):
        self.movement.invalidate()
        # Caller has confirmed disconnected servo power and supported links.
        self.bus.write_byte_data(self.address, 0xFD, 0x10)
        for channel in range(16):
            if not self.bus.read_byte_data(self.address, 0x09 + 4 * channel) & 0x10:
                raise OSError("Could not verify all output registers off")

    def close(self):
        # Stop/shutdown retains PWM. Only acknowledged finish calls disable().
        if not self.closed:
            self.closed = True
            self.movement.invalidate()
            try:
                self.bus.close()
            finally:
                self.lock.close()
