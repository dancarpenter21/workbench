import copy

import pytest
from fastapi import HTTPException
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_common import Store, settings
from workbench_contracts import RobotModelMeasurementsUpdate


@pytest.fixture
def models(tmp_path, monkeypatch):
    cfg = settings()
    cfg["mode"] = "hardware"
    cfg["arm"]["backend"] = "pca9685"
    store = Store("robot-model", tmp_path)
    control = LiveCommissioning(cfg)
    control.attach(store)
    monkeypatch.setattr(control, "open_output", lambda: pytest.fail("Model setup opened outputs"))
    monkeypatch.setattr(control.monitor, "poll", lambda **kw: pytest.fail("Model setup polled I2C"))
    yield control, store
    store.close()


def update(state, model=None):
    return RobotModelMeasurementsUpdate(
        context_key=state.context_key,
        revision=state.revision,
        model=state.model if model is None else model,
    )


def test_physical_drafts_persist_without_enabling_motion(models):
    control, store = models
    original = copy.deepcopy(control.config)
    state = control.robot_model.read()
    assert state.domain == "physical" and not state.valid
    assert state.model["links"][0]["size"] is None
    state.model["links"][0]["size"] = [0.1, 0.1, 0.05]
    saved = control.robot_model.save(update(state))
    assert saved.revision == 1 and not saved.valid
    restarted = LiveCommissioning(control.config)
    restarted.attach(store)
    assert restarted.robot_model.read() == saved
    assert control.config == original and not control.reserved
    assert control.bounds().joints == restarted.bounds().joints


def test_stale_setup_and_revision_never_overwrite(models):
    control, _ = models
    state = control.robot_model.read()
    saved = control.robot_model.save(update(state))
    with pytest.raises(HTTPException) as error:
        control.robot_model.save(update(state))
    assert error.value.status_code == 409
    request = update(saved)
    request.context_key += "-other-assembly"
    with pytest.raises(HTTPException):
        control.robot_model.save(request)
    assert control.robot_model.read() == saved


@pytest.mark.parametrize(
    "mutation",
    [
        lambda model: model.update(domain="simulated"),
        lambda model: model["links"][0].update(size=[float("nan"), 1, 1]),
        lambda model: model["links"][0].update(rpy=[0, "invalid", 0]),
        lambda model: model["links"][0].update(rpy=[0, 0]),
        lambda model: model["joints"][0].update(parent="tool0"),
        lambda model: model.update(links=[]),
    ],
)
def test_invalid_drafts_leave_previous_record_intact(models, mutation):
    control, _ = models
    state = control.robot_model.read()
    candidate = copy.deepcopy(state.model)
    mutation(candidate)
    with pytest.raises(HTTPException) as error:
        control.robot_model.save(update(state, candidate))
    assert error.value.status_code == 422
    assert control.robot_model.read() == state


def test_complete_flag_does_not_invent_missing_measurements(models):
    control, _ = models
    state = control.robot_model.read()
    state.model["complete"] = True
    saved = control.robot_model.save(update(state))
    assert not saved.valid and "coordinates" in saved.validation_error


def test_box_rotation_drafts_persist_independently_of_joint_frames(models):
    control, _ = models
    state = control.robot_model.read()
    original_joints = copy.deepcopy(state.model["joints"])
    state.model["links"][2]["rpy"] = [0, -0.5235987755982988, 0]
    saved = control.robot_model.save(update(state))
    assert saved.model["links"][2]["rpy"] == [0, -0.5235987755982988, 0]
    assert saved.model["joints"] == original_joints
    assert not saved.valid
    # Omitted orientations on legacy links retain their meaning, even on save.
    del saved.model["links"][2]["rpy"]
    legacy = control.robot_model.save(update(saved))
    assert "rpy" not in legacy.model["links"][2]
