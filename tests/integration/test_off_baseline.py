"""Read-only off-state capture and classification, with injected sensor/HTTP data."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_claw_servo_test import SensorBus
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.off_baseline import capture, main, summarize, validate_sample
from workbench_common import settings


def reading(**updates):
    return {
        "simulated": False,
        "healthy": True,
        "fault": "",
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "age_seconds": 0.01,
        "bus_v": 0.004,
        "shunt_mv": -0.02,
        "current_a": None,
        "scale_verified": False,
        **updates,
    }


def bounds(**updates):
    return {
        "baseline_id": "physical-fixture",
        "domain": "physical",
        "i2c_bus": 1,
        "sensor_address": 0x41,
        "pwm_address": 0x40,
        "max_bus_v": 0.020,
        "max_abs_shunt_mv": 0.080,
        "evidence": "Synthetic test fixture, not physical evidence",
        **updates,
    }


def test_bounds_cover_signed_noise_without_inventing_amperes():
    samples = [reading(bus_v=v, shunt_mv=s) for v, s in [(0, -0.04), (0.004, 0), (0.008, 0.04)]]
    summary = summarize(samples)
    assert summary["proposed_bounds"] == {
        "max_bus_v": 0.020,
        "max_abs_shunt_mv": 0.100,
        "max_abs_current_a": None,
    }
    assert summary["shunt_mv"]["min"] == -0.04
    assert summary["current_a"] is None
    assert summarize([reading(), reading()])["proposed_bounds"]["max_abs_shunt_mv"] == 0.05


@pytest.mark.parametrize(
    "changes",
    [
        {"simulated": True},
        {"healthy": False},
        {"fault": "lost sensor"},
        {"age_seconds": 0.3},
        {"age_seconds": -0.01},
        {"bus_v": 5},
        {"bus_v": -0.004},
        {"shunt_mv": -0.51},
        {"shunt_mv": float("nan")},
        {"current_a": -2.5},
        {"sampled_at": None},
        {"scale_verified": True, "current_a": 0},
    ],
)
def test_bad_off_samples_are_rejected(changes):
    with pytest.raises((ValueError, TypeError)):
        validate_sample(reading(**changes))


def test_only_explicit_verified_shunt_produces_ampere_bound():
    samples = [
        validate_sample(
            reading(
                scale_verified=True,
                shunt_ohms=0.1,
                verification_evidence="test measurement",
                current_a=-0.0002,
            )
        )
        for _ in range(3)
    ]
    assert summarize(samples)["proposed_bounds"]["max_abs_current_a"] == pytest.approx(0.0005)
    samples[-1]["shunt_ohms"] = 0.05
    with pytest.raises(ValueError, match="scale changed"):
        summarize(samples)


def test_classifier_requires_both_voltage_and_absolute_shunt_and_valid_health():
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(
        backend="pca9685", i2c_bus=1, i2c_address=0x40, claw_current={"off_baseline": bounds()}
    )
    sensor = SensorBus(shunt=-8, voltage=(5 << 3) | 2)  # -0.080 mV, 0.020 V
    clock = [0.0]
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0], bus_factory=lambda _: sensor)
    assert monitor.poll(force=True)
    status = monitor.status()
    assert status.power_state == "off" and status.current_a is None
    assert status.i2c_bus == 1 and status.sensor_address == 0x41 and status.sample_sequence == 1
    assert status.monitor_session_id
    sensor.values[2] = (6 << 3) | 2
    monitor.poll(force=True)
    assert monitor.status().power_state == "energized"  # Low draw is not proof voltage is off.
    sensor.values[2], sensor.values[1] = 2, -9 & 0xFFFF
    monitor.poll(force=True)
    assert monitor.status().power_state == "energized"
    sensor.values[1] = 0
    monitor.poll(force=True)
    assert monitor.status().power_state == "off"
    clock[0] += 0.3
    assert monitor.status().power_state == "unknown"
    monitor.trip("sensor failure")
    monitor.poll(force=True)
    assert monitor.status().power_state == "off"
    assert monitor.status().reading_healthy and not monitor.status().healthy
    with pytest.raises(RuntimeError, match="sensor failure"):
        monitor.require()  # Recovered readings do not clear the motion interlock.
    sensor.values[2] = 3  # A new conversion failure must not reuse the earlier off reading.
    assert not monitor.poll(force=True)
    assert not monitor.status().reading_healthy
    assert monitor.status().power_state == "unknown"
    monitor.close()


@pytest.mark.parametrize(
    "change",
    [
        {"i2c_bus": 2},
        {"domain": "simulated"},
        {"pwm_address": 0x42},
        {"max_bus_v": 4},
        {"max_abs_shunt_mv": float("nan")},
    ],
)
def test_mismatched_baseline_does_not_claim_off_or_weaken_current_monitor(change):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(backend="pca9685", claw_current={"off_baseline": bounds(**change)})
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(shunt=0, voltage=2))
    assert monitor.poll()
    status = monitor.status()
    assert status.healthy and status.power_state == "unknown" and status.off_baseline_error
    assert status.off_bounds is None and status.current_a is None
    monitor.close()


def run_capture(tmp_path, change=None, *, duplicate=False):
    clock = [0.0]
    epoch = datetime.now(timezone.utc)
    calls = []

    def dispatch(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path == "/commissioning/status":
            return httpx.Response(200, json={"reserved": False})
        assert request.url.path == "/manual/status"
        stamp = epoch if duplicate else epoch + timedelta(seconds=clock[0])
        sample = reading(sampled_at=stamp.isoformat())
        if change:
            sample.update(change(clock[0]))
        return httpx.Response(200, json={"active": False, "claw_current": sample})

    directory = tmp_path / "run"
    with httpx.Client(base_url="http://arm", transport=httpx.MockTransport(dispatch)) as client:
        report = capture(
            client,
            directory,
            duration=2,
            observer="test",
            clock=lambda: clock[0],
            sleep=lambda value: clock.__setitem__(0, clock[0] + max(0.00001, value)),
            wall_clock=lambda: epoch + timedelta(seconds=clock[0]),
        )
    return report, calls


def test_capture_retains_raw_evidence_hash_and_distinct_time_coverage(tmp_path):
    report, calls = run_capture(tmp_path)
    assert report["status"] == "complete" and report["domain"] == "physical"
    assert report["summary"]["sample_count"] >= 19
    raw = (tmp_path / "run/samples.jsonl").read_bytes()
    assert report["samples_sha256"] == hashlib.sha256(raw).hexdigest()
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[0]["record_type"] == "start" and events[-1]["record_type"] == "complete"
    assert all(
        event["reading"]["shunt_mv"] == -0.02
        for event in events
        if event["record_type"] == "sample"
    )
    assert len(calls) > report["summary"]["sample_count"]


@pytest.mark.parametrize("failure", ["stale", "scale", "restart", "duplicate"])
def test_capture_fault_keeps_partial_logs_and_never_proposes_bounds(tmp_path, failure):
    def change(elapsed):
        if elapsed < 0.5:
            return {}
        return {
            "stale": {"healthy": False, "fault": "lost reading"},
            "scale": {"verification_evidence": "changed"},
            "restart": {"monitor_session_id": "new process"},
            "duplicate": {},
        }[failure]

    with pytest.raises(ValueError):
        run_capture(tmp_path, change, duplicate=failure == "duplicate")
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert report["status"] == "rejected" and report["summary"] is None
    assert report["sample_count"] > 0
    assert (
        json.loads((tmp_path / "run/samples.jsonl").read_text().splitlines()[-1])["record_type"]
        == "rejected"
    )


def test_cli_offline_and_long_capture_validation(capsys):
    assert main(["--duration", "1800"]) == 0
    assert "GET status only" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--capture", "--duration", "1800"])


def test_synthetic_sample_does_not_latch_hardware_fault_when_host_pauses():
    cfg = settings()
    cfg["mode"] = "mock"
    clock = [0.0]
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0])
    monitor.poll()
    clock[0] = 2
    monitor.require()
    assert monitor.status().healthy and monitor.status().simulated
    assert not monitor.fault


def test_live_profile_uses_completed_half_hour_capture_at_boundaries():
    from pathlib import Path

    profile = json.loads(
        (Path(__file__).resolve().parents[2] / "config/development/pi.json").read_text()
    )
    off = profile["arm"]["claw_current"]["off_baseline"]
    assert off["baseline_id"] == "20261023T214233Z-journal-97c7c90c"
    assert off["max_bus_v"] == 0.020 and off["max_abs_shunt_mv"] == 0.120
    sensor = SensorBus(shunt=-12, voltage=(5 << 3) | 2)
    monitor = ClawCurrentMonitor(profile, bus_factory=lambda _: sensor)
    monitor.poll(force=True)
    assert monitor.status().power_state == "off"
    sensor.values[1] = 13
    monitor.poll(force=True)
    assert monitor.status().power_state == "energized"
    sensor.values[1], sensor.values[2] = 0, (6 << 3) | 2
    monitor.poll(force=True)
    assert monitor.status().power_state == "energized"
    monitor.close()


def test_sensor_fault_latch_survives_monitor_restart_until_explicit_power_off_prepare(tmp_path):
    from workbench_common import Store

    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    store = Store("fault", tmp_path)
    old = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(voltage=2))
    old.attach(store)
    old.trip("Sensor stale")
    old.record_fault()
    restarted = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(voltage=2))
    restarted.attach(store)
    assert restarted.shutdown_required
    assert restarted.poll(force=True)
    assert not restarted.status().healthy and restarted.status().power_state == "unknown"
    restarted.prepare()  # Explicitly power-off acknowledged by the owning API caller.
    assert restarted.status().healthy and not store.get("claw_current_fault")
    restarted.close()
    store.close()


async def test_main_workbench_forwards_current_and_clears_it_when_arm_unavailable(stack):
    client, cfg, _, faults, _ = stack
    url = cfg["services"]["coordinator"]
    response = await client.get(url + "/status")
    sample = response.json()["claw_current"]
    assert sample["simulated"] and sample["healthy"] and sample["sampled_at"]

    async def fail(request):
        return httpx.Response(503, json={"message": "offline"})

    faults[("GET", cfg["services"]["arm-controller"] + "/manual/status")] = fail
    response = await client.get(url + "/status")
    assert response.json()["claw_current"] is None
