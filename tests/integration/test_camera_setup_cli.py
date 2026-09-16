import builtins
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from workbench_common import settings
from workbench_vision import setup

cv = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def bench_paths(tmp_path):
    bench = settings()["workbench"]
    source = write_json(tmp_path / "source.json", bench)
    working = tmp_path / "bench.json"
    config = write_json(
        tmp_path / "runtime.json",
        {
            "camera": {
                "backend": "picamera2",
                "device": 0,
                "width": 640,
                "height": 480,
            }
        },
    )
    return source, working, config


def test_init_needs_no_camera_or_motor_imports_and_does_not_replace_files(bench_paths, monkeypatch):
    source, working, _ = bench_paths
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"picamera2", "smbus2", "serial", "workbench_arm_controller"}:
            pytest.fail(f"Unexpected hardware import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert setup.main(["init", "--source", str(source), "--output", str(working)]) == 0
    assert json.loads(source.read_text()) == json.loads(working.read_text())
    with pytest.raises(SystemExit) as error:
        setup.main(["init", "--source", str(source), "--output", str(working)])
    assert error.value.code == 2


def test_capture_saves_raw_pixels_and_releases_device(bench_paths, tmp_path, monkeypatch, capsys):
    _, _, config = bench_paths
    frame = np.full((480, 640, 3), (12, 34, 56), dtype=np.uint8)
    device = MagicMock()
    device.read.return_value = frame
    opener = MagicMock(return_value=device)
    monkeypatch.setattr(setup, "open_capture", opener)
    image = tmp_path / "capture.png"
    assert (
        setup.main(
            [
                "capture",
                "--config",
                str(config),
                "--output",
                str(image),
                "--discard-frames",
                "2",
            ]
        )
        == 0
    )
    assert device.read.call_count == 3
    device.close.assert_called_once()
    assert np.array_equal(cv.imread(str(image)), frame)
    result = json.loads(capsys.readouterr().out)
    assert result["backend"] == "picamera2"
    assert "received_at" in result and "captured_at" not in result


@pytest.mark.parametrize("failure", [TimeoutError("no frame"), RuntimeError("camera lost")])
def test_capture_failure_releases_device_without_saving(
    bench_paths, tmp_path, monkeypatch, failure
):
    _, _, config = bench_paths
    device = MagicMock()
    device.read.side_effect = failure
    monkeypatch.setattr(setup, "open_capture", lambda *_: device)
    image = tmp_path / "capture.png"
    with pytest.raises(SystemExit) as error:
        setup.main(["capture", "--config", str(config), "--output", str(image)])
    assert error.value.code == 2
    device.close.assert_called_once()
    assert not image.exists()


def test_existing_capture_is_preserved_before_camera_opens(bench_paths, tmp_path, monkeypatch):
    _, _, config = bench_paths
    image = tmp_path / "capture.png"
    image.write_bytes(b"existing evidence")
    monkeypatch.setattr(setup, "open_capture", lambda *_: pytest.fail("Opened camera"))
    with pytest.raises(SystemExit):
        setup.main(["capture", "--config", str(config), "--output", str(image)])
    assert image.read_bytes() == b"existing evidence"


def test_region_reference_and_check_workflow_preserves_arm_config(bench_paths, tmp_path, capsys):
    source, working, config = bench_paths
    setup.main(["init", "--source", str(source), "--output", str(working)])
    assert setup.main(["check", "--bench", str(working), "--config", str(config)]) == 1
    bench = json.loads(working.read_text())
    labels = ["empty", *(tool["id"] for tool in bench["tools"])]
    for index, label in enumerate(labels):
        frame = np.full((480, 640, 3), (index * 40, 40, 200), dtype=np.uint8)
        image = tmp_path / f"raw-{label}.png"
        assert cv.imwrite(str(image), frame)
        for region in bench["vision"]["regions"]:
            output = tmp_path / "refs" / f"{region}-{label}.png"
            assert (
                setup.main(
                    [
                        "reference",
                        "--bench",
                        str(working),
                        "--image",
                        str(image),
                        "--region",
                        region,
                        "--label",
                        label,
                        "--output",
                        str(output),
                    ]
                )
                == 0
            )
            _, _, width, height = bench["vision"]["regions"][region]
            assert cv.imread(str(output)).shape == (height, width, 3)
    assert setup.main(["check", "--bench", str(working), "--config", str(config)]) == 0
    calibrated = json.loads(working.read_text())
    assert calibrated["arm"] == bench["arm"]
    assert all(
        Path(path).is_absolute()
        for refs in calibrated["vision"]["references"].values()
        for path in refs.values()
    )

    raw = tmp_path / "raw-empty.png"
    before = raw.read_bytes()
    overlay = tmp_path / "overlay.png"
    assert (
        setup.main(
            [
                "overlay",
                "--bench",
                str(working),
                "--image",
                str(raw),
                "--output",
                str(overlay),
            ]
        )
        == 0
    )
    assert overlay.exists() and raw.read_bytes() == before

    assert (
        setup.main(
            [
                "region",
                "--bench",
                str(working),
                "--image",
                str(raw),
                "--name",
                "pliers",
                "--box",
                "430",
                "60",
                "180",
                "160",
            ]
        )
        == 0
    )
    updated = json.loads(working.read_text())
    assert updated["vision"]["references"].get("pliers", {}) == {}
    assert updated["vision"]["references"]["tray"] == calibrated["vision"]["references"]["tray"]
    assert updated["arm"] == calibrated["arm"]
    assert setup.main(["check", "--bench", str(working), "--config", str(config)]) == 1
    assert json.loads(source.read_text()) == bench
    capsys.readouterr()


def test_failed_bench_replace_preserves_original(tmp_path, monkeypatch):
    path = write_json(tmp_path / "bench.json", {"unchanged": True})
    original = path.read_bytes()

    def fail(*_):
        raise PermissionError("injected replacement failure")

    monkeypatch.setattr(setup.os, "replace", fail)
    with pytest.raises(PermissionError):
        setup.update_bench(path, {"unchanged": False})
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []
