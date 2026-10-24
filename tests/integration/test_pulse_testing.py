"""Pulse testing uses simulated readings or injected buses, never the physical bench."""

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from test_claw_servo_test import SensorBus
from test_commissioning_live import command, feedback, finish
from workbench_arm_controller.app import create_app
from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.manual import JOINT_ORDER
from workbench_common import Store, settings
from workbench_contracts import (
    CommissionAcquire,
    CommissionCreate,
    CommissionMark,
    CommissionPrepare,
    CommissionReady,
    CommissionSelect,
    CommissionTarget,
    CommissionWindow,
)


@pytest.fixture
def pulse(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    clock = [0.0]
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0])
    store = Store("pulse", tmp_path)
    monitor.store = store
    control = LiveCommissioning(cfg, clock=lambda: clock[0], monitor=monitor)
    control.attach(store)
    draft = control.create(CommissionCreate(assembly_id="Assembly not named", observer="Test"))
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base", trial_type="all_holding"))
    control.prepare(command(control, CommissionPrepare, power_off_and_supported=True))
    control.ready(command(control, CommissionReady, settled_and_clear=True))
    yield control, clock, store
    control.close()
    monitor.close()
    store.close()


def test_each_joint_changes_alone_and_exports_raw_samples(pulse):
    control, clock, store = pulse
    draft_id = control.trial["draft_id"]
    for name in JOINT_ORDER:
        control.select(command(control, CommissionSelect, joint_id=name))
        before = control.status().targets.copy()
        result = control.target(command(control, CommissionTarget, joint_id=name, pulse_us=1490))
        assert result.targets == {**before, name: 1490}
        assert len(result.encoded_targets) == 6
        feedback(control)
        clock[0] += 0.26
    exported = control.export(draft_id)
    assert exported.telemetry and all(s["simulated"] for s in exported.telemetry)
    assert all(s["shunt_mv"] == 0 and s["current_a"] is None for s in exported.telemetry)
    assert not exported.manual_config["enabled"]
    actions = [e["action"] for e in store.journal(control.prefix + "events:" + draft_id)]
    assert actions.count("step_intent") == actions.count("step_result") == 6
    finish(control)
    assert all(j.state == "needs_review" for j in control.read(draft_id).joints)


def test_reject_large_change_wrong_joint_and_bad_windows(pulse):
    control, _, _ = pulse
    for name, target in [("base", 1541), ("claw", 1490)]:
        with pytest.raises(HTTPException):
            control.target(command(control, CommissionTarget, joint_id=name, pulse_us=target))
    for low, high in [(1501, 1550), (1450, 1499), (1500, 1500), (1500, 1500.1)]:
        with pytest.raises(HTTPException):
            control.window(
                command(
                    control,
                    CommissionWindow,
                    joint_id="base",
                    min_us=low,
                    max_us=high,
                    reviewed=True,
                )
            )
    assert control.status().targets["base"] == 1500
    control.window(
        command(control, CommissionWindow, joint_id="base", min_us=1400, max_us=1600, reviewed=True)
    )
    assert control.status().windows["base"] == [1400, 1600]
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1510))
    feedback(control)
    with pytest.raises(HTTPException) as error:
        control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1520))
    assert error.value.status_code == 429


def test_logging_failure_prevents_target_and_invalidates_session(pulse, monkeypatch):
    control, _, store = pulse

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append", fail)
    # Do not sample here: this isolates failure of durable command intent.
    from workbench_contracts import CommissionTarget

    request = CommissionTarget(token=control.token, sequence=99, joint_id="base", pulse_us=1510)
    with pytest.raises((OSError, HTTPException)):
        control.target(request)
    assert control.token is None and control.trial["stage"] == "awaiting_power_off"
    assert control.trial["targets"] == {}


@pytest.mark.parametrize("shunt", [25000, -25000])
def test_verified_scale_trips_at_absolute_cutoff(shunt):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(
        backend="pca9685",
        claw_current={
            "scale_verified": True,
            "shunt_ohms": 0.1,
            "verification_evidence": "synthetic fixture",
        },
    )
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(shunt=shunt))
    assert not monitor.poll()
    assert monitor.shutdown_required and not monitor.status().healthy
    with pytest.raises(RuntimeError, match="Current limit"):
        monitor.require()
    monitor.close()


