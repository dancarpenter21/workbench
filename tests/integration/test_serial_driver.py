import json
import sys
from types import SimpleNamespace

import pytest
from workbench_arm_controller.driver import SerialArm, validate_calibration
from workbench_common import settings


def calibration():
    config = settings()
    home = {"base": 0, "shoulder": 0, "elbow": 1, "hand": 1}
    pickup = {**home, "base": 0.2}
    config["workbench"]["arm"]["calibrated"] = True
    config["workbench"]["arm"]["sequences"] = {
        tool["id"]: [home.copy(), pickup.copy(), home.copy()]
        for tool in config["workbench"]["tools"]
    }
    return config


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.commands = []
        self.pose = {"b": 0, "s": 0, "e": 1, "t": 1}

    def write(self, data):
        command = json.loads(data)
        self.commands.append(command)
        if command["T"] == 102:
            self.pose = {
                key: command[joint]
                for joint, key in {"base": "b", "shoulder": "s", "elbow": "e", "hand": "t"}.items()
            }

    def readline(self):
        return json.dumps({"T": 1051, **self.pose}).encode()

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def test_serial_feedback_paths_and_interrupt(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    driver = SerialArm(calibration())
    driver.execute("pliers")
    moves = [c for c in driver.serial.commands if c["T"] == 102]
    assert [c["base"] for c in moves] == [0.2, 0]
    assert all(c["spd"] == 100 and c["acc"] == 5 for c in moves)
    driver.stop()
    count = sum(c["T"] == 102 for c in driver.serial.commands)
    with pytest.raises(InterruptedError):
        driver.execute("pliers")
    assert sum(c["T"] == 102 for c in driver.serial.commands) == count
    assert not any(c["T"] in {0, 210} for c in driver.serial.commands)
    driver.recover()
    assert not driver.interrupted.is_set()


def test_wrong_start_pose_never_moves(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    driver = SerialArm(calibration())
    driver.serial.pose["b"] = 0.9
    with pytest.raises(ValueError, match="starting pose"):
        driver.execute("pliers")
    assert not any(c["T"] == 102 for c in driver.serial.commands)


@pytest.mark.parametrize("value", [100, float("nan"), float("inf")])
def test_invalid_waypoint_rejected(value):
    config = calibration()
    config["workbench"]["arm"]["sequences"]["pliers"][1]["base"] = value
    with pytest.raises(ValueError, match="limit"):
        validate_calibration(config)
