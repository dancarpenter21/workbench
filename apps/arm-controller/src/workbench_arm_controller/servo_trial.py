"""Restricted single-channel trial output. No measured position feedback."""

import math
import time

from .device_lock import DeviceLock

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
    if type(channel) is not int or not 0 <= channel <= 5:
        raise ValueError("Trial channel must be 0–5")
    ticks, actual_us = encode_pulse(pulse_us, minimum, maximum)
    register = 0x06 + 4 * channel
    data = [0, 0, ticks & 0xFF, ticks >> 8]
    bus.write_i2c_block_data(address, register, data)
    for offset, expected in enumerate(data):
        if bus.read_byte_data(address, register + offset) != expected:
            raise ValueError(f"Channel {channel} pulse register readback failed")
    return actual_us


class SingleServoOutput:
    def __init__(self, bus_number=1, address=0x40, *, bus_factory=None):
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
            return self.write(START_US)
        except Exception:
            self.faulted = True
            raise

    def write(self, pulse_us):
        if self.closed or self.faulted or self.channel is None:
            raise RuntimeError("Trial output unavailable; inspect and prepare again")
        try:
            return write_pulse(self.bus, self.address, pulse_us, self.channel)
        except Exception:
            self.faulted = True
            raise

    def prepare_all(self):
        if self.closed or self.faulted or self.channel is not None:
            raise RuntimeError("Fresh power-off preparation required")
        prepare(self.bus, self.address)
        self.channel = 5
        return {
            name: self.write_joint(index, START_US, MIN_US, MAX_US)
            for index, name in enumerate(
                ("claw", "joint_5", "joint_4", "joint_3", "joint_2", "base")
            )
        }

    def write_joint(self, channel, target, minimum, maximum):
        if self.closed or self.faulted or self.channel is None:
            raise RuntimeError("Trial output unavailable")
        try:
            return write_pulse(
                self.bus, self.address, target, channel, minimum=minimum, maximum=maximum
            )
        except Exception:
            self.faulted = True
            raise

    def disable(self):
        # Caller has confirmed disconnected servo power and supported links.
        self.bus.write_byte_data(self.address, 0xFD, 0x10)
        for channel in range(16):
            if not self.bus.read_byte_data(self.address, 0x09 + 4 * channel) & 0x10:
                raise OSError("Could not verify all output registers off")

    def close(self):
        # Stop/shutdown retains PWM. Only acknowledged finish calls disable().
        if not self.closed:
            self.closed = True
            try:
                self.bus.close()
            finally:
                self.lock.close()