def test_raw_scale_fault_latch_staleness_and_explicit_power_off_reset():
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    clock = [0.0]
    sensor = SensorBus(shunt=-400)
    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0], bus_factory=lambda _: sensor)
    assert monitor.poll()
    assert monitor.status().shunt_mv == -4 and monitor.status().current_a is None
    with pytest.raises(RuntimeError, match="supply present"):
        monitor.prepare()
    clock[0] += 0.301
    with pytest.raises(RuntimeError, match="stale"):
        monitor.require()
    assert monitor.poll(force=True)  # Healthy reads cannot resume motion after a fault.
    with pytest.raises(RuntimeError):
        monitor.require()
    sensor.values[2] = 2
    monitor.prepare()
    monitor.require()
    sensor.values[2] = 3
    assert not monitor.poll(force=True)
    assert not monitor.status().healthy
    monitor.close()


@pytest.mark.parametrize("shutdown_fails", [False, True])
async def test_service_keeps_sampling_after_release_and_sensor_fault_disables_pwm(
    tmp_path, monkeypatch, shutdown_fails
):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(backend="pca9685", commissioning_enabled=True)
    sensor = SensorBus(voltage=2)
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: sensor)
    monkeypatch.setattr("workbench_arm_controller.app.ClawCurrentMonitor", lambda _: monitor)
    outputs = []

    class Output:
        def __init__(self, *args):
            self.disabled = False
            outputs.append(self)

        def prepare_all(self):
            return dict.fromkeys(JOINT_ORDER, 1498.16)

        def disable(self):
            self.disabled = True
            if shutdown_fails:
                raise OSError("Injected PWM shutdown failure")

        def close(self):
            pass

    monkeypatch.setattr("workbench_arm_controller.commissioning_live.SingleServoOutput", Output)
    # The default argument was bound at import; inject through the controller constructor.
    original = LiveCommissioning
    monkeypatch.setattr(
        "workbench_arm_controller.app.LiveCommissioning",
        lambda config, **kw: original(config, output_factory=Output, **kw),
    )
    store = Store("pulse-service", tmp_path)
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            draft = (
                await client.post(
                    "/commissioning/drafts",
                    json={
                        "assembly_id": "Fake hardware",
                        "observer": "Test",
                    },
                )
            ).json()
            acquired = (
                await client.post(
                    "/commissioning/acquire",
                    json={
                        "draft_id": draft["id"],
                        "joint_id": "base",
                        "trial_type": "all_holding",
                    },
                )
            ).json()
            token = acquired["token"]
            prepared = await client.post(
                "/commissioning/prepare",
                json={
                    "token": token,
                    "sequence": 1,
                    "power_off_and_supported": True,
                },
            )
            assert prepared.status_code == 200, prepared.text
            await client.post(
                "/commissioning/stop", json={"trial_id": acquired["status"]["trial_id"]}
            )
            stream = "commissioning:physical:samples:" + draft["id"]
            count = len(store.journal(stream))
            sensor.values[2] = 10002  # Power on, holding, browser no longer owns motion.
            await asyncio.sleep(0.15)
            assert len(store.journal(stream)) > count
            assert not outputs[0].disabled
            sensor.values[2] = 10003  # Overflow.
            await asyncio.sleep(0.15)
            assert outputs[0].disabled
            status = (await client.get("/commissioning/status")).json()
            assert not status["claw_current"]["healthy"]
            assert "CUT PHYSICAL SERVO POWER" in status["claw_current"]["fault"]
            assert status["targets"] == {}
            if shutdown_fails:
                assert "PWM shutdown failed" in status["claw_current"]["fault"]
                # Let orderly test cleanup succeed after verifying the failure path.
                shutdown_fails = False
            sensor.values[2] = 2
            await asyncio.sleep(0.15)
            status = (await client.get("/commissioning/status")).json()
            assert not status["claw_current"]["healthy"]  # Healthy samples cannot resume motion.
            rejected = await client.post(
                "/commissioning/target",
                json={
                    "token": token,
                    "sequence": 99,
                    "joint_id": "base",
                    "pulse_us": 1510,
                },
            )
            assert rejected.status_code == 409
    store.close()


