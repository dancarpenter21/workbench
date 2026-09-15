import builtins
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import httpx
import pytest
from workbench_common import settings
from workbench_contracts import Observations, now
from workbench_vision import app as vision_app
from workbench_vision.capture import OpenCVCapture, Picamera2Capture, open_capture


@pytest.fixture
def camera_config():
    return {"device": 0, "width": 640, "height": 480, "interval_seconds": 0}


@pytest.fixture
def cv():
    module = MagicMock()
    module.CAP_PROP_FRAME_WIDTH = 3
    module.CAP_PROP_FRAME_HEIGHT = 4
    module.VideoCapture.return_value.isOpened.return_value = True
    return module


@pytest.fixture
def picamera(monkeypatch):
    module = MagicMock()
    monkeypatch.setitem(sys.modules, "picamera2", module)
    return module


def test_default_backend_keeps_opencv_capture_and_releases_device(camera_config, cv):
    frame = object()
    device = cv.VideoCapture.return_value
    device.read.return_value = True, frame
    capture = open_capture(camera_config, cv)
    assert isinstance(capture, OpenCVCapture)
    assert capture.read() is frame
    cv.VideoCapture.assert_called_once_with(0)
    assert device.set.call_args_list == [call(3, 640), call(4, 480)]
    capture.close()
    capture.close()
    device.release.assert_called_once()


def test_opencv_open_failure_releases_device(camera_config, cv):
    cv.VideoCapture.return_value.isOpened.return_value = False
    with pytest.raises(RuntimeError, match="unavailable"):
        open_capture(camera_config, cv)
    cv.VideoCapture.return_value.release.assert_called_once()


def test_opencv_read_failure_is_reported(camera_config, cv):
    cv.VideoCapture.return_value.read.return_value = False, None
    capture = open_capture(camera_config, cv)
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            capture.read()
    finally:
        capture.close()
    cv.VideoCapture.return_value.release.assert_called_once()


def test_picamera2_selects_bgr_bytes_and_preserves_frame(camera_config, cv, picamera):
    camera_config["backend"] = "picamera2"
    device = picamera.Picamera2.return_value
    frame = object()
    device.wait.return_value = frame
    capture = open_capture(camera_config, cv)
    assert isinstance(capture, Picamera2Capture)
    picamera.Picamera2.assert_called_once_with(camera_num=0)
    device.create_video_configuration.assert_called_once_with(
        main={"size": (640, 480), "format": "RGB888"}, queue=False
    )
    device.configure.assert_called_once_with(device.create_video_configuration.return_value)
    device.start.assert_called_once()
    assert capture.read() is frame
    device.capture_array.assert_called_once_with("main", wait=False)
    device.wait.assert_called_once_with(device.capture_array.return_value, timeout=1.0)
    cv.VideoCapture.assert_not_called()
    capture.close()
    capture.close()
    device.cancel_all_and_flush.assert_not_called()
    device.close.assert_called_once()


def test_picamera2_initialization_failure_closes_device(camera_config, picamera):
    device = picamera.Picamera2.return_value
    device.configure.side_effect = RuntimeError("CSI configuration failed")
    with pytest.raises(RuntimeError, match="CSI configuration failed"):
        Picamera2Capture(camera_config)
    device.close.assert_called_once()


def test_picamera2_timeout_does_not_queue_more_jobs_and_cleanup_flushes(camera_config, picamera):
    device = picamera.Picamera2.return_value
    device.wait.side_effect = TimeoutError()
    capture = Picamera2Capture(camera_config)
    for _ in range(2):
        with pytest.raises(TimeoutError, match="frame capture timed out"):
            capture.read()
    device.capture_array.assert_called_once_with("main", wait=False)
    capture.close()
    device.cancel_all_and_flush.assert_called_once()
    device.close.assert_called_once()


@pytest.mark.parametrize("failure", [OSError("CSI read failed"), RuntimeError("capture failed")])
def test_picamera2_failed_job_allows_new_capture(camera_config, picamera, failure):
    device = picamera.Picamera2.return_value
    failed_job, healthy_job, frame = object(), object(), object()
    device.capture_array.side_effect = [failed_job, healthy_job]
    device.wait.side_effect = [failure, frame]
    capture = Picamera2Capture(camera_config)
    try:
        with pytest.raises(type(failure), match=str(failure)):
            capture.read()
        assert capture.read() is frame
        assert device.capture_array.call_args_list == [
            call("main", wait=False),
            call("main", wait=False),
        ]
        assert device.wait.call_args_list == [
            call(failed_job, timeout=1.0),
            call(healthy_job, timeout=1.0),
        ]
    finally:
        capture.close()
    device.cancel_all_and_flush.assert_not_called()
    device.close.assert_called_once()


