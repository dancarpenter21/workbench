"""Device presence uses injected registers only; it never writes PWM registers."""

from types import SimpleNamespace

from workbench_arm_controller.hat_presence import HatPresence
from workbench_common import settings


class Registers:
    def __init__(self):
        self.reads = []
        self.missing = False
        self.values = {0: 0x20, 1: 4, 0xFE: 121, 2: 0xE2}

    def read_byte_data(self, address, register):
        self.reads.append((address, register))
        if self.missing:
            raise OSError("No response at 0x40")
        return self.values[register]


def test_read_only_presence_cache_missing_recovery_and_staleness():
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    bus = Registers()
    clock = [0.0]
    monitor = HatPresence(cfg, SimpleNamespace(bus=bus), clock=lambda: clock[0])
    assert monitor.status().state == "unknown"
    monitor.poll()
    assert monitor.status().state == "detected"
    assert [reg for _, reg in bus.reads] == [0, 1, 0xFE, 2]
    monitor.poll()
    assert len(bus.reads) == 4
    bus.missing = True
    clock[0] += 1
    monitor.poll()
    assert monitor.status().state == "error"
    assert "No response" in monitor.status().detail
    bus.missing = False
    clock[0] += 1
    monitor.poll()
    assert monitor.status().state == "detected"
    clock[0] += 3
    assert monitor.status().state == "unknown"


def test_invalid_registers_do_not_claim_detection_and_missing_bus_is_unknown():
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    bus = Registers()
    bus.values[1] = 0xE0
    monitor = HatPresence(cfg, SimpleNamespace(bus=bus))
    monitor.poll()
    assert monitor.status().state == "error"
    unavailable = HatPresence(cfg, SimpleNamespace(bus=None))
    unavailable.poll()
    assert unavailable.status().state == "unknown"


def test_mock_presence_is_explicitly_simulated_without_accessing_a_bus():
    cfg = settings()
    cfg["mode"] = "mock"
    monitor = HatPresence(cfg, SimpleNamespace(bus=None))
    monitor.poll()
    assert monitor.status().state == "detected"
    assert monitor.status().simulated
    assert "Simulated" in monitor.status().detail
