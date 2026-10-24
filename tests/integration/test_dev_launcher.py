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
