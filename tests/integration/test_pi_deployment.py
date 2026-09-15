import importlib.util
import json
from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pi_deployment", ROOT / "scripts/pi.py")
pi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pi)


def runtime():
    return pi.read_json(ROOT / "config/development/pi.json")


def make_bundle(tmp_path):
    output = tmp_path / "bundle"
    pi.prepare(
        output,
        "/home/operator/workbench",
        "operator",
        ROOT / "config/development/pi.json",
        ROOT / "config/workbench/default.json",
    )
    return output


def test_bundle_uses_snapshots_mock_mode_and_persistent_separate_state(tmp_path):
    bundle = make_bundle(tmp_path)
    report = pi.check_bundle(bundle)
    assert report["offline_checks_ok"] is True
    assert report["pi_validation"] == "unperformed"
    assert report["state_directory"] == "/home/operator/workbench/.runtime/pi-mock"
    assert pi.read_json(bundle / "runtime.json") == runtime()
    assert pi.read_json(bundle / "bench.json") == pi.read_json(
        ROOT / "config/workbench/default.json"
    )
    unit = (bundle / "workbench-mock.service").read_text()
    assert "--backend-only --mode mock" in unit
    assert "Restart=no\n" in unit
    assert "KillMode=mixed\n" in unit
    assert "Environment=WEB_CONCURRENCY=1\n" in unit
    assert "\n[Install]\n" not in unit


def test_prepare_refuses_existing_output_and_hardware_profile(tmp_path):
    bundle = make_bundle(tmp_path)
    original = (bundle / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        make_bundle(tmp_path)
    assert (bundle / "manifest.json").read_bytes() == original
    config = runtime()
    config["mode"] = "hardware"
    source = tmp_path / "hardware.json"
    source.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="mock mode"):
        pi.prepare(tmp_path / "unsafe", "/srv/workbench", "operator", source, bundle / "bench.json")
    assert not (tmp_path / "unsafe").exists()


def test_bundle_detects_changed_files_and_rejects_altered_unit_even_with_updated_hash(tmp_path):
    bundle = make_bundle(tmp_path)
    path = bundle / "workbench-mock.service"
    path.write_text(path.read_text().replace("--mode mock", "--mode hardware"))
    with pytest.raises(ValueError, match="files changed"):
        pi.check_bundle(bundle)
    manifest = pi.read_json(bundle / "manifest.json")
    manifest["files"][path.name] = pi.digest(path)
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="mock-only template"):
        pi.check_bundle(bundle)


@pytest.mark.parametrize(
    "root", ["relative", "/srv/work bench", "/srv/%n", "/srv/a/../b", "/srv/a\nb"]
)
def test_unit_rejects_ambiguous_or_expanding_target_paths(root):
    with pytest.raises(ValueError):
        pi.render_unit(root, "operator")


@pytest.mark.parametrize(
    "address",
    [
        "http://0.0.0.0:8101",
        "http://example.com:8101",
        "http://127.0.0.1:8101/extra",
        "http://127.0.0.1:8101?x=y",
        "http://user@127.0.0.1:8101",
        "http://127.0.0.1:8100",
    ],
)
def test_invalid_service_addresses_are_rejected_before_network_io(address):
    config = runtime()
    config["services"]["vision"] = address
    fetch = MagicMock()
    with pytest.raises(ValueError):
        pi.inspect_health(config, "mock", fetch=fetch)
    fetch.assert_not_called()


def responses(config):
    rows = {
        url + "/health": {"service": name, "mode": "mock", "ready": True}
        for name, url in config["services"].items()
    }
    rows[config["services"]["arm-controller"] + "/status"] = {
        "state": "idle",
        "recovery_required": False,
    }
    rows[config["services"]["coordinator"] + "/status"] = {
        "mode": "mock",
        "operation": None,
        "recovery_required": False,
    }
    return rows


