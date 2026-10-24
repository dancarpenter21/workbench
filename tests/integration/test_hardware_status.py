"""Hardware status uses cached service monitors, never commands or resets devices."""

import httpx
import pytest
from test_claw_servo_test import SensorBus
from test_hat_presence import Registers
from test_neutral_reference import profile
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.hardware_status import HardwareStatus
from workbench_arm_controller.hat_presence import HatPresence
from workbench_arm_controller.neutral_reference import NeutralReference


class Bus:
    def __init__(self):
        self.sensor = SensorBus(shunt=-6, voltage=10)
        self.hat = Registers()
        self.word_reads = 0

    def read_word_data(self, *args):
        self.word_reads += 1
        return self.sensor.read_word_data(*args)

    def read_byte_data(self, *args):
        return self.hat.read_byte_data(*args)

    def close(self):
        pass


@pytest.mark.parametrize(
    "state",
    ["off", "energized", "latched_off", "latched_on", "stale", "error", "missing", "unknown"],
)
def test_cached_status_separates_readings_power_and_interlock(state):
    cfg = profile()
    bus = Bus()
    clock = [0.0]
    if state == "unknown":
        cfg["arm"]["claw_current"].pop("off_baseline", None)
    if state in ("energized", "latched_on"):
        bus.sensor.values[2] = 10002
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0], bus_factory=lambda _: bus)
    hat = HatPresence(cfg, monitor, clock=lambda: clock[0])
    hardware = HardwareStatus(cfg, monitor, hat, NeutralReference(cfg))
    if state != "missing":
        assert monitor.poll(force=True)
        hat.poll()
    if state.startswith("latched"):
        monitor.trip("Earlier stale INA219 readings")
        assert monitor.poll(force=True)
    if state == "stale":
        clock[0] += 0.31
    if state == "error":
        bus.sensor.values[2] = 10003
        assert not monitor.poll(force=True)
    reads = (bus.word_reads, len(bus.hat.reads))
    for _ in range(3):
        snapshot = hardware.snapshot(commanded_neutral=True)
        current = snapshot.claw_current
        checks = {c.component: c for c in snapshot.checks}
        valid = state not in ("stale", "error", "missing")
        assert checks["claw_current"].measurements["reading_healthy"] == valid
        assert checks["claw_current"].status == (
            "ok" if valid else "error" if state == "error" else "unavailable"
        )
        assert checks["claw_current"].measurements["power_state"] == current.power_state
        if state in ("off", "latched_off"):
            assert current.power_state == "off"
            assert snapshot.neutral_evidence.state == "off"
            assert "Powered off" in checks["claw_current"].summary
        if state == "unknown":
            assert current.power_state == "unknown"
        if state.startswith("latched"):
            assert current.reading_healthy and not current.healthy
            assert current.reset_required
            assert checks["motion_interlock"].status == "warning"
            with pytest.raises(RuntimeError, match="Earlier stale"):
                monitor.require()
        if not valid:
            assert current.power_state == "unknown"
            assert snapshot.neutral_evidence.state == "fault"
    assert (bus.word_reads, len(bus.hat.reads)) == reads
    monitor.close()


async def test_coordinator_uses_hardware_independently_of_manual_control(stack):
    client, cfg, calls, faults, _ = stack
    arm = cfg["services"]["arm-controller"]
    coordinator = cfg["services"]["coordinator"]

    async def unavailable(request):
        return httpx.Response(503, json={"message": "Manual configuration unavailable"})

    faults[("GET", arm + "/manual/status")] = unavailable
    status = (await client.get(coordinator + "/status")).json()
    hardware = status["hardware"]
    assert hardware is not None
    assert status["claw_current"] == hardware["claw_current"]
    assert status["servo_hat"] == hardware["servo_hat"]
    assert status["neutral_evidence"] == hardware["neutral_evidence"]
    assert status["commissioning"]["claw_current"] == hardware["claw_current"]
    report = (await client.get(coordinator + "/diagnostics")).json()
    assert report["hardware"] is not None
    assert report["neutral_evidence"] == report["hardware"]["neutral_evidence"]
    for check in report["hardware"]["checks"]:
        assert check in report["checks"]
    assert all(method == "GET" for method, _ in calls)
    faults[("GET", arm + "/hardware/status")] = unavailable
    status = (await client.get(coordinator + "/status")).json()
    assert status["hardware"] is None and status["claw_current"] is None
    assert (await client.get(coordinator + "/hardware/status")).status_code == 503
    report = (await client.get(coordinator + "/diagnostics")).json()
    assert report["hardware"] is None
    assert any(
        c["component"] == "service:arm-controller" and c["status"] == "unavailable"
        for c in report["checks"]
    )
