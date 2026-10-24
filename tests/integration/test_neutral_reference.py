"""Recorded electrical reference never qualifies motion or assumes measured position."""

import json
from pathlib import Path

import httpx
import pytest
from test_claw_servo_test import SensorBus
from workbench_arm_controller.app import create_app
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.neutral_reference import NeutralReference
from workbench_common import Store
from workbench_contracts import ClawCurrentStatus


def profile():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "config/development/pi.json").read_text()
    )


def sample(**changes):
    return ClawCurrentStatus(
        simulated=False,
        healthy=changes.pop("healthy", True),
        age_seconds=changes.pop("age_seconds", 0.01),
        bus_v=changes.pop("bus_v", 4.988),
        shunt_mv=changes.pop("shunt_mv", -0.75),
        power_state=changes.pop("power_state", "energized"),
        **changes,
    )


def test_recorded_reference_has_verified_shutdown_counts_and_provenance():
    ref = NeutralReference(profile()).reference
    assert ref.trial_id == "20261024T013046Z-a56f8954"
    assert ref.sample_count == 17192 and ref.duration_seconds >= 1800
    assert ref.bus_v.min == 4.956 and ref.bus_v.max == 4.996
    assert ref.shunt_mv.min == pytest.approx(-0.85) and ref.shunt_mv.max == pytest.approx(-0.35)
    assert ref.pwm_full_off_verified and not ref.scale_verified
    assert len(ref.samples_sha256) == len(ref.report_sha256) == 64


@pytest.mark.parametrize(
    "bus,shunt,state",
    [
        (4.988, -0.75, "within_reference"),
        (4.956, -0.85, "within_reference"),
        (4.996, 0.35, "within_reference"),
        (4.9, -0.75, "outside_reference"),
        (4.988, -0.9, "outside_reference"),
        (4.988, 0, "outside_reference"),
    ],
)
def test_advisory_comparison_uses_both_voltage_and_absolute_shunt(bus, shunt, state):
    catalog = NeutralReference(profile())
    assert catalog.assess(sample(bus_v=bus, shunt_mv=shunt), commanded_neutral=True).state == state
    assert (
        catalog.assess(sample(bus_v=bus, shunt_mv=shunt), commanded_neutral=False).state
        == "not_neutral"
    )


def test_off_fault_stale_and_wrong_setup_do_not_claim_normal_neutral():
    catalog = NeutralReference(profile())
    assert (
        catalog.assess(sample(bus_v=0, shunt_mv=0, power_state="off"), commanded_neutral=True).state
        == "off"
    )
    assert (
        catalog.assess(sample(healthy=False, fault="lost sensor"), commanded_neutral=True).state
        == "fault"
    )
    assert catalog.assess(sample(age_seconds=1), commanded_neutral=True).state == "fault"
    cfg = profile()
    cfg["arm"]["i2c_bus"] = 2
    assert NeutralReference(cfg).assess(sample()).state == "unavailable"
    cfg = profile()
    cfg["mode"] = "mock"
    assert NeutralReference(cfg).assess(sample()).reference is None


async def test_service_exposes_reference_without_qualifying_manual_motion(tmp_path, monkeypatch):
    cfg = profile()
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(shunt=-75, voltage=9978))
    monkeypatch.setattr("workbench_arm_controller.app.ClawCurrentMonitor", lambda _: monitor)
    store = Store("reference", tmp_path)
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            current = (await client.get("/manual/status")).json()
            evidence = (await client.get("/commissioning/evidence")).json()
            assert not current["available"] and not current["prepared"]
            assert evidence["state"] == "not_neutral" and not evidence["commanded_neutral"]
            assert current["neutral_evidence"]["reference"]["sample_count"] == 17192
    store.close()


async def test_coordinator_diagnostic_export_includes_neutral_reference(stack):
    client, cfg, _, faults, _ = stack
    catalog = NeutralReference(profile())
    evidence = catalog.assess(sample(), commanded_neutral=False).model_dump(mode="json")
    arm = cfg["services"]["arm-controller"]

    async def manual(request):
        return httpx.Response(200, json={"simulated": True, "neutral_evidence": evidence})

    faults[("GET", arm + "/manual/status")] = manual
    root = cfg["services"]["coordinator"]
    assert (await client.get(root + "/status")).json()["neutral_evidence"] == evidence
    exported = (await client.get(root + "/diagnostics")).json()
    assert exported["neutral_evidence"]["reference"]["trial_id"] == "20261024T013046Z-a56f8954"
