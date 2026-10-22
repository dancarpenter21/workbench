"""Fake-bus tests only; never connect to I2C."""

import pytest
from workbench_arm_controller.base_servo_test import encode_pulse, prepare, write_pulse


class FakeBus:
    def __init__(self):
        self.registers = {0: 0x11}
        self.writes = []

    def read_byte_data(self, address, register):
        return self.registers.get(register, 0)

    def write_byte_data(self, address, register, value):
        self.writes.append((register, value))
        self.registers[register] = value
        if register == 0xFD:
            for channel in range(16):
                self.registers[0x09 + 4 * channel] = value

    def write_i2c_block_data(self, address, register, data):
        for offset, value in enumerate(data):
            self.write_byte_data(address, register + offset, value)


@pytest.mark.parametrize("pulse", [1450, 1500, 1550])
def test_quantized_trial_stays_inside_window(pulse):
    ticks, actual = encode_pulse(pulse)
    assert 0 < ticks < 4096
    assert 1450 <= actual <= 1550


@pytest.mark.parametrize("pulse", [1449, 1551, float("nan"), float("inf")])
def test_out_of_window_never_writes(pulse):
    bus = FakeBus()
    with pytest.raises(ValueError):
        write_pulse(bus, 0x40, pulse)
    assert not bus.writes


def test_prepare_disables_all_channels_before_frequency_change():
    bus = FakeBus()
    prepare(bus, 0x40)
    assert bus.writes[0] == (0xFD, 0x10)
    assert all(bus.registers[0x09 + 4 * channel] == 0x10 for channel in range(16))
    assert bus.registers[0xFE] == 121


def test_only_channel_five_is_enabled_by_pulse():
    bus = FakeBus()
    prepare(bus, 0x40)
    before = len(bus.writes)
    write_pulse(bus, 0x40, 1500)
    assert [register for register, _ in bus.writes[before:]] == list(range(0x1A, 0x1E))
    assert all(bus.registers[0x09 + 4 * channel] == 0x10 for channel in range(16) if channel != 5)


def test_external_clock_is_rejected_before_writes():
    bus = FakeBus()
    bus.registers[0] = 0x40
    with pytest.raises(ValueError, match="External clock"):
        prepare(bus, 0x40)
    assert not bus.writes
