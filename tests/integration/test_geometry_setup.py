import copy

import pytest
from fastapi import HTTPException
from movement_fixtures import geometry_data
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.movement import MovementSession
from workbench_common import Store, settings
from workbench_contracts import GeometryMeasurementsUpdate


@pytest.fixture
def measurements(tmp_path, monkeypatch):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("geometry", tmp_path)
    control = LiveCommissioning(cfg)
    control.attach(store)
    monkeypatch.setattr(control, "open_output", lambda: pytest.fail("Measurements opened outputs"))
    monkeypatch.setattr(
        control.monitor, "poll", lambda **kw: pytest.fail("Measurements polled I2C")
    )
    yield control, store
    store.close()


def update(control, geometry=None, activate=False):
    state = control.geometry.read()
    return GeometryMeasurementsUpdate(
        context_key=state.context_key,
        revision=state.revision,
        geometry=geometry if geometry is not None else state.geometry,
        activate=activate,
    )


def test_incomplete_capsules_save_without_pulse_bounds_or_hardware(measurements):
    control, _ = measurements
    original = control.bounds()
    state = control.geometry.read()
    assert not state.valid and state.active_revision is None
    data = state.geometry
    data["capsules"][0]["radius_mm"] = 12
    saved = control.geometry.save(update(control, data))
    assert saved.revision == 1 and saved.geometry["capsules"][0]["radius_mm"] == 12
    assert not saved.valid and saved.active_revision is None
    assert control.bounds() == original and not control.reserved


def test_activation_and_later_draft_preserve_active_revision_on_restart(measurements):
    control, store = measurements
    state = control.geometry.save(update(control, geometry_data(), activate=True))
    assert state.valid and state.active_revision == 1
    assert MovementSession.from_config(control.config).model.revision == "measurements-1"
    draft = copy.deepcopy(state.geometry)
    draft["capsules"][0]["radius_mm"] = None
    state = control.geometry.save(update(control, draft))
    assert state.revision == 2 and state.active_revision == 1 and not state.valid
    restarted = LiveCommissioning(control.config)
    restarted.attach(store)
    restarted.geometry.apply_active()
    assert restarted.geometry.read().geometry["capsules"][0]["radius_mm"] is None
    assert MovementSession.from_config(restarted.config).model.revision == "measurements-1"


def test_invalid_activation_stale_revision_and_wrong_setup_leave_state_unchanged(measurements):
    control, _ = measurements
    initial = control.geometry.read()
    with pytest.raises(HTTPException) as exc:
        control.geometry.save(update(control, activate=True))
    assert exc.value.status_code == 422 and control.geometry.read() == initial
    stale = update(control)
    control.geometry.save(stale)
    saved = control.geometry.read()
    for request in (stale, update(control).model_copy(update={"context_key": "another-arm"})):
        with pytest.raises(HTTPException) as exc:
            control.geometry.save(request)
        assert exc.value.status_code == 409 and control.geometry.read() == saved


def test_storage_failure_never_changes_active_model(measurements, monkeypatch):
    control, store = measurements
    control.geometry.save(update(control, geometry_data(), activate=True))
    before = copy.deepcopy(control.config["arm"]["movement_geometry_data"])

    def fail(*args):
        raise OSError("Storage failed")

    monkeypatch.setattr(store, "put", fail)
    with pytest.raises(OSError):
        control.geometry.save(update(control, geometry_data(), activate=True))
    assert control.config["arm"]["movement_geometry_data"] == before


def test_changed_assembly_does_not_inherit_active_geometry(measurements):
    control, _ = measurements
    control.geometry.save(update(control, geometry_data(), activate=True))
    control.monitor.active_assembly_id = "new assembly"
    assert control.geometry.read().active_revision is None
    control.geometry.apply_active()
    assert "movement_geometry_data" not in control.config["arm"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(joints=None),
        lambda d: d.update(capsules=[None]),
        lambda d: d["joints"][0].update(pulse_angle_deg="invalid"),
        lambda d: d["capsules"][0].update(a_mm=[0, 0]),
        lambda d: d.update(domain="physical"),
    ],
)
def test_malformed_drafts_cannot_break_the_form(measurements, change):
    control, _ = measurements
    data = geometry_data()
    change(data)
    with pytest.raises(HTTPException) as exc:
        control.geometry.save(update(control, data))
    assert exc.value.status_code == 422 and control.geometry.read().revision == 0


async def test_geometry_api_proxies_save_and_blocks_activation_during_jog(stack):
    bus, cfg, calls, _, _ = stack
    url = cfg["services"]["coordinator"]
    original = (await bus.get(url + "/commissioning/geometry")).json()
    payload = {
        "context_key": original["context_key"],
        "revision": 0,
        "geometry": geometry_data(),
        "activate": False,
    }
    saved = await bus.post(url + "/commissioning/geometry", json=payload)
    assert saved.status_code == 200 and saved.json()["valid"]
    acquired = await bus.post(url + "/manual/session", json={"ready_acknowledged": True})
    assert acquired.status_code == 200
    payload.update(revision=1, activate=True)
    assert (await bus.post(url + "/commissioning/geometry", json=payload)).status_code == 409
    assert (await bus.get(url + "/commissioning/geometry")).json()["active_revision"] is None
    await bus.post(url + "/manual/release", json={"token": acquired.json()["token"]})
    activated = await bus.post(url + "/commissioning/geometry", json=payload)
    assert activated.status_code == 200 and activated.json()["active_revision"] == 2
    assert not any(
        method == "POST" and endpoint.endswith(("/execute", "/commissioning/initialize"))
        for method, endpoint in calls
    )