def test_all_output_register_mapping_and_partial_fault_shutdown(tmp_path):
    from test_base_servo_test import FakeBus
    from workbench_arm_controller.servo_trial import SingleServoOutput

    bus = FakeBus()
    bus.close = lambda: None
    output = SingleServoOutput(bus_number=986, bus_factory=lambda _: bus)
    try:
        assert set(output.prepare_all()) == set(JOINT_ORDER)
        for channel in range(6):
            before = len(bus.writes)
            output.write_joint(channel, 1510, 1400, 1600)
            assert [reg for reg, _ in bus.writes[before:]] == list(
                range(6 + 4 * channel, 10 + 4 * channel)
            )
        output.disable()
        assert all(bus.registers[9 + 4 * channel] == 16 for channel in range(16))
    finally:
        output.close()


@pytest.mark.parametrize("shunt,evidence", [(None, ""), (0, "test"), (-0.1, "test"), (0.1, "")])
def test_verified_scale_requires_actual_resistance_and_evidence(shunt, evidence):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"].update(
        backend="pca9685",
        claw_current={
            "scale_verified": True,
            "shunt_ohms": shunt,
            "verification_evidence": evidence,
        },
    )
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: pytest.fail("Invalid scale opened I2C"))
    with pytest.raises(RuntimeError, match="Verified current scale"):
        monitor.prepare()


def test_slow_read_and_storage_failure_are_latched(tmp_path):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    clock = [0.0]

    class SlowSensor(SensorBus):
        def read_word_data(self, address, register):
            clock[0] += 0.11
            return super().read_word_data(address, register)

    monitor = ClawCurrentMonitor(cfg, clock=lambda: clock[0], bus_factory=lambda _: SlowSensor())
    assert not monitor.poll()
    assert "freshness" in monitor.fault
    monitor.close()

    class BrokenLog:
        def append(self, *args):
            raise OSError("disk full")

    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: SensorBus(voltage=2))
    monitor.store = BrokenLog()
    assert not monitor.poll()
    with pytest.raises(RuntimeError, match="disk full"):
        monitor.prepare()
    monitor.close()


def capture(control, kind, **overrides):
    confirmed = control.confirmed_target() or {}
    values = dict(
        kind=kind,
        joint_id=control.trial["joint_id"],
        command_id=confirmed.get("command_id"),
        expected_requested_us=control.trial["requested_us"],
        settled_and_clear=True,
    )
    values.update(overrides)
    return control.mark(command(control, CommissionMark, **values))


def test_bounds_require_matching_ok_and_valid_order(pulse):
    control, clock, _ = pulse
    assert not control.status().capture_ready
    with pytest.raises(HTTPException, match="Confirm"):
        capture(control, "minimum")
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1460))
    with pytest.raises(HTTPException):
        capture(control, "minimum")
    feedback(control)
    assert control.status().capture_ready
    for stale in [dict(joint_id="claw"), dict(command_id="old"), dict(expected_requested_us=1500)]:
        with pytest.raises(HTTPException):
            capture(control, "minimum", **stale)
    before = control.status().targets.copy()
    capture(control, "minimum")
    assert control.status().targets == before  # Capturing never moves an output.
    with pytest.raises(HTTPException, match="below maximum"):
        capture(control, "maximum")
    assert control.bounds().joints["base"].maximum is None
    clock[0] += 0.26
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1500))
    assert not control.status().capture_ready
    feedback(control)
    capture(control, "maximum")
    saved = control.bounds().joints["base"]
    assert (saved.minimum.requested_us, saved.maximum.requested_us) == (1460, 1500)
    assert saved.minimum.command_id != saved.maximum.command_id
    assert saved.minimum.encoded_us != saved.minimum.requested_us
    control.select(command(control, CommissionSelect, joint_id="claw"))
    assert not control.status().capture_ready
    with pytest.raises(HTTPException):
        capture(control, "minimum")
    control.select(command(control, CommissionSelect, joint_id="base"))
    with pytest.raises(HTTPException):
        capture(control, "minimum")  # Equal endpoint replacement is rejected.


