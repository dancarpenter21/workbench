"""Browser API validation remains usable without importing a ROS runtime."""

import io
import json
import wave
from uuid import uuid4

import httpx
import pytest
from workbench_common import ROOT
from workbench_contracts import Health, MockScene, Operation, WorkbenchStatus
from workbench_coordinator.ros_gateway import create_app


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.operations = {}
        self.audio = []
        self.closed = False
        self.unavailable = False
        self.current = WorkbenchStatus(
            mode=cfg["mode"],
            operation=None,
            recovery_required=False,
            commissioning_ready=True,
            retrieval_enabled=True,
        )

    def status(self):
        if self.unavailable:
            raise RuntimeError("ROS coordinator status is unavailable or stale")
        return self.current

    def services(self):
        return [Health(service="coordinator", mode=self.cfg["mode"], ready=True)]

    def scene(self):
        return MockScene(tools=[])

    def diagnostics(self):
        return []

    async def submit(self, request):
        self.status()
        operation = Operation(id=request.operation_id, text=request.text)
        self.operations[str(operation.id)] = operation
        return operation

    async def lookup(self, operation_id):
        return self.operations.get(operation_id)

    async def stop(self):
        self.current = self.current.model_copy(update={"recovery_required": True})
        return self.current

    async def recover(self):
        self.current = self.current.model_copy(update={"recovery_required": False})
        return self.current

    async def reset(self):
        return self.current

    async def transcribe(self, data):
        self.audio.append(data)
        return "fetch the screwdriver"

    def close(self):
        self.closed = True


@pytest.fixture
async def browser():
    cfg = json.loads((ROOT / "config/development/default.json").read_text())
    cfg["mode"] = "mock"
    bridge = Bridge(cfg)
    app = create_app(cfg, bridge_factory=lambda _: bridge)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as client:
            yield client, bridge
    assert bridge.closed


async def test_gateway_forwards_validated_request_and_operation_identity(browser):
    client, bridge = browser
    operation_id = str(uuid4())
    response = await client.post(
        "/requests", json={"operation_id": operation_id, "text": "fetch the pliers"}
    )
    assert response.status_code == 202
    assert response.json()["id"] == operation_id
    assert bridge.operations[operation_id].text == "fetch the pliers"
    assert (await client.get(f"/operations/{operation_id}")).json() == response.json()
    assert (await client.get(f"/operations/{uuid4()}")).status_code == 404


async def test_gateway_rejects_blank_text_and_malformed_operation_ids(browser):
    client, bridge = browser
    for body in ({"text": "   "}, {"text": "fetch pliers", "operation_id": "invalid"}):
        assert (await client.post("/requests", json=body)).status_code == 422
    assert not bridge.operations


async def test_gateway_reports_ros_loss_and_requires_recovery_acknowledgement(browser):
    client, bridge = browser
    assert (await client.post("/stop")).json()["recovery_required"]
    assert (await client.post("/recover", json={"acknowledged": False})).status_code == 422
    assert bridge.current.recovery_required
    assert not (await client.post("/recover", json={"acknowledged": True})).json()[
        "recovery_required"
    ]
    bridge.unavailable = True
    assert (await client.get("/status")).status_code == 503
    assert (await client.post("/requests", json={"text": "fetch pliers"})).status_code == 503
    assert not bridge.operations


async def test_gateway_validates_wav_before_ros_transcription(browser):
    client, bridge = browser
    assert (await client.post("/transcriptions", content=b"invalid")).status_code == 422
    assert not bridge.audio
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    response = await client.post("/transcriptions", content=buffer.getvalue())
    assert response.status_code == 200 and response.json()["text"] == "fetch the screwdriver"
    assert bridge.audio == [buffer.getvalue()]
