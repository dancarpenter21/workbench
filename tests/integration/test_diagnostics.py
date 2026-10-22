import asyncio
import base64
import io
import sys
import wave
from contextlib import AsyncExitStack
from unittest.mock import MagicMock

import httpx
import pytest
from workbench_arm_controller import app as arm_module
from workbench_assistant.app import create_app as assistant_app
from workbench_common import Store, settings
from workbench_common.diagnostics import command
from workbench_coordinator.app import create_app as coordinator_app
from workbench_vision import app as vision_module
from workbench_voice import microphone
from workbench_voice.app import create_app as voice_app


async def test_mock_diagnostics_report_and_audio(stack, monkeypatch):
    bus, cfg, calls, faults, _ = stack

    async def forbidden(*args, **kwargs):
        raise AssertionError("Mock diagnostics accessed hardware")

    monkeypatch.setattr(microphone, "command", forbidden)
    monkeypatch.setattr(arm_module, "command", forbidden)
    url = cfg["services"]["coordinator"]
    report = (await bus.get(url + "/diagnostics")).json()
    assert report["mode"] == "mock"
    assert {"pi", "hat", "camera", "microphone", "voice", "assistant"} <= {
        check["component"] for check in report["checks"]
    }
    response = await bus.post(url + "/diagnostics/microphone/test", json={"device_id": "mock"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["simulated"] and data["peak"] > 0
    with wave.open(io.BytesIO(base64.b64decode(data["wav_base64"]))) as wav:
        assert wav.getnframes() == 80000
        assert wav.getframerate() == 16000
    assert "wav_base64" not in str(report)
    bad = await bus.post(url + "/diagnostics/microphone/test", json={"device_id": "invalid"})
    assert bad.status_code == 422


async def test_report_partial_failure_and_recovery(stack):
    bus, cfg, _, faults, _ = stack
    url = cfg["services"]["coordinator"] + "/diagnostics"
    key = ("GET", cfg["services"]["vision"] + "/diagnostics")

    async def failed(request):
        return httpx.Response(503)

    faults[key] = failed
    report = (await bus.get(url)).json()
    checks = {c["component"]: c for c in report["checks"]}
    assert checks["service:vision"]["status"] == "unavailable"
    assert checks["hat"]["status"] == "ok"
    del faults[key]
    checks = {c["component"]: c for c in (await bus.get(url)).json()["checks"]}
    assert checks["camera"]["status"] == "ok"


async def test_diagnostics_profile_blocks_requests_and_skips_models(tmp_path, monkeypatch):
    cfg = settings()
    cfg.update(mode="hardware", profile="diagnostics")
    cfg["arm"]["backend"] = "roarm"  # Even an alternate driver must not initialize.
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    driver = MagicMock(side_effect=AssertionError("Driver initialized"))
    monkeypatch.setattr(arm_module, "SerialArm", driver)
    stores = [Store("coordinator", tmp_path), Store("arm", tmp_path)]
    apps = [
        coordinator_app(cfg, stores[0]),
        arm_module.create_app(cfg, stores[1]),
        voice_app(cfg),
        assistant_app(cfg),
    ]
    async with AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(apps[0]), base_url="http://test"
        ) as c:
            response = await c.post("/requests", json={"text": "get pliers"})
            assert response.status_code == 409
            assert not (await c.get("/status")).json()["retrieval_enabled"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(apps[1]), base_url="http://test"
        ) as c:
            response = await c.post(
                "/execute",
                json={"tool_id": "pliers", "operation_id": "12345678-1234-1234-1234-123456789abc"},
            )
            assert response.status_code == 409
        for app in apps[2:]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as c:
                assert not (await c.get("/health")).json()["ready"]
    driver.assert_not_called()
    for store in stores:
        store.close()


async def test_microphone_enumeration_and_fixed_capture(monkeypatch):
    calls = []

    async def fake_command(*args, **kwargs):
        calls.append(args)
        if args == ("arecord", "-l"):
            return b"card 2: Device [USB PnP], device 0: USB Audio [USB Audio]\n"
        return b"\x00\x10" * 80000

    monkeypatch.setattr(microphone, "command", fake_command)
    devices = await microphone.devices()
    assert devices[0].id == "plughw:CARD=Device,DEV=0"
    result = await microphone.Microphone().capture(devices[0].id)
    assert result.duration_seconds == 5
    assert result.peak == 0.125
    assert calls[-1] == (
        "arecord",
        "-q",
        "-D",
        devices[0].id,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-d",
        "5",
    )


@pytest.mark.parametrize("pcm", [b"", b"x", b"\0" * 400000])
def test_microphone_rejects_bad_capture(pcm):
    with pytest.raises(ValueError):
        microphone.result("device", pcm)


def test_microphone_clipping_and_silence():
    assert microphone.result("device", b"\xff\x7f" * 80000).clipped
    assert microphone.result("device", b"\0\0" * 80000).rms == 0


async def test_microphone_busy_and_cancellation(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_devices(mock=False):
        return [microphone.MicrophoneDevice(id="test", name="Test")]

    async def capture(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(microphone, "devices", fake_devices)
    monkeypatch.setattr(microphone, "command", capture)
    mic = microphone.Microphone()
    task = asyncio.create_task(mic.capture("test"))
    await started.wait()
    with pytest.raises(microphone.HTTPException) as exc:
        await mic.capture("test")
    assert exc.value.status_code == 409
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set() and not mic.lock.locked()


async def test_bounded_command_timeout_and_missing_program():
    with pytest.raises(TimeoutError):
        await command(sys.executable, "-c", "import time; time.sleep(10)", timeout=0.05)
    with pytest.raises(FileNotFoundError):
        await command("workbench-missing-program")


async def test_hat_diagnostics_only_calls_readonly_checker(tmp_path, monkeypatch):
    cfg = settings()
    cfg.update(mode="hardware", profile="diagnostics")
    cfg["arm"]["backend"] = "pca9685"
    calls = []

    async def fake_command(*args, **kwargs):
        calls.append(args)
        return b'{"status":"pass","channels_read":16}'

    monkeypatch.setattr(arm_module, "command", fake_command)
    store = Store("arm", tmp_path)
    app = arm_module.create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as c:
            data = (await c.get("/diagnostics")).json()
    store.close()
    assert data[0]["status"] == "ok"
    assert data[0]["measurements"]["physical_position"] is None
    assert "--register-test" not in calls[0]
    assert calls[0][2] == "workbench_arm_controller.hat_check"


async def test_camera_preview_without_references(monkeypatch):
    monkeypatch.setattr(vision_module, "RECONNECT_INTERVAL_SECONDS", 0.02)
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    cfg = settings()
    cfg.update(mode="hardware", profile="diagnostics")
    cfg["camera"]["interval_seconds"] = 0.01
    capture = MagicMock()
    capture.read.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
    monkeypatch.setattr(vision_module, "open_capture", lambda *a: capture)
    app = vision_module.create_app(cfg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as c:
            for _ in range(100):
                preview = await c.get("/preview")
                if preview.status_code == 200:
                    break
                await asyncio.sleep(0.01)
            assert preview.headers["content-type"] == "image/jpeg"
            assert not (await c.get("/observations")).json()["camera_ok"]
            checks = (await c.get("/diagnostics")).json()
            assert checks[0]["status"] == "ok"
            assert checks[1]["status"] == "warning"
            capture.read.side_effect = OSError("Disconnected")
            await asyncio.sleep(0.05)
            assert (await c.get("/preview")).status_code == 503
            capture.read.side_effect = None
            for _ in range(100):
                if (await c.get("/preview")).status_code == 200:
                    break
                await asyncio.sleep(0.01)
            assert (await c.get("/preview")).status_code == 200
    assert capture.close.call_count >= 1


async def test_report_rejects_mismatched_service_mode(stack):
    bus, cfg, _, faults, _ = stack

    async def wrong_mode(request):
        return httpx.Response(200, json={"service": "vision", "mode": "hardware", "ready": True})

    faults[("GET", cfg["services"]["vision"] + "/health")] = wrong_mode
    report = (await bus.get(cfg["services"]["coordinator"] + "/diagnostics")).json()
    checks = {check["component"]: check for check in report["checks"]}
    assert checks["service:vision"]["status"] == "unavailable"
    assert "camera" not in checks


@pytest.mark.parametrize(
    "error", [PermissionError("I2C denied"), TimeoutError(), RuntimeError("No response")]
)
async def test_hat_failure_is_reported_without_hiding_status(tmp_path, monkeypatch, error):
    cfg = settings()
    cfg.update(mode="hardware", profile="diagnostics")
    cfg["arm"]["backend"] = "pca9685"

    async def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(arm_module, "command", failed)
    store = Store("arm", tmp_path)
    app = arm_module.create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.get("/diagnostics")
            assert response.status_code == 200
            assert response.json()[0]["status"] == "error"
            assert (await c.get("/status")).status_code == 200
    store.close()


async def test_microphone_disconnect_releases_worker(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_capture(device_id):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    class Request:
        async def is_disconnected(self):
            return started.is_set()

    mic = microphone.Microphone()
    monkeypatch.setattr(mic, "capture", blocked_capture)
    with pytest.raises(microphone.HTTPException) as exc:
        await mic.test(Request(), "test")
    assert exc.value.status_code == 499
    assert cancelled.is_set()
    assert not mic.tasks


def test_camera_retries_open_after_temporary_failure(monkeypatch):
    monkeypatch.setattr(vision_module, "RECONNECT_INTERVAL_SECONDS", 0.02)
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    cfg = settings()
    cfg["camera"]["interval_seconds"] = 0.01
    capture = MagicMock()
    capture.read.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
    opened = MagicMock(side_effect=[RuntimeError("camera busy"), capture])
    monkeypatch.setattr(vision_module, "open_capture", opened)
    camera = vision_module.Camera(cfg)
    try:
        import time

        deadline = time.monotonic() + 4
        while camera.jpeg is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert camera.jpeg is not None
        assert opened.call_count == 2
    finally:
        camera.close()
    capture.close.assert_called_once()
