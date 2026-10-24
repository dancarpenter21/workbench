import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workbench_arm_controller.app import create_app
from workbench_arm_controller.manual import JOINT_ORDER, ManualControl, mock_configuration
from workbench_arm_controller.pca9685 import Pca9685Output
from workbench_common import Store, settings
from workbench_contracts import ManualInput


def controller():
    now = [0.0]
    cfg = settings()
    cfg["mode"] = "mock"
    return ManualControl(cfg, clock=lambda: now[0]), now


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
    now[0] = 0.31
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
    control.targets["base"] = 1999
    now[0] += 0.02
    control.tick()
    assert control.targets["base"] == 2000
    assert control.commanded["base"] <= 2000
    control.input(command(control, -1))
    control.targets["base"] = 1001
    now[0] += 0.02
    control.tick()
    assert control.targets["base"] == 1000
    assert control.commanded["base"] >= 1000


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
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    cfg["manual"] = mock_configuration()  # Synthetic fixture only; never a physical calibration.
    bus = Bus(1)
    from test_claw_servo_test import SensorBus
    from workbench_arm_controller.claw_current import ClawCurrentMonitor

    sensor = SensorBus(voltage=2)
    monitor = ClawCurrentMonitor(cfg, bus_factory=lambda _: sensor)
    monkeypatch.setattr("workbench_arm_controller.app.ClawCurrentMonitor", lambda _: monitor)
    opened = []

    def output(*args, **kwargs):
        opened.append(True)
        return Pca9685Output(*args, **kwargs, bus_factory=lambda _: bus)

    monkeypatch.setattr("workbench_arm_controller.manual.Pca9685Output", output)
    store = Store("arm", tmp_path)
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert not opened and not bus.writes
            assert (
                await client.post(
                    "/manual/prepare", json={"power_disconnected_and_arm_supported": False}
                )
            ).status_code == 422
            prepared = await client.post(
                "/manual/prepare", json={"power_disconnected_and_arm_supported": True}
            )
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


@pytest.mark.parametrize("change", ["mapping", "limits", "rate", "initial", "evidence"])
def test_invalid_manual_configuration_is_unavailable_without_hardware(change, monkeypatch):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    cfg["manual"] = mock_configuration()
    if change == "mapping":
        cfg["manual"]["channels"]["base"]["channel"] = 0
    elif change == "evidence":
        cfg["manual"]["evidence"] = ""
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
    assert not control.status().available
    with pytest.raises(HTTPException):
        control.prepare()


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
