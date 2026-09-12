import asyncio
from uuid import uuid4

import httpx
import pytest
from workbench_arm_controller.app import create_app
from workbench_arm_controller.driver import validate_calibration
from workbench_common import Store, settings


def test_uncalibrated_hardware_is_disabled():
    with pytest.raises(ValueError, match="disabled"):
        validate_calibration(settings())


@pytest.mark.parametrize("speed", [0, -1, 301])
def test_unbounded_speed_rejected(speed):
    cfg = settings()
    cfg["workbench"]["arm"].update(calibrated=True, speed=speed)
    with pytest.raises(ValueError, match="Speed"):
        validate_calibration(cfg)


async def test_restart_preserves_deduplication_and_requires_recovery(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    cfg["arm"]["mock_step_seconds"] = 0.001
    store = Store("arm", tmp_path)
    operation_id = str(uuid4())
    command = {"operation_id": operation_id, "tool_id": "pliers"}
    store.put(f"operation:{operation_id}", command)
    store.put("status", {"state": "moving", **command})
    app = create_app(cfg, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/status")).json()["recovery_required"]
            assert (
                await client.post("/execute", json={**command, "operation_id": str(uuid4())})
            ).status_code == 409
            assert (await client.post("/recover", json={"acknowledged": True})).status_code == 200
            assert (await client.post("/execute", json=command)).status_code == 409
            assert (
                await client.post("/execute", json={**command, "operation_id": str(uuid4())})
            ).status_code == 202
            await asyncio.sleep(0.03)
    store.close()


def test_hardware_mode_has_no_mock_vision_routes():
    from workbench_vision.app import create_app as vision_app

    cfg = settings()
    cfg["mode"] = "hardware"
    assert not any(route.path.startswith("/mock") for route in vision_app(cfg).routes)
