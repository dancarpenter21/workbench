"""Current scale lifecycle uses injected INA219 readings, never real I2C."""

import pytest
from fastapi import HTTPException
from test_claw_servo_test import SensorBus
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.current_scale import CurrentScaleRecords
from workbench_common import Store, settings
from workbench_contracts import CurrentScaleApply, CurrentScaleVerification, now


@pytest.fixture
def scales(tmp_path):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(backend="pca9685", claw_current={})
    sensor = SensorBus(voltage=2)
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: sensor)
    store = Store("scale-test", tmp_path)
    monitor.attach(store)
    records = CurrentScaleRecords(monitor)
    records.attach(store)
    yield records, sensor, cfg, store
    monitor.close()
    store.close()


def verification(**kwargs):
    values = dict(
        domain="physical",
        assembly_id="Bench arm",
        observer="Operator",
        method="Independent shunt measurement",
        instrumentation="Calibrated meter",
        evidence="bench-notes/measurement-1",
        observed_at=now(),
        i2c_bus=1,
        pwm_address=64,
        shunt_ohms=0.1,
        externally_verified=True,
    )
    values.update(kwargs)
    return CurrentScaleVerification(**values)


def test_apply_persists_source_precedence_and_preserves_fault(scales):
    records, _, cfg, store = scales
    saved = records.save(verification())
    assert records.status().source == "configuration"
    records.monitor.trip("Earlier fault")
    disabled = []
    records.apply(
        CurrentScaleApply(verification_id=saved.id, power_off_and_supported=True),
        lambda: disabled.append(True),
    )
    assert disabled == [True]
    assert records.status().active_id == saved.id
    assert records.monitor.verified and records.monitor.shunt == 0.1
    assert "Earlier fault" in records.monitor.fault
    cfg["arm"]["claw_current"] = dict(
        scale_verified=True, shunt_ohms=0.2, verification_evidence="Config"
    )
    restarted = ClawCurrentMonitor(cfg)
    restored = CurrentScaleRecords(restarted)
    restored.attach(store)
    assert restored.status().source == "web"
    assert restarted.shunt == 0.1
    assert restarted.active_assembly_id == "Bench arm"
    assert restarted.last is None


@pytest.mark.parametrize("shunt", [-25000, 25000])
def test_applied_scale_enforces_absolute_cutoff(scales, shunt):
    records, sensor, _, _ = scales
    saved = records.save(verification())
    records.apply(
        CurrentScaleApply(verification_id=saved.id, power_off_and_supported=True), lambda: None
    )
    sensor.values[1] = shunt & 0xFFFF
    assert not records.monitor.poll(force=True)
    assert "Current limit" in records.monitor.fault


def test_reject_wrong_setup_domain_blank_evidence_and_powered_apply(scales):
    records, sensor, _, _ = scales
    for record in [
        verification(i2c_bus=2),
        verification(domain="simulated"),
        verification(evidence=" "),
        verification(observed_at=now().replace(tzinfo=None)),
    ]:
        with pytest.raises(HTTPException):
            records.save(record)
    saved = records.save(verification())
    sensor.values[2] = 10002
    with pytest.raises(HTTPException, match="Disconnect ALL"):
        records.apply(
            CurrentScaleApply(verification_id=saved.id, power_off_and_supported=True),
            lambda: pytest.fail("Powered apply attempted shutdown"),
        )
    assert records.status().active_id is None


def test_shutdown_failure_and_sensor_failure_do_not_activate(scales):
    records, sensor, _, _ = scales
    saved = records.save(verification())
    request = CurrentScaleApply(verification_id=saved.id, power_off_and_supported=True)
    with pytest.raises(OSError):
        records.apply(request, lambda: (_ for _ in ()).throw(OSError("Injected shutdown failure")))
    assert records.status().active_id is None
    sensor.values[2] = 3
    with pytest.raises(HTTPException):
        records.apply(request, lambda: pytest.fail("Invalid sensor allowed shutdown"))
    assert records.status().active_id is None


def test_mismatched_active_record_latches_fault_without_config_fallback(scales):
    records, _, cfg, store = scales
    saved = records.save(verification())
    store.put("current_scale:active", saved.id)
    cfg["arm"]["i2c_bus"] = 2
    restarted = ClawCurrentMonitor(cfg)
    CurrentScaleRecords(restarted).attach(store)
    assert restarted.config_error
    assert "does not match" in restarted.fault


@pytest.mark.asyncio
async def test_scale_api_routes_and_active_session_exclusion(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    payload = verification(domain="simulated").model_dump(mode="json")
    response = await bus.post(url + "/commissioning/current-scale", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    draft = (
        await bus.post(
            url + "/commissioning/drafts", json={"assembly_id": "Bench arm", "observer": "Operator"}
        )
    ).json()
    session = (
        await bus.post(
            url + "/commissioning/acquire", json={"draft_id": draft["id"], "joint_id": "base"}
        )
    ).json()
    apply = dict(verification_id=saved["id"], power_off_and_supported=True)
    response = await bus.post(url + "/commissioning/current-scale/apply", json=apply)
    assert response.status_code == 409
    await bus.post(url + "/commissioning/stop", json={"trial_id": session["status"]["trial_id"]})
    await bus.post(
        url + "/commissioning/finish",
        json={"trial_id": session["status"]["trial_id"], "power_off_and_supported": True},
    )
    response = await bus.post(url + "/commissioning/current-scale/apply", json=apply)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "web"
