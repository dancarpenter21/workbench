import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workbench_arm_controller.app import create_app
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.manual import JOINT_ORDER, ManualControl, mock_configuration
from workbench_arm_controller.pca9685 import Pca9685Output
from workbench_common import Store, settings
from workbench_contracts import CommissionPulseBound, ManualInput


def controller():
    now = [0.0]
    cfg = settings()
    cfg["mode"] = "mock"
    return ManualControl(cfg, clock=lambda: now[0]), now


def test_jog_ignores_commissioning_and_legacy_manual_configuration():
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    cfg["manual"] = {"channels": {"invalid": {}}}
    control = ManualControl(cfg)
    assert control.status().available
    assert all(j.min_us == 500 and j.max_us == 2500 for j in control.joints)


def test_abandon_incomplete_trial_without_valid_commissioning_setup(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("abandon", tmp_path)
    commissioning = LiveCommissioning(cfg)
    commissioning.attach(store)
    commissioning.trial = {"trial_id": "unfinished", "draft_id": "missing"}
    commissioning.abandon_for_jog()
    assert commissioning.trial is None
    assert (
        store.get(commissioning.prefix + "abandoned:unfinished")["trial"]["draft_id"] == "missing"
    )
    commissioning.abandon_for_jog()
    store.close()


def command(control, speed=0, joint="base"):
    return ManualInput(
        token=control.token, sequence=control.sequence + 1, joint_id=joint, speed=speed
    )


def test_mapping_motion_precision_and_hold():
    control, now = controller()
    assert [(j.id, j.channel) for j in control.joints] == list(zip(JOINT_ORDER, range(5, -1, -1)))
    initial = dict(control.commanded)
    control.acquire()
    control.input(command(control))
    control.input(command(control, 1))
    for _ in range(10):
        now[0] += 0.02
        control.tick()
    assert control.targets["base"] == pytest.approx(1520)
    assert all(control.commanded[j] == initial[j] for j in JOINT_ORDER[1:])
    control.input(command(control, joint="claw"))
    control.input(command(control, 0.25, "claw"))
    for _ in range(10):
        now[0] += 0.02
        control.tick()
    assert control.targets["claw"] == pytest.approx(1505)
    assert control.commanded["claw"] != initial["claw"]
    control.hold()
    held = dict(control.commanded)
    now[0] += 1
    control.tick()
    assert control.commanded == held
    assert not control.recovery_required


def test_neutral_selection_stale_sequence_and_expired_session():
    control, now = controller()
    session = control.acquire()
    with pytest.raises(HTTPException) as busy:
        control.acquire()
    assert busy.value.status_code == 409
    with pytest.raises(HTTPException):
        control.input(command(control, 1))
    value = command(control)
    control.input(value)
    now[0] += 0.1
    with pytest.raises(HTTPException):
        control.input(value)
    assert control.deadline == 0.3  # Stale messages cannot extend the lease.
    control.input(command(control, 1))
    now[0] = 0.41
    control.tick()
    assert control.token is None and control.recovery_required
    held = dict(control.commanded)
    with pytest.raises(HTTPException):
        control.input(ManualInput(token=session.token, sequence=99, joint_id="base", speed=1))
    assert control.commanded == held
    control.recover()
    assert control.acquire().token != session.token


def test_fractional_motion_bounds_and_no_catch_up():
    control, now = controller()
    control.acquire()
    control.input(command(control))
    control.input(command(control, 0.05))
    initial = control.commanded["base"]
    for index in range(60):
        now[0] += 0.02
        control.tick()
        if index % 5 == 0:
            control.input(command(control, 0.05))
    assert control.targets["base"] == pytest.approx(1506)
    assert control.commanded["base"] > initial
    before = control.targets["base"]
    control.input(command(control, 1))
    now[0] += 0.2
    control.tick()
    assert control.targets["base"] - before == pytest.approx(2)
    control.targets["base"] = 2499
    now[0] += 0.02
    control.tick()
    assert control.targets["base"] == 2500
    assert control.commanded["base"] <= 2500
    control.input(command(control, -1))
    control.targets["base"] = 501
    now[0] += 0.02
    control.tick()
    assert control.targets["base"] == 500
    assert control.commanded["base"] >= 500


async def test_manual_is_independent_and_blocks_retrieval(stack):
    bus, cfg, calls, faults, _ = stack
    url = cfg["services"]["coordinator"]

    async def unavailable(request):
        return httpx.Response(503, json={"message": "Offline"})

    faults[("POST", cfg["services"]["assistant"] + "/interpret")] = unavailable
    faults[("GET", cfg["services"]["vision"] + "/observations")] = unavailable
    acquired = await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    assert acquired.status_code == 200, acquired.text
    token = acquired.json()["token"]
    assert not (await bus.get(url + "/status")).json()["retrieval_enabled"]
    blocked = await bus.post(url + "/requests", json={"text": "get pliers"})
    assert blocked.status_code == 409
    arm_blocked = await bus.post(
        cfg["services"]["arm-controller"] + "/execute",
        json={"operation_id": str(uuid4()), "tool_id": "pliers"},
    )
    assert arm_blocked.status_code == 409
    assert (
        await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    ).status_code == 409
    for sequence, speed in enumerate((0, 1, 0)):
        result = await bus.post(
            url + "/manual/input",
            json={"token": token, "sequence": sequence, "joint_id": "base", "speed": speed},
        )
        assert result.status_code == 200, result.text
        await asyncio.sleep(0.06)
    joints = result.json()["joints"]
    assert joints[0]["commanded_us"] > joints[-1]["commanded_us"]
    assert all("/interpret" not in path and "/observations" not in path for _, path in calls)
    assert (await bus.post(url + "/manual/release", json={"token": token})).status_code == 200
    assert (
        await bus.post(
            url + "/manual/input",
            json={"token": token, "sequence": 4, "joint_id": "base", "speed": 1},
        )
    ).status_code == 409


async def test_watchdog_stop_recovery_and_invalid_input(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    token = (await bus.post(url + "/manual/session", json={"ready_acknowledged": True})).json()[
        "token"
    ]
    body = {"token": token, "sequence": 0, "joint_id": "base", "speed": 0}
    for change in (
        {"speed": 2},
        {"speed": True},
        {"sequence": -1},
        {"sequence": "0"},
        {"unexpected": True},
    ):
        assert (await bus.post(url + "/manual/input", json={**body, **change})).status_code == 422
    assert (
        await bus.post(url + "/manual/input", json={**body, "joint_id": "wrong"})
    ).status_code == 422
    assert (await bus.post(url + "/manual/input", json=body)).status_code == 200
    assert (
        await bus.post(url + "/manual/input", json={**body, "sequence": 1, "speed": 1})
    ).status_code == 200
    await asyncio.sleep(0.36)
    status = (await bus.get(url + "/status")).json()
    assert status["recovery_required"] and not status["manual"]["active"]
    assert (
        await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    ).status_code == 409
    assert (await bus.post(url + "/recover", json={"acknowledged": True})).status_code == 200
    token = (await bus.post(url + "/manual/session", json={"ready_acknowledged": True})).json()[
        "token"
    ]
    stopped = (await bus.post(url + "/stop")).json()
    assert stopped["recovery_required"] and not stopped["manual"]["active"]
    assert (await bus.post(url + "/manual/input", json={**body, "token": token})).status_code == 409


async def test_retrieval_and_manual_acquisition_race(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    manual, retrieval = await asyncio.gather(
        bus.post(url + "/manual/session", json={"ready_acknowledged": True}),
        bus.post(url + "/requests", json={"text": "get pliers"}),
    )
    assert sorted([manual.status_code, retrieval.status_code]) in ([200, 409], [202, 409])
    await bus.post(url + "/stop")


async def test_restart_does_not_restore_manual_ownership(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("arm", tmp_path)
    store.put("manual_active", True)
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/status")).json()["recovery_required"]
            assert not (await client.get("/manual/status")).json()["active"]
            assert (
                await client.post("/manual/session", json={"ready_acknowledged": True})
            ).status_code == 409
    store.close()


class Bus:
    def __init__(self, number):
        self.writes = []
        self.fail = False
        self.closed = False

    def write_byte_data(self, *args):
        self.writes.append(args)

    def read_byte_data(self, address, register):
        return 0x10

    def write_i2c_block_data(self, *args):
        self.writes.append(args)
        if self.fail:
            raise OSError("Injected partial write")

    def close(self):
        self.closed = True


async def test_hardware_preparation_failure_and_pwm_shutdown(tmp_path, monkeypatch):
    import json

    from movement_fixtures import geometry_data

    # Synthetic buses must not contend with the running physical controller's lock.
    monkeypatch.setattr(
        "workbench_arm_controller.device_lock.Path", lambda name: tmp_path / Path(name).name
    )
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    geometry = tmp_path / "geometry.json"
    geometry.write_text(json.dumps(geometry_data(domain="physical")))
    cfg["arm"]["movement_geometry"] = str(geometry)
    cfg["manual"] = mock_configuration()  # Synthetic fixture only; never a physical calibration.
    bus = Bus(1)
    from test_claw_servo_test import SensorBus
    from workbench_arm_controller.claw_current import ClawCurrentMonitor

    sensor = SensorBus(voltage=2, shunt=0)
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: sensor)
    monkeypatch.setattr("workbench_arm_controller.app.ClawCurrentMonitor", lambda _: monitor)
    opened = []

    def output(*args, **kwargs):
        opened.append(True)
        return Pca9685Output(*args, **kwargs, bus_factory=lambda _: bus)

    monkeypatch.setattr("workbench_arm_controller.manual.Pca9685Output", output)
    store = Store("arm", tmp_path)
    commissioning = LiveCommissioning(cfg, monitor=monitor)
    commissioning.attach(store)
    bounds = commissioning.bounds()
    for name in JOINT_ORDER:
        for kind, pulse in (("minimum", 1000), ("maximum", 2000)):
            setattr(
                bounds.joints[name],
                kind,
                CommissionPulseBound(
                    requested_us=pulse,
                    encoded_us=pulse,
                    observed_at=datetime.now(timezone.utc),
                    trial_id="fixture",
                    command_id="fixture",
                ),
            )
    store.put(commissioning.bounds_key(bounds), bounds.model_dump(mode="json"))
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert not opened and not bus.writes
            assert (await client.get("/commissioning/ready")).json() == {"ready": True}
            assert len((await client.get("/manual/status")).json()["joints"]) == 6
            assert (
                await client.post(
                    "/manual/prepare", json={"power_disconnected_and_arm_supported": False}
                )
            ).status_code == 422
            prepared = await client.post(
                "/manual/prepare", json={"power_disconnected_and_arm_supported": True}
            )
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()["prepared"] and opened
            assert [j["channel"] for j in prepared.json()["joints"]] == [5, 4, 3, 2, 1, 0]
            assert (
                await client.post(
                    "/execute", json={"operation_id": str(uuid4()), "tool_id": "pliers"}
                )
            ).status_code == 409
            token = (
                await client.post("/manual/session", json={"ready_acknowledged": True})
            ).json()["token"]
            for sequence, speed in enumerate((0, 1)):
                result = await client.post(
                    "/manual/input",
                    json={"token": token, "sequence": sequence, "joint_id": "base", "speed": speed},
                )
                assert result.status_code == 200
            bus.fail = True
            await asyncio.sleep(0.1)
            status = (await client.get("/manual/status")).json()
            assert not status["active"] and status["recovery_required"] and not status["prepared"]
            assert all(j["commanded_us"] is None for j in status["joints"])
            count = len(bus.writes)
            await asyncio.sleep(0.05)
            assert len(bus.writes) == count  # No replay of a failed hardware write.
            assert (await client.post("/recover", json={"acknowledged": True})).status_code == 409
    assert bus.closed and bus.writes[-1] == (0x40, 0xFD, 0x10)  # Shutdown attempts full-off.
    store.close()


@pytest.mark.parametrize("change", ["mapping", "limits", "rate", "initial", "type"])
def test_legacy_manual_configuration_does_not_gate_jog(change, monkeypatch):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    cfg["manual"] = mock_configuration()
    if change == "mapping":
        cfg["manual"]["channels"]["base"]["channel"] = 0
    elif change == "type":
        cfg["manual"] = "invalid configuration"
    else:
        field, value = {
            "limits": ("min_us", 2501),
            "rate": ("max_rate_us_per_second", 0),
            "initial": ("initial_us", 2501),
        }[change]
        cfg["manual"]["channels"]["base"][field] = value
    monkeypatch.setattr(
        "workbench_arm_controller.manual.Pca9685Output",
        lambda *a, **k: pytest.fail("Opened hardware"),
    )
    control = ManualControl(cfg)
    assert control.status().available
    assert not control.prepared
    assert all(j.min_us == 500 and j.max_us == 2500 for j in control.joints)


async def test_shutdown_waits_for_an_inflight_manual_io_thread(tmp_path, monkeypatch):
    import threading

    started = threading.Event()
    finish = threading.Event()
    closed = []

    def tick(self):
        started.set()
        assert finish.wait(3), "Test did not release the I/O thread"

    def close(self):
        assert finish.is_set(), "Closed output while I/O still ran"
        closed.append(True)

    monkeypatch.setattr(ManualControl, "tick", tick)
    monkeypatch.setattr(ManualControl, "close", close)
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("arm", tmp_path)
    app = create_app(cfg, store)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    try:
        assert await asyncio.to_thread(started.wait, 1)
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.03)
        assert not shutdown.done() and not closed
        finish.set()
        await shutdown
        assert closed
    finally:
        finish.set()
        store.close()


async def test_recovery_exposes_controller_refusal(stack):
    bus, cfg, _, faults, _ = stack

    async def refuse(request):
        return httpx.Response(
            409,
            json={
                "code": "request_failed",
                "message": "Disconnect servo power and prepare outputs again",
            },
        )

    faults[("POST", cfg["services"]["arm-controller"] + "/recover")] = refuse
    response = await bus.post(
        cfg["services"]["coordinator"] + "/recover", json={"acknowledged": True}
    )
    assert response.status_code == 409
    assert response.json()["message"] == "Disconnect servo power and prepare outputs again"


async def test_prepare_recovers_jog_without_separate_recover(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    await bus.post(url + "/stop")
    before = (await bus.get(url + "/manual/status")).json()
    assert before["can_prepare"]
    result = await bus.post(
        url + "/manual/prepare", json={"power_disconnected_and_arm_supported": True}
    )
    assert result.status_code == 200, result.text
    assert result.json()["prepared"] and not result.json()["active"]
    assert not result.json()["recovery_required"]
    assert (
        await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    ).status_code == 200


def test_slow_telemetry_does_not_block_monitoring():
    import threading

    from workbench_arm_controller.claw_current import ClawCurrentMonitor

    entered, release = threading.Event(), threading.Event()

    class SlowStore:
        def append(self, stream, payload):
            entered.set()
            assert release.wait(2)

    cfg = settings()
    cfg["mode"] = "mock"
    monitor = ClawCurrentMonitor(cfg)
    monitor.store = SlowStore()
    monitor.start_logging()
    try:
        assert monitor.poll(force=True)
        assert entered.wait(1)
        assert monitor.poll(force=True)
        assert monitor.sample_sequence == 2
        assert monitor.status().reading_healthy
    finally:
        release.set()
        monitor.close()


def test_telemetry_overflow_latches_fault():
    from workbench_arm_controller.claw_current import ClawCurrentMonitor

    cfg = settings()
    cfg["mode"] = "mock"
    monitor = ClawCurrentMonitor(cfg)
    monitor.store = object()
    monitor.logging_thread = True  # Queue mode with a stalled consumer.
    for _ in range(monitor.telemetry.maxsize):
        assert monitor.poll(force=True)
    assert not monitor.poll(force=True)
    assert "queue full" in monitor.fault
    assert monitor.shutdown_required


async def test_prepare_does_not_take_over_an_active_jog_session(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"]
    acquired = await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    assert acquired.status_code == 200
    status = (await bus.get(url + "/manual/status")).json()
    assert status["blocker"] == "owned" and not status["can_prepare"]
    refused = await bus.post(
        url + "/manual/prepare", json={"power_disconnected_and_arm_supported": True}
    )
    assert refused.status_code == 409
    assert (await bus.get(url + "/manual/status")).json()["active"]


def test_active_commissioning_trial_cannot_be_abandoned(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("owned-trial", tmp_path)
    control = LiveCommissioning(cfg)
    control.attach(store)
    control.token = "live-owner"
    control.trial = {"trial_id": "owned", "draft_id": "missing"}
    with pytest.raises(HTTPException) as error:
        control.abandon_for_jog()
    assert error.value.status_code == 409
    assert control.trial["trial_id"] == "owned"
    assert store.get(control.prefix + "abandoned:owned") is None
    store.close()


def test_direct_position_applies_immediately_and_preserves_other_joints():
    from workbench_contracts import ManualPosition

    control, now = controller()
    control.acquire()
    initial = dict(control.targets)
    control.position(
        ManualPosition(
            token=control.token,
            sequence=0,
            joint_id="base",
            pulse_us=2000,
        )
    )
    assert control.targets["base"] == 2000
    assert all(control.targets[j] == initial[j] for j in JOINT_ORDER[1:])
    assert control.speed == 0
    with pytest.raises(HTTPException):
        control.position(
            ManualPosition(
                token=control.token,
                sequence=0,
                joint_id="base",
                pulse_us=1800,
            )
        )


def test_manual_reset_sets_all_targets_to_neutral():
    from workbench_contracts import ManualPosition, ManualRelease

    control, now = controller()
    control.acquire()
    control.position(
        ManualPosition(
            token=control.token,
            sequence=0,
            joint_id="base",
            pulse_us=2000,
        )
    )
    control.reset(ManualRelease(token=control.token))
    assert set(control.targets.values()) == {1500}
    assert control.speed == 0
    assert control.joint is None
    control.hold()
    with pytest.raises(HTTPException):
        control.reset(ManualRelease(token="00000000-0000-0000-0000-000000000000"))


def test_idle_manual_session_survives_background_timer_throttling():
    control, now = controller()
    session = control.acquire()
    now[0] += 120
    control.tick()
    assert control.token == session.token
    assert not control.recovery_required
    control.input(command(control))
    control.input(command(control, 1))
    now[0] += 1
    control.tick()
    assert control.token is None
    assert control.recovery_required
