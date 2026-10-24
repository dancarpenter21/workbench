import pytest
from workbench_arm_controller.claw_servo_test import check_current, main, read_sample


class SensorBus:
    def __init__(self, shunt=400, voltage=10002, config=0x399F):
        self.values = {0: config, 1: shunt & 0xFFFF, 2: voltage}

    def close(self):
        pass

    def read_word_data(self, address, register):
        assert address == 0x41
        value = self.values[register]
        return ((value & 255) << 8) | (value >> 8)


@pytest.mark.parametrize("shunt", [400, -400])
def test_signed_current_and_bus_scaling(shunt):
    sample = read_sample(SensorBus(shunt), 0x41, 0.1)
    assert sample["bus_v"] == 5
    assert sample["current_a"] == pytest.approx(shunt * 0.0001)


def test_unknown_shunt_reports_no_amperes():
    assert read_sample(SensorBus(), 0x41)["current_a"] is None


@pytest.mark.parametrize("current", [-2.5, 2.5, -3, 3])
def test_absolute_current_limit(current):
    with pytest.raises(ValueError, match="Current limit"):
        check_current({"current_a": current, "bus_v": 5, "shunt_mv": 0}, 2.5)


@pytest.mark.parametrize("voltage,config", [(10003, 0x399F), (10000, 0x399F), (10002, 0)])
def test_sensor_faults(voltage, config):
    with pytest.raises(ValueError):
        read_sample(SensorBus(voltage=voltage, config=config), 0x41)


def test_offline(capsys):
    assert main([]) == 0
    assert "No I2C access" in capsys.readouterr().out
