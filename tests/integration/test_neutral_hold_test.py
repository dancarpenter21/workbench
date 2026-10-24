"""Fixed-neutral holds use fake PWM/INA219 buses only."""

import json
from pathlib import Path

import pytest
from test_base_servo_test import FakeBus
from workbench_arm_controller.neutral_hold_test import main, run


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, duration):
        self.value += max(duration, 1e-6)


class BenchBus(FakeBus):
    def __init__(self, clock, *, power_at=0.3, sensor_fault=None, shunt=4):
        super().__init__()
        self.clock = clock
        self.power_at = power_at
        self.sensor_fault = sensor_fault
        self.shunt = shunt
        self.closed = False
        self.timed_writes = []

    def read_word_data(self, address, register):
        assert address == 0x41
        powered = self.clock.value >= self.power_at
        values = {0: 0x399F, 1: self.shunt if powered else 0, 2: 10002 if powered else 2}
        if self.sensor_fault is not None and self.clock.value >= self.sensor_fault:
            values[2] |= 1
        value = values[register] & 0xFFFF
        return ((value & 255) << 8) | (value >> 8)

    def write_byte_data(self, address, register, value):
        assert address == 0x40
        self.timed_writes.append((self.clock.value, register, value))
        super().write_byte_data(address, register, value)

    def close(self):
        self.closed = True


def config():
    path = Path(__file__).resolve().parents[2] / "config/development/pi.json"
    return json.loads(path.read_text())


def test_prepares_all_six_once_with_power_off_then_monitors_hold_and_disables(tmp_path):
    clock = Clock()
    bus = BenchBus(clock)
    report = run(
        config(),
        tmp_path / "hold",
        duration=1,
        bus_factory=lambda _: bus,
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert report["status"] == "complete" and report["pwm_full_off_verified"]
    assert report["hold_summary"]["sample_count"] >= 9
    assert report["hold_summary"]["bus_v"]["mean"] == 5
    assert report["hold_summary"]["current_a"] is None
    assert report["current_a_and_2_5_a_cutoff"] == "UNVERIFIED"
    assert set(report["targets_us"]) == set(range(6))
    assert all(t == 0 for t, register, _ in bus.timed_writes if 6 <= register <= 29)
    assert bus.writes[-1] == (0xFD, 0x10) and bus.closed
    samples = [
        json.loads(line) for line in (tmp_path / "hold/samples.jsonl").read_text().splitlines()
    ]
    assert any(s["phase"] == "await_power" and s["record_type"] == "sample" for s in samples)


def test_power_already_on_never_enables_any_channel(tmp_path):
    clock = Clock()
    bus = BenchBus(clock, power_at=0)
    with pytest.raises(RuntimeError, match="supply present"):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    assert bus.writes == [(0xFD, 0x10)]
    report = json.loads((tmp_path / "hold/report.json").read_text())
    assert report["status"] == "aborted" and report["pwm_full_off_verified"]


def test_sensor_fault_disables_without_reapplying_neutral(tmp_path):
    clock = Clock()
    bus = BenchBus(clock, sensor_fault=0.5)
    with pytest.raises(RuntimeError, match="overflow"):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    assert all(t == 0 for t, register, _ in bus.timed_writes if 6 <= register <= 29)
    assert bus.writes[-1] == (0xFD, 0x10)
    assert json.loads((tmp_path / "hold/report.json").read_text())["status"] == "aborted"


@pytest.mark.parametrize("shunt", [-25000, 25000])
def test_verified_positive_and_negative_current_abort_at_cutoff(tmp_path, shunt):
    cfg = config()
    cfg["arm"]["claw_current"].update(
        scale_verified=True, shunt_ohms=0.1, verification_evidence="Injected fixture only"
    )
    clock = Clock()
    bus = BenchBus(clock, shunt=shunt)
    with pytest.raises(RuntimeError, match="Current limit"):
        run(
            cfg,
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    assert bus.writes[-1] == (0xFD, 0x10)


def test_power_application_timeout_disables_without_motion_retry(tmp_path):
    clock = Clock()
    bus = BenchBus(clock, power_at=100)
    with pytest.raises(TimeoutError):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            await_power_seconds=0.5,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    assert bus.writes[-1] == (0xFD, 0x10)


def test_long_hold_requires_explicit_off_and_supervision_and_is_offline_by_default(capsys):
    assert main(["--duration", "1800"]) == 0
    assert "No I2C access" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--live", "--duration", "1800"])


def test_polling_gap_aborts_and_disables_pwm(tmp_path):
    clock = Clock()
    bus = BenchBus(clock)

    def delayed_sleep(duration):
        clock.value += 0.4

    with pytest.raises(RuntimeError, match="polling gap"):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=delayed_sleep,
        )
    assert bus.writes[-1] == (0xFD, 0x10)


def test_power_loss_never_reapplies_targets(tmp_path):
    clock = Clock()

    class PowerLossBus(BenchBus):
        def read_word_data(self, address, register):
            if self.clock.value >= 0.6:
                self.power_at = 100
            return super().read_word_data(address, register)

    bus = PowerLossBus(clock)
    with pytest.raises(RuntimeError, match="power removed"):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    assert bus.writes[-1] == (0xFD, 0x10)
    assert all(t == 0 for t, register, _ in bus.timed_writes if 6 <= register <= 29)


def test_failed_shutdown_is_reported_for_physical_power_cutoff(tmp_path):
    clock = Clock()

    class ShutdownFailure(BenchBus):
        def write_byte_data(self, address, register, value):
            if register == 0xFD and self.clock.value >= 0.5:
                raise OSError("injected shutdown failure")
            super().write_byte_data(address, register, value)

    bus = ShutdownFailure(clock)
    with pytest.raises(RuntimeError, match="CUT PHYSICAL SERVO POWER"):
        run(
            config(),
            tmp_path / "hold",
            duration=1,
            bus_factory=lambda _: bus,
            clock=clock.now,
            sleep=clock.sleep,
        )
    report = json.loads((tmp_path / "hold/report.json").read_text())
    assert report["status"] == "aborted" and report["pwm_full_off_verified"] is False
    assert "shutdown_error" in report and bus.closed
