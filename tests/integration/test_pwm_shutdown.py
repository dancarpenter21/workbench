"""Shutdown writes use injected fake buses only; no physical arm is touched."""

import pytest
from workbench_arm_controller.pwm_shutdown import shutdown_pwm


class ShutdownBus:
    def __init__(self, failed_channel=None):
        self.writes = []
        self.reads = []
        self.failed_channel = failed_channel
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def write_byte_data(self, address, register, value):
        self.writes.append((address, register, value))

    def read_byte_data(self, address, register):
        self.reads.append((address, register))
        return (
            0
            if register == 9 + 4 * (self.failed_channel or 0) and self.failed_channel is not None
            else 16
        )


def test_shutdown_only_writes_full_off_and_verifies_every_channel():
    bus = ShutdownBus()
    config = {"mode": "hardware", "arm": {"backend": "pca9685", "i2c_bus": 987}}
    assert shutdown_pwm(config, bus_factory=lambda number: bus)
    assert bus.writes == [(0x40, 0xFD, 0x10)]
    assert bus.reads == [(0x40, 9 + 4 * channel) for channel in range(16)]
    assert bus.closed


def test_full_off_verification_failure_is_reported():
    bus = ShutdownBus(failed_channel=3)
    config = {"mode": "hardware", "arm": {"backend": "pca9685", "i2c_bus": 987}}
    with pytest.raises(OSError, match="CUT SERVO POWER"):
        shutdown_pwm(config, bus_factory=lambda number: bus)
    assert bus.closed


def test_mock_shutdown_never_opens_i2c():
    config = {"mode": "mock", "arm": {"backend": "pca9685"}}
    assert not shutdown_pwm(config, bus_factory=lambda number: pytest.fail("Mock opened I2C"))
