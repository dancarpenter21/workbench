"""Checklist reviews never program output or authorize motion."""

import pytest
from fastapi import HTTPException
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_common import Store, settings
from workbench_contracts import CommissionAcquire, CommissionChecklistUpdate


def test_reviews_persist_and_are_separate_from_bounds_and_motion_configuration(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    store = Store("checklist", tmp_path)
    control = LiveCommissioning(cfg)
    control.attach(store)
    before = control.bounds()
    context = control.checklist().context_key
    control.update_checklist(
        CommissionChecklistUpdate(context_key=context, step="startup", completed=True)
    )
    control.update_checklist(
        CommissionChecklistUpdate(context_key=context, step="directions", completed=True)
    )
    assert control.output is None and control.trial is None
    assert control.bounds() == before
    assert control.checklist().updated_at is not None
    other_store = Store("checklist", tmp_path)
    restored = LiveCommissioning(cfg)
    restored.attach(other_store)
    try:
        assert restored.checklist().completed == {"startup": True, "directions": True}
        restored.update_checklist(
            CommissionChecklistUpdate(context_key=context, step="startup", completed=False)
        )
        assert restored.checklist().completed == {"startup": False, "directions": True}
        restored.monitor.active_assembly_id = "Another assembly"
        assert restored.checklist().completed == {}
        with pytest.raises(HTTPException, match="setup changed"):
            restored.update_checklist(
                CommissionChecklistUpdate(context_key=context, step="startup", completed=True)
            )
        restored.monitor.active_assembly_id = None
        physical_cfg = settings()
        physical_cfg["mode"] = "hardware"
        physical_cfg["arm"]["backend"] = "pca9685"
        physical = LiveCommissioning(physical_cfg)
        physical.attach(other_store)
        try:
            assert physical.checklist().completed == {}
            physical_context = physical.checklist().context_key
            physical.update_checklist(
                CommissionChecklistUpdate(
                    context_key=physical_context, step="hardware", completed=True
                )
            )
            physical_cfg["arm"]["i2c_bus"] = 99
            assert physical.checklist().completed == {}
        finally:
            physical.close()
            physical.monitor.close()
    finally:
        restored.close()
        restored.monitor.close()
        other_store.close()
        control.close()
        control.monitor.close()
        store.close()


@pytest.mark.asyncio
async def test_checklist_routes_validate_steps_and_reject_powered_trial_updates(stack):
    bus, cfg, _, _, _ = stack
    url = cfg["services"]["coordinator"] + "/commissioning"
    result = await bus.get(url + "/checklist")
    assert result.status_code == 200
    context = result.json()["context_key"]
    payload = dict(context_key=context, step="hardware", completed=True)
    result = await bus.post(url + "/checklist", json=payload)
    assert result.status_code == 200
    assert result.json()["completed"] == {"hardware": True}
    result = await bus.post(url + "/checklist", json={**payload, "step": "bounds"})
    assert result.status_code == 422  # Automatic checks cannot be manually completed.
    draft = (
        await bus.post(
            url + "/drafts", json={"assembly_id": "Assembly not named", "observer": "Test"}
        )
    ).json()
    result = await bus.post(
        url + "/acquire",
        json=CommissionAcquire(draft_id=draft["id"], joint_id="base").model_dump(mode="json"),
    )
    assert result.status_code == 200
    result = await bus.post(url + "/checklist", json={**payload, "step": "speeds"})
    assert result.status_code == 409
    assert (await bus.get(url + "/checklist")).json()["completed"] == {"hardware": True}
