from contextlib import AsyncExitStack

import httpx
import pytest_asyncio
from workbench_arm_controller.app import create_app as arm_app
from workbench_assistant.app import create_app as assistant_app
from workbench_common import Store, settings
from workbench_coordinator.app import create_app as coordinator_app
from workbench_vision.app import create_app as vision_app
from workbench_voice.app import create_app as voice_app


@pytest_asyncio.fixture
async def stack(tmp_path):
    config = settings()
    config["mode"] = "mock"
    config["arm"]["mock_step_seconds"] = 0.005
    stores = [Store("arm", tmp_path), Store("coordinator", tmp_path)]
    apps = {
        "vision": vision_app(config),
        "voice": voice_app(config),
        "assistant": assistant_app(config),
        "arm-controller": arm_app(config, stores[0]),
    }
    transports = {}
    calls = []
    faults = {}

    async def dispatch(request):
        calls.append((request.method, str(request.url)))
        key = (request.method, str(request.url))
        if key in faults:
            return await faults[key](request)
        base = f"{request.url.scheme}://{request.url.netloc.decode()}"
        return await transports[base].handle_async_request(request)

    async with AsyncExitStack() as context:
        bus = await context.enter_async_context(
            httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
        )
        apps["coordinator"] = coordinator_app(config, stores[1], bus)
        for name, app in apps.items():
            transports[config["services"][name]] = httpx.ASGITransport(app=app)
            await context.enter_async_context(app.router.lifespan_context(app))
        yield bus, config, calls, faults, stores
    for store in stores:
        store.close()