def test_bounds_persist_across_sessions_restart_and_identity_changes(pulse, tmp_path):
    control, _, store = pulse
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1490))
    feedback(control)
    capture(control, "minimum")
    original = control.bounds()
    finish(control)
    assert control.bounds() == original
    draft = control.create(CommissionCreate(assembly_id="Assembly not named", observer="Test"))
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base", trial_type="all_holding"))
    control.prepare(command(control, CommissionPrepare, power_off_and_supported=True))
    control.ready(command(control, CommissionReady, settled_and_clear=True))
    assert not control.read(draft.id).joints[0].observations
    assert control.bounds() == original
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1480))
    feedback(control)
    capture(control, "minimum")
    finish(control)
    reloaded_store = Store("pulse", tmp_path)
    restarted = LiveCommissioning(control.config)
    restarted.attach(reloaded_store)
    try:
        assert restarted.bounds().joints["base"].minimum.requested_us == 1480
        restarted.monitor.active_assembly_id = "Different assembly"
        assert restarted.bounds().joints["base"].minimum is None
        restarted.monitor.active_assembly_id = None
        cfg = settings()
        cfg["mode"] = "hardware"
        cfg["arm"]["backend"] = "pca9685"
        physical = LiveCommissioning(cfg)
        physical.attach(reloaded_store)  # Read-only; never opens an I2C device.
        try:
            assert physical.bounds().joints["base"].minimum is None
            saved = physical.bounds()
            saved.joints["base"] = restarted.bounds().joints["base"]
            reloaded_store.put(physical.bounds_key(saved), saved.model_dump(mode="json"))
            assert physical.bounds().joints["base"].minimum.requested_us == 1480
            cfg["arm"]["i2c_bus"] = 99
            assert physical.bounds().joints["base"].minimum is None
        finally:
            physical.close()
            physical.monitor.close()
    finally:
        restarted.close()
        restarted.monitor.close()
        reloaded_store.close()


def test_larger_steps_and_range_extension_do_not_bypass_limits(pulse):
    control, clock, _ = pulse
    assert control.status().max_move_us == 40
    for target in [1541, 1551]:
        with pytest.raises(HTTPException):
            control.target(command(control, CommissionTarget, joint_id="base", pulse_us=target))
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1540))
    feedback(control)
    before = control.status().targets.copy()
    control.window(
        command(control, CommissionWindow, joint_id="base", min_us=1450, max_us=1600, reviewed=True)
    )
    assert control.status().targets == before
    clock[0] += 0.26
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1580))
    feedback(control)
    capture(control, "maximum")
    finish(control)
    draft = control.create(CommissionCreate(assembly_id="Assembly not named", observer="Test"))
    control.acquire(CommissionAcquire(draft_id=draft.id, joint_id="base", trial_type="all_holding"))
    assert control.status().windows["base"] == [1450, 1550]
    assert control.bounds().joints["base"].maximum.requested_us == 1580


def test_sensor_fault_prevents_capture(pulse):
    control, _, _ = pulse
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1490))
    feedback(control)
    control.monitor.trip("Synthetic sensor failure")
    with pytest.raises(RuntimeError):
        capture(control, "minimum")
    assert control.bounds().joints["base"].minimum is None


def test_bounds_storage_failure_stops_session_and_preserves_saved_value(pulse, monkeypatch):
    control, clock, store = pulse
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1490))
    feedback(control)
    capture(control, "minimum")
    original = control.bounds()
    clock[0] += 0.26
    control.target(command(control, CommissionTarget, joint_id="base", pulse_us=1480))
    feedback(control)
    put = store.put

    def fail_bounds(key, value):
        if key == control.bounds_key(original):
            raise OSError("Synthetic bounds storage failure")
        put(key, value)

    monkeypatch.setattr(store, "put", fail_bounds)
    with pytest.raises(HTTPException, match="CUT PHYSICAL SERVO POWER"):
        capture(control, "minimum")
    assert control.token is None
    assert control.status().stage == "awaiting_power_off"
    assert control.status().targets == {}
    assert control.bounds() == original
