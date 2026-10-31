"""Harness tests never access physical I2C or energize the arm."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workbench_arm_controller.app import create_app as arm_app
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.harness import HarnessExecutor
from workbench_arm_controller.manual import ManualControl
from workbench_common import Store, settings
from workbench_contracts import (
    CommissionPulseBound,
    HarnessDispatch,
    HarnessEnable,
    HarnessRunRequest,
    HarnessStep,
    now,
)
from workbench_coordinator.harness import definitions
from workbench_coordinator.ros_gateway import create_app


@pytest.fixture
def rig(tmp_path):
    cfg = settings()
    cfg["mode"] = "mock"
    cfg["arm"]["backend"] = "pca9685"
    cfg["arm"]["claw_current"] = {"scale_verified": False, "shunt_ohms": None}
    clock = [0.0]
    manual = ManualControl(cfg, clock=lambda: clock[0])
    store = Store("harness-test", tmp_path)
    commissioning = LiveCommissioning(cfg)
    commissioning.attach(store)
    bounds = commissioning.bounds()
    for joint in bounds.joints.values():
        for kind, target in (("minimum", 1400), ("maximum", 1600)):
            setattr(
                joint,
                kind,
                CommissionPulseBound(
                    requested_us=target,
                    encoded_us=target,
                    observed_at=now(),
                    trial_id="simulated-fixture",
                    command_id="simulated-fixture",
                ),
            )
    executor = HarnessExecutor(manual, clock=lambda: clock[0])
    executor.attach(store)
    yield executor, manual, bounds, clock
    store.close()


def enable(executor, bounds):
    return executor.enable(
        HarnessEnable(
            supervised=True,
            acknowledge_unverified_scale=True,
            claw_open_us=1450,
            claw_close_us=1550,
        ),
        bounds,
    )


def sequence(token, *steps, operation_id=None):
    return HarnessDispatch(
        token=token, operation_id=operation_id or uuid4(), steps=[HarnessStep(**s) for s in steps]
    )


def advance(executor, clock, count=100):
    for _ in range(count):
        clock[0] += 0.02
        executor.manual.monitor.poll()
        executor.tick()


def test_order_bounds_command_evidence_and_duplicate_id(rig):
    executor, manual, bounds, clock = rig
    lease = enable(executor, bounds)
    request = sequence(
        lease.token,
        {"action": "move_joint", "joint_id": "base", "target_pulse_us": 1510},
        {"action": "close_claw"},
    )
    initial_claw = manual.targets["claw"]
    executor.submit(request)
    advance(executor, clock, 3)
    assert manual.targets["base"] > 1500
    assert manual.targets["claw"] == initial_claw
    advance(executor, clock, 40)
    result = executor.lookup(request.operation_id)
    assert result.state == "completed"
    assert result.completed_steps == 2
    assert result.simulated
    assert "unverified" in result.message
    before = dict(manual.targets)
    assert executor.submit(request).state == "completed"
    assert manual.targets == before
    changed = request.model_copy(update={"steps": [HarnessStep(action="open_claw")]})
    with pytest.raises(HTTPException, match="different steps"):
        executor.submit(changed)


def test_entire_sequence_validated_before_output(rig):
    executor, manual, bounds, _ = rig
    lease = enable(executor, bounds)
    with pytest.raises(HTTPException, match="outside saved bounds"):
        executor.submit(
            sequence(
                lease.token,
                {"action": "open_claw"},
                {"action": "move_joint", "joint_id": "base", "target_pulse_us": 1900},
            )
        )
    assert all(v == 1500 for v in manual.targets.values())
    assert executor.operation is None


def test_no_nominal_fallback_and_no_implicit_scale_acknowledgement(rig):
    executor, _, bounds, _ = rig
    with pytest.raises(HTTPException, match="unverified"):
        executor.enable(
            HarnessEnable(supervised=True, claw_open_us=1450, claw_close_us=1550), bounds
        )
    bounds.joints["claw"].minimum = None
    with pytest.raises(HTTPException, match="Save both"):
        enable(executor, bounds)
    assert not executor.active


def test_heartbeat_expiry_cancels_without_replay_and_retains_hold(rig):
    executor, manual, bounds, clock = rig
    lease = enable(executor, bounds)
    executor.submit(sequence(lease.token, {"action": "close_claw"}))
    advance(executor, clock, 2)
    before = dict(manual.targets)
    clock[0] = 4
    executor.tick()
    assert not executor.active
    assert manual.recovery_required
    assert manual.targets == before
    assert executor.operation.state == "stopped"
    with pytest.raises(HTTPException):
        executor.heartbeat(lease)


def test_restart_interrupts_operation_and_requires_recovery(rig):
    executor, manual, bounds, _ = rig
    lease = enable(executor, bounds)
    executor.submit(sequence(lease.token, {"action": "close_claw"}))
    restored = HarnessExecutor(manual)
    restored.attach(executor.store)
    assert not restored.active
    assert restored.operation.state == "interrupted"
    assert manual.recovery_required


def test_current_fault_stops_and_cannot_be_retried(rig):
    executor, manual, bounds, clock = rig
    lease = enable(executor, bounds)
    executor.submit(sequence(lease.token, {"action": "close_claw"}))
    advance(executor, clock, 2)
    before = dict(manual.targets)
    manual.monitor.trip("INA219 failed")
    executor.tick()
    assert manual.monitor.shutdown_required  # Arm worker performs physical shutdown.
    assert not executor.active
    assert manual.targets == before
    with pytest.raises(HTTPException):
        executor.submit(sequence(lease.token, {"action": "open_claw"}))


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    cfg = settings()
    cfg["mode"] = "mock"
    cfg["arm"]["backend"] = "pca9685"
    owner = arm_app(cfg)
    handlers = {r.endpoint.__name__: r.endpoint for r in owner.routes if hasattr(r, "endpoint")}

    class Bridge:
        def scene(self):
            from workbench_contracts import MockScene

            return MockScene(tools=[])

        def harness_scene(self):
            return {"tools": [], "simulated": True, "metric_pose_available": False}

        def latest(self):
            return None

        async def control(self, binding, arguments):
            return await handlers[binding["endpoint"]](**arguments)

        async def stop(self):
            from workbench_contracts import WorkbenchStatus

            await handlers["stop"]()
            return WorkbenchStatus(mode="mock", operation=None, recovery_required=True)

        def close(self):
            pass

    app = create_app(cfg, bridge_factory=lambda _: Bridge())
    async with owner.router.lifespan_context(owner), app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8100"
        ) as client:
            yield client, app.state.harness, owner


async def test_config_revision_fixed_handlers_and_run_snapshot(gateway):
    client, service, _ = gateway
    original = (await client.get("/harness/config")).json()
    value = {**original, "model": "test-model", "system_prompt": "Test prompt"}
    saved = await client.put("/harness/config", json=value)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert (await client.put("/harness/config", json=value)).status_code == 409
    bad = {**saved.json(), "credential_env": "AWS_SECRET_ACCESS_KEY"}
    assert (await client.put("/harness/config", json=bad)).status_code == 422
    gate = asyncio.Event()

    class Model:
        async def run(self, config, text, definitions, call, emit):
            assert config.system_prompt == "Test prompt"
            await gate.wait()
            await emit("assistant", {"text": "done"})

    service.model = Model()
    run = await service.launch(HarnessRunRequest(text="inspect"))
    await service.save_config(service.config().model_copy(update={"system_prompt": "New prompt"}))
    gate.set()
    await service.task
    assert service.lookup(run.id).config.system_prompt == "Test prompt"
    assert service.lookup(run.id).state == "completed"


async def test_mcp_discovery_prompt_disabled_tools_and_local_boundary(gateway):
    client, service, _ = gateway

    async def rpc(method, params=None, **kwargs):
        return await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            **kwargs,
        )

    initialized = (await rpc("initialize", {"protocolVersion": "2025-11-25"})).json()
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    tools = (await rpc("tools/list")).json()["result"]["tools"]
    expected = [t for t in definitions(service.config()) if t.enabled]
    assert [t["inputSchema"] for t in tools] == [t.input_schema for t in expected]
    assert "capture_image" not in {t["name"] for t in tools}
    assert (await rpc("prompts/get", {"name": "workbench"})).json()["result"]["messages"][0][
        "content"
    ]["text"] == service.config().system_prompt
    result = (
        await rpc(
            "tools/call",
            {
                "name": "execute_sequence",
                "arguments": {"operation_id": str(uuid4()), "steps": [{"action": "open_claw"}]},
            },
        )
    ).json()
    assert result["result"]["isError"]
    assert (await rpc("tools/list", headers={"Origin": "https://evil.example"})).status_code == 403
    assert (await rpc("tools/list", headers={"Host": "evil.example"})).status_code == 403
    assert (await client.get("/mcp")).status_code == 405


async def test_camera_failure_and_grip_unknown(gateway):
    _, service, _ = gateway
    grip = await service.call("inspect_grip", {})
    assert grip["grip"] == "unknown"
    assert grip["claw_current"]["simulated"]
    config = service.config().model_copy(update={"supports_images": True})
    with pytest.raises(HTTPException, match="fresh camera"):
        await service.call("capture_image", {}, config)


async def test_worker_current_fault_cancels_sequence_and_attempts_shutdown(gateway, monkeypatch):
    _, service, owner = gateway
    executor = owner.state.harness
    manual = executor.manual
    # The output is fake; monitor readings and the entire owner remain simulated.
    disabled = []

    class Output:
        def disable(self):
            disabled.append(True)

        def close(self):
            pass

    manual.output = Output()
    executor.token = uuid4()
    executor.deadline = executor.clock() + 300
    executor.heartbeat_until = executor.clock() + 3
    manual.monitor.trip("Injected sensor fault")
    await asyncio.sleep(0.15)
    assert disabled
    assert not executor.active
    assert manual.recovery_required
    assert "CUT PHYSICAL SERVO POWER" in manual.monitor.fault


async def test_camera_jpeg_and_no_image_in_persistent_activity(gateway):
    import base64

    import numpy as np

    client, service, _ = gateway
    sample = (0, np.zeros((48, 64, 3), dtype=np.uint8), now(), "simulated")
    service.bridge().latest = lambda: sample
    config = service.config().model_copy(update={"supports_images": True})
    result = await service.call("capture_image", {}, config)
    assert base64.b64decode(result["image"]).startswith(b"\xff\xd8")
    assert result["simulated"]
    assert result["age_seconds"] >= 0
    assert result["frame_reference"] == sample[2].isoformat()
    assert result["camera_owner"] == "vision"
    service.config = lambda: config
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "capture_image", "arguments": {}},
        },
    )
    content = response.json()["result"]
    assert not content["isError"]
    assert content["content"][1]["type"] == "image"
    assert base64.b64decode(content["content"][1]["data"]).startswith(b"\xff\xd8")
    activity = service.store.get("activity")
    assert "image" not in activity[-1]["data"]["result"]
    assert activity[-1]["data"]["result"]["captured_at"] == sample[2].isoformat()


async def test_model_runs_real_handlers_with_fake_io_and_stops_on_failure(gateway, monkeypatch):
    import json

    from workbench_contracts import CommissionBoundsUpdate, CommissionLimitValues

    client, service, owner = gateway
    bounds = await service.control("commissioning_bounds")
    await service.control(
        "commissioning_save_bounds",
        value=CommissionBoundsUpdate(
            assembly_id=bounds.assembly_id,
            setup=bounds.setup,
            joints={
                name: CommissionLimitValues(minimum_us=1400, maximum_us=1600)
                for name in bounds.joints
            },
        ),
    )
    await service.enable(
        HarnessEnable(
            supervised=True,
            acknowledge_unverified_scale=True,
            claw_open_us=1450,
            claw_close_us=1550,
        )
    )
    await service.save_config(service.config().model_copy(update={"model": "test-model"}))
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "step1",
                                        "type": "function",
                                        "function": {
                                            "name": "execute_sequence",
                                            "arguments": json.dumps(
                                                {
                                                    "steps": [
                                                        {
                                                            "action": "move_joint",
                                                            "joint_id": "base",
                                                            "target_pulse_us": 1510,
                                                        }
                                                    ],
                                                }
                                            ),
                                        },
                                    },
                                ],
                            }
                        }
                    ]
                },
            )
        # A provider error after physical dispatch must end the session, not retry.
        return httpx.Response(500, text="private provider details")

    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    monkeypatch.setattr("workbench_assistant.harness.httpx.AsyncClient", lambda **_: fake_client)
    run = await service.launch(HarnessRunRequest(text="Move base"))
    await service.task
    assert len(calls) == 2
    assert owner.state.harness.manual.targets["base"] == pytest.approx(1510)
    assert owner.state.harness.operation.state == "completed"
    assert not owner.state.harness.active
    assert (await service.control("status")).recovery_required
    saved = service.lookup(run.id)
    assert saved.state == "failed"
    assert "private provider" not in saved.model_dump_json()


def test_sequence_timeout_and_session_lifetime_require_recovery(rig):
    executor, manual, bounds, clock = rig
    lease = enable(executor, bounds)
    executor.submit(sequence(lease.token, {"action": "close_claw"}))
    clock[0] = 61
    executor.heartbeat_until = 64
    executor.tick()
    assert executor.operation.state == "stopped"
    assert "timed out" in executor.message
    assert manual.recovery_required


def test_session_budget_is_not_extended_by_heartbeats(rig):
    executor, manual, bounds, clock = rig
    lease = enable(executor, bounds)
    for second in range(1, 300):
        clock[0] = second
        executor.heartbeat(lease)
    clock[0] = 300
    with pytest.raises(HTTPException):
        executor.heartbeat(lease)
    assert not executor.active and manual.recovery_required


async def test_camera_rejects_frame_stale_at_delivery(gateway):
    from datetime import timedelta

    import numpy as np

    _, service, _ = gateway
    stamp = now() - timedelta(seconds=service.cfg["observation_max_age_seconds"] + 1)
    service.bridge().latest = lambda: (0, np.zeros((48, 64, 3), dtype=np.uint8), stamp, "simulated")
    config = service.config().model_copy(update={"supports_images": True})
    with pytest.raises(HTTPException, match="stale before delivery"):
        await service.call("capture_image", {}, config)


async def test_state_metadata_shared_by_internal_and_mcp(gateway):
    import json

    client, service, _ = gateway
    internal = await service.call("get_workbench_state", {}, internal=True)
    assert internal["measured_position_available"] is False
    assert internal["snapshot_atomic"] is False
    assert len(internal["calibration"]["revision"]) == 64
    assert internal["claw_current"]["simulated"]
    assert "shunt_mv" in internal["claw_current"]
    assert "scale_verified" in internal["claw_current"]
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "get_workbench_state", "arguments": {}},
        },
    )
    result = response.json()["result"]
    assert not result["isError"]
    assert all(item["type"] == "text" for item in result["content"])
    external = json.loads(result["content"][0]["text"])
    assert external["calibration"] == internal["calibration"]
    assert external["ownership"] == internal["ownership"]


async def test_mcp_camera_loss_is_error_without_image(gateway):
    client, service, _ = gateway
    config = service.config().model_copy(update={"supports_images": True})
    service.config = lambda: config
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "capture_image", "arguments": {}},
        },
    )
    result = response.json()["result"]
    assert result["isError"]
    assert all(item["type"] == "text" for item in result["content"])
    assert "fresh camera" in result["content"][0]["text"]
