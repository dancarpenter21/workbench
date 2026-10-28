import json

import httpx
import pytest
from workbench_assistant.harness import HarnessModelLoop
from workbench_coordinator.harness import defaults, definitions


async def test_model_loop_uses_credentials_tools_and_on_demand_images_without_recording_them(
    monkeypatch,
):
    config = defaults().model_copy(update={"model": "fixture", "supports_images": True})
    monkeypatch.setenv(config.credential_env, "secret-test-key")
    requests, events = [], []

    def endpoint(request):
        assert request.headers["authorization"] == "Bearer secret-test-key"
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "image-1",
                        "type": "function",
                        "function": {"name": "capture_image", "arguments": "{}"},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": "Observed the scene."}
        return httpx.Response(
            200, json={"choices": [{"message": message}], "usage": {"total_tokens": 10}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    monkeypatch.setattr("workbench_assistant.harness.httpx.AsyncClient", lambda **_: client)

    async def call(name, arguments):
        assert name == "capture_image" and arguments == {}
        return {"image": "ZmFrZQ==", "simulated": True, "captured_at": "fixture"}

    async def emit(kind, data):
        events.append((kind, data))

    await HarnessModelLoop().run(config, "Inspect", definitions(config), call, emit)
    assert "image_url" not in json.dumps(requests[0])
    assert "data:image/jpeg;base64,ZmFrZQ==" in json.dumps(requests[1])
    assert "ZmFrZQ==" not in json.dumps(events)
    assert "secret-test-key" not in json.dumps(events)


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_errors_do_not_leak_response_bodies(monkeypatch, status):
    config = defaults().model_copy(update={"model": "fixture"})
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="secret response content")
        )
    )
    monkeypatch.setattr("workbench_assistant.harness.httpx.AsyncClient", lambda **_: client)

    async def unused(*args):
        pytest.fail("No tool should run")

    with pytest.raises(ValueError, match=f"HTTP {status}") as exc:
        await HarnessModelLoop().run(config, "Inspect", definitions(config), unused, unused)
    assert "secret" not in str(exc.value)
