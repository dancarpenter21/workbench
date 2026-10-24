"""Missing launch settings report setup steps without creating files or starting services."""

import importlib.util
from pathlib import Path

import pytest


def test_missing_launch_config_points_to_first_run_setup_without_creating_files(tmp_path):
    source = Path(__file__).resolve().parents[2] / "scripts/dev.py"
    spec = importlib.util.spec_from_file_location("workbench_dev_launcher", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "absent" / "workbench-config.json"
    with pytest.raises(SystemExit) as error:
        module.load_launch_config(path)
    message = str(error.value)
    assert str(path) in message
    assert "No applications started" in message
    assert "README.md#first-run-initialization" in message
    assert "config file alone does not establish commissioning" in message
    assert not path.parent.exists()


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts/dev.py"
    spec = importlib.util.spec_from_file_location("workbench_dev_launcher", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("WORKBENCH_CONFIG", raising=False)
    return module


@pytest.mark.parametrize("mode", ["hardware", "mock"])
def test_deleted_local_override_uses_mode_defaults(launcher, monkeypatch, capsys, mode):
    local = launcher.ROOT / ".runtime/hardware/workbench-config.json"
    monkeypatch.setenv("WORKBENCH_CONFIG", str(local))
    expected = "pi.json" if mode == "hardware" else "default.json"
    assert launcher.resolve_launch_config(mode) == launcher.ROOT / "config/development" / expected
    assert "Local configuration missing" in capsys.readouterr().out
    assert not local.exists()


def test_custom_missing_override_still_fails(launcher, monkeypatch):
    path = launcher.ROOT / "custom.json"
    monkeypatch.setenv("WORKBENCH_CONFIG", str(path))
    assert launcher.resolve_launch_config("hardware") == path
    with pytest.raises(SystemExit, match="Configuration file not found"):
        launcher.load_launch_config(path)


def test_existing_local_settings_are_preserved(launcher, monkeypatch):
    local = launcher.ROOT / ".runtime/hardware/workbench-config.json"
    local.parent.mkdir(parents=True)
    local.write_text("{}")
    assert launcher.resolve_launch_config("hardware") == local
    assert launcher.resolve_launch_config("mock") == (
        launcher.ROOT / "config/development/default.json"
    )
    monkeypatch.setenv("WORKBENCH_CONFIG", str(local))
    assert launcher.resolve_launch_config("hardware") == local
