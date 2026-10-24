"""Full-off shutdown only: never initializes PWM, enables a channel or requests a pose."""

from .device_lock import DeviceLock
from .neutral_servo_test import disable_outputs


def shutdown_pwm(config, *, bus_factory=None):
    if config["mode"] != "hardware" or config["arm"].get("backend") != "pca9685":
        return False
    if bus_factory is None:
        from smbus2 import SMBus

        bus_factory = SMBus
    arm = config["arm"]
    bus_number, address = arm.get("i2c_bus", 1), arm.get("i2c_address", 0x40)
    with DeviceLock(bus_number, address), bus_factory(bus_number) as bus:
        disable_outputs(bus, address)
    return True