def test_picamera2_close_attempts_release_when_flush_fails(camera_config, picamera):
    device = picamera.Picamera2.return_value
    device.wait.side_effect = TimeoutError()
    capture = Picamera2Capture(camera_config)
    with pytest.raises(TimeoutError):
        capture.read()
    device.cancel_all_and_flush.side_effect = RuntimeError("flush failed")
    with pytest.raises(RuntimeError, match="flush failed"):
        capture.close()
    device.close.assert_called_once()


def test_picamera2_missing_dependency_has_actionable_error(camera_config, cv, monkeypatch):
    monkeypatch.setitem(sys.modules, "picamera2", None)
    with pytest.raises(RuntimeError, match="python3-picamera2"):
        open_capture(camera_config | {"backend": "picamera2"}, cv)
    cv.VideoCapture.assert_not_called()


def test_unknown_backend_fails_without_opening_hardware(camera_config, cv):
    with pytest.raises(ValueError, match="Unknown camera backend"):
        open_capture(camera_config | {"backend": "typo"}, cv)
    cv.VideoCapture.assert_not_called()


def worker(camera_config, cv):
    camera = vision_app.Camera.__new__(vision_app.Camera)
    camera.cfg = {
        "camera": camera_config,
        "workbench": {
            "tools": [{"id": "pliers"}],
            "vision": {"regions": {"pliers": [0, 0, 10, 10], "tray": [20, 20, 10, 10]}},
        },
    }
    camera.cv = cv
    camera.lock = threading.Lock()
    camera.stop = threading.Event()
    camera.jpeg = b"old preview"
    camera.observations = Observations(
        captured_at=now(), tools=[], tray_clear=False, camera_ok=True
    )
    camera.error = ""
    camera.classify = lambda crop, region: ("empty" if region == "tray" else region, 1.0)
    return camera


def test_worker_startup_failure_invalidates_previous_capture(camera_config, cv, monkeypatch):
    camera = worker(camera_config, cv)
    captured_at = camera.observations.captured_at
    monkeypatch.setattr(vision_app, "open_capture", MagicMock(side_effect=RuntimeError("CSI busy")))
    camera.run()
    assert camera.error == "CSI busy"
    assert camera.jpeg is None
    assert not camera.observations.camera_ok
    assert camera.observations.captured_at == captured_at


def test_worker_processes_bgr_frame_then_hides_preview_on_read_failure(
    camera_config, cv, monkeypatch
):
    camera = worker(camera_config, cv)
    frame = MagicMock()
    frame.shape = (480, 640, 3)
    cv.imencode.return_value = True, SimpleNamespace(tobytes=lambda: b"new jpeg")
    capture = MagicMock()
    capture.read.side_effect = [frame, RuntimeError("camera disconnected")]
    monkeypatch.setattr(vision_app, "open_capture", lambda cfg, cv: capture)
    snapshots = []

    def after_frame(_):
        snapshots.append((camera.jpeg, camera.observations.camera_ok))
        if len(snapshots) == 2:
            camera.stop.set()

    monkeypatch.setattr(camera.stop, "wait", after_frame)
    camera.run()
    assert snapshots == [(b"new jpeg", True), (None, False)]
    assert camera.error == "camera disconnected"
    assert camera.observations.tools[0].location == "source"
    cv.imencode.assert_called_once_with(".jpg", frame)
    capture.close.assert_called_once()


def test_worker_closes_device_when_service_stops(camera_config, cv, monkeypatch):
    camera = worker(camera_config, cv)
    capture = MagicMock()
    read_started = threading.Event()

    def read():
        read_started.set()
        raise RuntimeError("no frame")

    capture.read.side_effect = read
    camera.cfg["camera"]["interval_seconds"] = 10
    monkeypatch.setattr(vision_app, "open_capture", lambda cfg, cv: capture)
    camera.thread = threading.Thread(target=camera.run)
    camera.thread.start()
    try:
        assert read_started.wait(timeout=1)
    finally:
        camera.close()
    assert not camera.thread.is_alive()
    capture.close.assert_called_once()


async def test_mock_service_does_not_import_camera_libraries(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"cv2", "picamera2"}:
            raise AssertionError("Mock service imported a camera library")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    cfg = settings()
    cfg["mode"] = "mock"
    cfg["camera"]["backend"] = "picamera2"
    app = vision_app.create_app(cfg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).json()["ready"]
            assert (await client.get("/observations")).json()["camera_ok"]
            assert (await client.get("/preview")).headers["content-type"] == "image/svg+xml"
