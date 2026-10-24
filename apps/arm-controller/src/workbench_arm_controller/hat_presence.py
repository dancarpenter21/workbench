"""Cached, read-only PCA9685 presence checks under the arm service's I/O lock."""

import time

from workbench_contracts import PeripheralStatus, now


class HatPresence:
    def __init__(self, config, monitor, *, clock=time.monotonic):
        self.monitor = monitor
        self.clock = clock
        self.enabled = config["mode"] == "mock" or config["arm"].get("backend") == "pca9685"
        self.last_attempt = None
        self.result = PeripheralStatus(
            simulated=config["mode"] == "mock",
            i2c_bus=config["arm"].get("i2c_bus", 1),
            address=config["arm"].get("i2c_address", 0x40),
            detail="Not checked" if self.enabled else "PCA9685 backend not selected",
        )

    def poll(self):
        if not self.enabled:
            return
        if self.last_attempt is not None and self.clock() - self.last_attempt < 1:
            return
        self.last_attempt = self.clock()
        self.result.checked_at = now()
        try:
            if not self.result.simulated:
                if self.monitor.bus is None:
                    self.result.state = "unknown"
                    self.result.detail = "I2C bus unavailable"
                    return
                values = [
                    self.monitor.bus.read_byte_data(self.result.address, register)
                    for register in (0x00, 0x01, 0xFE, 0x02)
                ]
                if any(type(value) is not int or not 0 <= value <= 255 for value in values):
                    raise ValueError("Invalid register response")
                _, mode2, prescale, subaddress = values
                if mode2 & 0xE0 or prescale < 3 or subaddress & 1:
                    raise ValueError("Register response inconsistent with PCA9685")
            self.result.state = "detected"
            self.result.detail = (
                "Simulated HAT"
                if self.result.simulated
                else "PCA9685 register reads succeeded; servo power and motion not verified"
            )
        except Exception as exc:
            self.result.state = "error"
            self.result.detail = str(exc) or "Device did not respond"

    def status(self):
        age = None if self.last_attempt is None else max(0, self.clock() - self.last_attempt)
        result = self.result.model_copy(update={"age_seconds": age})
        if age is not None and age >= 3:
            result.state, result.detail = "unknown", "Device check is stale"
        return result
