import errno
import json
import sys
from types import SimpleNamespace

import pytest
from workbench_arm_controller.hat_check import check_hat, main


class Bus:
    def __init__(self):
        self.registers = {0: 1, 1: 4, 2: 0xE2, 0xFE: 30}
        self.writes = []
        self.fail = False

    def read_byte_data(self, address, register):
        if self.fail and self.writes and self.registers[2] != 0xE2:
            raise OSError("read failed")
        return self.registers.get(register, 0)

    def write_byte_data(self, address, register, value):
        self.writes.append((address, register, value))
        self.registers[register] = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def test_read_only_does_not_write():
    bus = Bus()
    assert check_hat(bus)["channels_read"] == 16
    assert bus.writes == []


def test_register_test_restores_and_never_writes_output_registers():
    bus = Bus()
    assert check_hat(bus, register_test=True)["register_test"] == "passed and restored"
    assert bus.writes == [(0x40, 2, value) for value in (0xAA, 0x54, 0xE2)]


def test_restore_after_read_failure():
    bus = Bus()
    bus.fail = True
    with pytest.raises(OSError, match="read failed"):
        check_hat(bus, register_test=True)
    assert bus.registers[2] == 0xE2


@pytest.mark.parametrize("register,value", [(1, 0xFF), (0xFE, 0), (2, 1), (0x45, 0xFF)])
def test_incompatible_registers_prevent_writes(register, value):
    bus = Bus()
    bus.registers[register] = value
    with pytest.raises(ValueError):
        check_hat(bus, register_test=True)
    assert bus.writes == []


def test_enabled_subaddress_prevents_writes():
    bus = Bus()
    bus.registers[0] |= 8
    with pytest.raises(ValueError, match="enabled"):
        check_hat(bus, register_test=True)
    assert bus.writes == []


def test_missing_bus(monkeypatch, capsys):
    monkeypatch.setattr("workbench_arm_controller.hat_check.Path.exists", lambda _: False)
    assert main([]) == 1
    assert "Enable I2C" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("error", [None, OSError(errno.EREMOTEIO, "absent"), PermissionError()])
def test_cli_bus_result_and_cleanup(monkeypatch, capsys, error):
    bus = Bus()

    def factory(number):
        assert number == 1
        if error:
            raise error
        return bus

    monkeypatch.setattr("workbench_arm_controller.hat_check.Path.exists", lambda _: True)
    monkeypatch.setitem(sys.modules, "smbus2", SimpleNamespace(SMBus=factory))
    assert main([]) == (1 if error else 0)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("fail" if error else "pass")
    if not error:
        assert bus.closed


def test_readback_mismatch_fails_and_restores():
    bus = Bus()
    original_write = bus.write_byte_data

    def stuck_write(address, register, value):
        if value == 0xE2:
            original_write(address, register, value)

    bus.write_byte_data = stuck_write
    with pytest.raises(ValueError, match="write/read verification failed"):
        check_hat(bus, register_test=True)
    assert bus.registers[2] == 0xE2


def test_restore_failure_is_explicit():
    bus = Bus()
    original_write = bus.write_byte_data

    def failed_restore(address, register, value):
        if value == 0xE2:
            raise OSError("restore failed")
        original_write(address, register, value)

    bus.write_byte_data = failed_restore
    with pytest.raises(ValueError, match="restoration failed"):
        check_hat(bus, register_test=True)
