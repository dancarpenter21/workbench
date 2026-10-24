"""Combined neutral test uses fake I2C only."""

import pytest
from test_base_servo_test import FakeBus
from workbench_arm_controller.neutral_servo_test import (
    disable_outputs,
    main,
    prepare_neutral,
)
from workbench_arm_controller.servo_trial import encode_pulse


def test_only_five_non_claw_servos_receive_midpoint():
    bus = FakeBus()
    prepare_neutral(bus, 0x40)
    ticks, _ = encode_pulse(1500)
    for channel in range(16):
        register = 0x06 + 4 * channel
        if channel in (1, 2, 3, 4, 5):
            assert [bus.registers[register + i] for i in range(4)] == [
                0,
                0,
                ticks & 0xFF,
                ticks >> 8,
            ]
        else:
            assert bus.registers[register + 3] == 0x10
    disable_outputs(bus, 0x40)
    assert all(bus.registers[0x09 + 4 * channel] == 0x10 for channel in range(16))


@pytest.mark.parametrize("failed_channel", [5, 4, 3, 2, 1])
def test_partial_write_failure_disables_all_outputs(failed_channel):
    class FailingBus(FakeBus):
        def write_i2c_block_data(self, address, register, data):
            if register == 0x06 + 4 * failed_channel:
                raise OSError("injected channel failure")
            super().write_i2c_block_data(address, register, data)

    bus = FailingBus()
    with pytest.raises(OSError, match="injected"):
        prepare_neutral(bus, 0x40)
    assert all(bus.registers[0x09 + 4 * channel] == 0x10 for channel in range(16))


def test_offline_plan(capsys):
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Channels 1–5: 1500 us" in output
    assert "Claw/channel 0 remains disabled and unpowered" in output
    assert "No I2C access" in output


def test_all_six_neutral_is_explicit():
    bus = FakeBus()
    prepare_neutral(bus, 0x40, include_claw=True)
    ticks, _ = encode_pulse(1500)
    for channel in range(16):
        register = 0x06 + 4 * channel
        if channel <= 5:
            assert [bus.registers[register + i] for i in range(4)] == [
                0,
                0,
                ticks & 255,
                ticks >> 8,
            ]
        else:
            assert bus.registers[register + 3] == 16


def test_claw_write_failure_disables_all_outputs():
    class FailingClawBus(FakeBus):
        def write_i2c_block_data(self, address, register, data):
            if register == 0x06:
                raise OSError("claw failure")
            super().write_i2c_block_data(address, register, data)

    bus = FailingClawBus()
    with pytest.raises(OSError, match="claw failure"):
        prepare_neutral(bus, 0x40, include_claw=True)
    assert all(bus.registers[0x09 + 4 * channel] == 0x10 for channel in range(16))
