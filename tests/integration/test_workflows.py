import asyncio
import io
import wave
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from workbench_contracts import Observation, Observations, now
from workbench_coordinator.app import check_observations


async def wait_for(bus, base, operation_id, states=None):
    states = states or {"completed", "rejected", "failed", "clarification", "stopped", "status"}
    for _ in range(250):
        result = await bus.get(f"{base}/operations/{operation_id}")
        if result.json()["state"] in states:
            return result.json()
        await asyncio.sleep(0.02)
    pytest.fail(f"Operation did not reach {states}: {result.text}")


async def submit(stack, text="Grab me the crescent wrench", operation_id=None):
    bus, cfg, *_ = stack
    body = {"text": text, "operation_id": str(operation_id or uuid4())}
    response = await bus.post(cfg["services"]["coordinator"] + "/requests", json=body)
    assert response.status_code == 202, response.text
    return body


async def test_delivery_and_idempotence(stack):
    bus, cfg, calls, *_ = stack
    base = cfg["services"]["coordinator"]
    body = await submit(stack)
    result = await wait_for(bus, base, body["operation_id"])
    assert result["state"] == "completed"
    assert result["tool_id"] == "adjustable_wrench"
    assert (await bus.post(base + "/requests", json=body)).json()["state"] == "completed"
    executes = [c for c in calls if c == ("POST", cfg["services"]["arm-controller"] + "/execute")]
    assert len(executes) == 1
    assert (
        await bus.post(base + "/requests", json={**body, "text": "Grab me the pliers"})
    ).status_code == 409
    scene = (await bus.get(cfg["services"]["vision"] + "/observations")).json()
    assert not scene["tray_clear"]
    assert scene["tools"][0]["location"] == "tray"


@pytest.mark.parametrize(
    "text",
    ["Grab me the hammer", "Grab me wrench and pliers", "Don't grab the wrench", "hello", "wrench"],
)
async def test_ambiguous_or_unsupported_requests_never_move(stack, text):
    bus, cfg, calls, *_ = stack
    body = await submit(stack, text)
    assert (await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"]))[
        "state"
    ] == "clarification"
    assert not any(url.endswith("/execute") for _, url in calls)


@pytest.mark.parametrize("fault", ["stale", "missing", "uncertain", "camera", "tray", "confidence"])
async def test_bad_observations_block_motion(stack, fault):
    bus, cfg, calls, *_ = stack
    vision = cfg["services"]["vision"]
    scene = (await bus.get(vision + "/observations")).json()
    scene.pop("captured_at")
    if fault == "stale":
        scene["age_seconds"] = 10
    elif fault == "camera":
        scene["camera_ok"] = False
    elif fault == "tray":
        scene["tray_clear"] = False
    elif fault == "confidence":
        scene["tools"][0]["confidence"] = 0.1
    else:
        scene["tools"][0]["location"] = fault
    assert (await bus.put(vision + "/mock/scene", json=scene)).status_code == 200
    body = await submit(stack)
    assert (await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"]))[
        "state"
    ] == "rejected"
    assert not any(url.endswith("/execute") for _, url in calls)


async def test_stop_and_explicit_recovery(stack):
    bus, cfg, *_ = stack
    cfg["arm"]["mock_step_seconds"] = 0.1
    base = cfg["services"]["coordinator"]
    body = await submit(stack)
    await wait_for(bus, base, body["operation_id"], {"moving"})
    busy = await bus.post(base + "/requests", json={"text": "Grab me the pliers"})
    assert busy.status_code == 409
    stopped = await bus.post(base + "/stop")
    assert stopped.json()["recovery_required"]
    assert stopped.json()["operation"]["state"] == "stopped"
    assert (
        await bus.post(base + "/requests", json={"text": "Grab me the pliers"})
    ).status_code == 409
    await asyncio.sleep(0.15)
    assert (await bus.post(base + "/recover", json={"acknowledged": False})).status_code == 422
    assert (await bus.post(base + "/recover", json={"acknowledged": True})).status_code == 200
    body = await submit(stack, "Grab me the pliers")
    assert (await wait_for(bus, base, body["operation_id"]))["state"] == "completed"


async def test_uncertain_dispatch_never_retries(stack):
    bus, cfg, calls, faults, *_ = stack
    endpoint = cfg["services"]["arm-controller"] + "/execute"

    async def timeout(request):
        raise httpx.ReadTimeout("Response lost", request=request)

    faults[("POST", endpoint)] = timeout
    body = await submit(stack)
    result = await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"])
    assert result["state"] == "failed"
    assert (await bus.get(cfg["services"]["coordinator"] + "/status")).json()["recovery_required"]
    assert calls.count(("POST", endpoint)) == 1


async def test_offline_arm_rejects_before_dispatch(stack):
    bus, cfg, calls, faults, *_ = stack

    async def unavailable(request):
        raise httpx.ConnectError("Disconnected", request=request)

    faults[("GET", cfg["services"]["arm-controller"] + "/health")] = unavailable
    body = await submit(stack)
    result = await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"])
    assert result["state"] == "rejected"
    assert not any(url.endswith("/execute") for _, url in calls)


async def test_failed_delivery_verification_requires_recovery(stack):
    bus, cfg, calls, faults, *_ = stack

    async def no_delivery(request):
        return httpx.Response(200, json={})

    faults[("POST", cfg["services"]["vision"] + "/mock/deliver/adjustable_wrench")] = no_delivery
    body = await submit(stack)
    result = await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"])
    assert result["state"] == "failed"
    assert (await bus.get(cfg["services"]["coordinator"] + "/status")).json()["recovery_required"]
    assert ("POST", cfg["services"]["arm-controller"] + "/stop") in calls


async def test_mode_mismatch_rejects_hardware(stack):
    bus, cfg, calls, faults, *_ = stack

    async def hardware(request):
        return httpx.Response(
            200, json={"service": "arm-controller", "mode": "hardware", "ready": True}
        )

    faults[("GET", cfg["services"]["arm-controller"] + "/health")] = hardware
    body = await submit(stack)
    assert (await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"]))[
        "state"
    ] == "rejected"
    assert not any(url.endswith("/execute") for _, url in calls)


async def test_voice_wav_and_invalid_input(stack):
    bus, cfg, *_ = stack
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 8000)
    result = await bus.post(
        cfg["services"]["coordinator"] + "/transcriptions", content=audio.getvalue()
    )
    assert result.json()["text"] == "Grab me the screwdriver"
    assert (
        await bus.post(cfg["services"]["voice"] + "/transcribe", content=b"not wav")
    ).status_code == 422


async def test_status_is_read_only(stack):
    bus, cfg, calls, *_ = stack
    body = await submit(stack, "status")
    assert (await wait_for(bus, cfg["services"]["coordinator"], body["operation_id"]))[
        "state"
    ] == "status"
    assert not any(url.endswith("/execute") for _, url in calls)


def test_future_timestamp_rejected():
    from workbench_common import settings

    obs = Observations(
        captured_at=now() + timedelta(seconds=10),
        camera_ok=True,
        tray_clear=True,
        tools=[Observation(tool_id="pliers", location="source", confidence=1)],
    )
    with pytest.raises(ValueError, match="stale"):
        check_observations(obs, settings(), "pliers", "source")