def test_health_only_reads_and_does_not_confuse_service_health_with_recovery():
    config = runtime()
    rows = responses(config)
    fetch = MagicMock(side_effect=lambda url, timeout: rows[url])
    report = pi.inspect_health(config, "mock", fetch=fetch)
    assert report["checks_ok"] is True
    assert "not established" in report["physical_readiness"]
    assert fetch.call_count == 7
    assert all(call.args[0].endswith(("/health", "/status")) for call in fetch.call_args_list)
    rows[config["services"]["arm-controller"] + "/status"]["recovery_required"] = True
    report = pi.inspect_health(config, "mock", fetch=fetch)
    assert report["services_ready"] is True
    assert report["checks_ok"] is False
    assert report["recovery_required"] is True


@pytest.mark.parametrize("failure", ["unready", "wrong-mode", "wrong-service", "missing-status"])
def test_health_fails_closed_for_unready_mismatched_or_unreachable_services(failure):
    config = runtime()
    rows = responses(config)
    arm_health = rows[config["services"]["arm-controller"] + "/health"]
    if failure == "unready":
        arm_health["ready"] = False
    elif failure == "wrong-mode":
        arm_health["mode"] = "hardware"
    elif failure == "wrong-service":
        arm_health["service"] = "vision"
    else:
        rows[config["services"]["arm-controller"] + "/status"] = None

    def fetch(url, timeout):
        if rows[url] is None:
            raise TimeoutError("no response")
        return rows[url]

    assert pi.inspect_health(config, "mock", fetch=fetch)["checks_ok"] is False


def test_health_does_not_report_busy_arm_as_idle():
    config = runtime()
    rows = responses(config)
    rows[config["services"]["arm-controller"] + "/status"]["state"] = "moving"
    report = pi.inspect_health(config, "mock", fetch=lambda url, _: rows[url])
    assert report["services_ready"] is True
    assert report["workflow_idle"] is False
    assert report["checks_ok"] is False


@pytest.mark.parametrize("failure", [BadStatusLine("invalid status"), IncompleteRead(b"{", 12)])
@pytest.mark.parametrize("endpoint", ["health", "status"])
def test_malformed_http_response_is_reported_and_remaining_checks_continue(failure, endpoint):
    config = runtime()
    rows = responses(config)
    failed_service = "vision" if endpoint == "health" else "arm-controller"
    failed_url = config["services"][failed_service] + "/" + endpoint

    def fetch(url, timeout):
        if url == failed_url:
            raise failure
        return rows[url]

    reader = MagicMock(side_effect=fetch)
    report = pi.inspect_health(config, "mock", fetch=reader)
    assert reader.call_count == 7
    assert report["checks_ok"] is False
    assert report["services"]["coordinator"]["ready"] is True
    assert "coordinator" in report["status"]
    if endpoint == "health":
        assert report["services"][failed_service]["responding"] is False
        assert report["services"][failed_service]["detail"] == str(failure)
    else:
        assert report["status_errors"][failed_service] == str(failure)
        assert report["recovery_required"] is None
        assert report["workflow_idle"] is None


def test_http_reader_disables_redirects_proxies_and_uses_get(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"ready": true}'
    opener = MagicMock()
    opener.open.return_value = response
    factory = MagicMock(return_value=opener)
    monkeypatch.setattr(pi.urllib.request, "build_opener", factory)
    assert pi.get_json("http://127.0.0.1:8100/health", 0.5) == {"ready": True}
    proxy, redirect = factory.call_args.args
    assert proxy.proxies == {}
    assert redirect.redirect_request(None, None, 302, None, None, "https://example.com") is None
    assert opener.open.call_args.args[0].method == "GET"
    assert opener.open.call_args.kwargs["timeout"] == 0.5


def test_cli_invalid_timeout_and_changed_bundle_return_errors(tmp_path):
    with pytest.raises(SystemExit) as error:
        pi.main(["health", "--timeout", "nan"])
    assert error.value.code == 2
    bundle = make_bundle(tmp_path)
    assert pi.main(["check", "--bundle", str(bundle)]) == 0
    (bundle / "runtime.json").write_text("{}")
    with pytest.raises(SystemExit) as error:
        pi.main(["check", "--bundle", str(bundle)])
    assert error.value.code == 2
