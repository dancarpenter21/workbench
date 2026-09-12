import asyncio
import time
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from workbench_common import Store, app_base, settings
from workbench_contracts import (
    TERMINAL,
    WAV_REQUEST_BODY,
    ArmStatus,
    Health,
    Intent,
    Observations,
    Operation,
    Recovery,
    TextRequest,
    Tool,
    Transcript,
    WorkbenchStatus,
    now,
)


def check_observations(obs: Observations, cfg, tool_id: str, location: str):
    age = (now() - obs.captured_at).total_seconds()
    if not obs.camera_ok or not 0 <= age <= cfg["observation_max_age_seconds"]:
        raise ValueError("Camera observations are unavailable or stale")
    matches = [t for t in obs.tools if t.tool_id == tool_id]
    if (
        len(matches) != 1
        or matches[0].location != location
        or matches[0].confidence < cfg["confidence_threshold"]
    ):
        raise ValueError(f"Cannot confidently confirm tool at {location}")
    if location == "source" and not obs.tray_clear:
        raise ValueError("Clear the delivery tray before requesting a tool")


def create_app(config=None, store=None, client=None):
    cfg = config or settings()
    state = {"store": store, "client": client, "task": None}
    gate = asyncio.Lock()

    def current():
        value = state["store"].get("current")
        return Operation.model_validate(value) if value else None

    async def save(operation):
        operation.updated_at = now()
        value = operation.model_dump(mode="json")
        await state["store"].aput_many({f"operation:{operation.id}": value, "current": value})
        return operation

    async def update(operation, status, message):
        operation.state, operation.message = status, message
        return await save(operation)

    def snapshot():
        return WorkbenchStatus(
            mode=cfg["mode"],
            operation=current(),
            recovery_required=state["store"].get("recovery", False),
            message=state["store"].get("stop_message", ""),
        )

    async def call(service, method, path, operation_id=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if operation_id:
            headers["x-operation-id"] = str(operation_id)
        response = await state["client"].request(
            method, cfg["services"][service] + path, headers=headers, **kwargs
        )
        response.raise_for_status()
        return response

    async def stop_arm():
        try:
            response = await call("arm-controller", "POST", "/stop", timeout=3)
            return ArmStatus.model_validate(response.json()).message
        except Exception:
            return "Arm stop could not be confirmed. Use the physical power cutoff."

    @asynccontextmanager
    async def lifespan(app):
        state["store"] = store or Store("coordinator")
        state["client"] = client or httpx.AsyncClient(timeout=5)
        operation = current()
        if operation and operation.state not in TERMINAL:
            await state["store"].aput("recovery", True)
            await update(
                operation, "failed", "Coordinator restarted; inspect the workbench and recover"
            )
            await stop_arm()
        yield
        if state["task"] and not state["task"].done():
            await state["store"].aput("recovery", True)
            state["task"].cancel()
            await asyncio.gather(state["task"], return_exceptions=True)
            await stop_arm()
            operation = current()
            if operation and operation.state not in TERMINAL:
                await update(operation, "stopped", "Application shutdown; recovery required")
        if client is None:
            await state["client"].aclose()
        if store is None:
            state["store"].close()

    app = app_base("coordinator", lifespan)

    @app.get("/health", response_model=Health)
    async def health():
        return Health(service="coordinator", mode=cfg["mode"], ready=True)

    @app.get("/services", response_model=list[Health])
    async def services():
        async def one(name):
            try:
                result = Health.model_validate(
                    (await call(name, "GET", "/health", timeout=1)).json()
                )
                if result.mode != cfg["mode"]:
                    result.ready, result.detail = False, "Service mode does not match coordinator"
                return result
            except Exception:
                return Health(
                    service=name, mode=cfg["mode"], ready=False, detail="Service unreachable"
                )

        return await asyncio.gather(*(one(n) for n in cfg["services"] if n != "coordinator"))

    @app.get("/tools", response_model=list[Tool])
    async def tools():
        return cfg["workbench"]["tools"]

    @app.get("/status", response_model=WorkbenchStatus)
    async def status():
        return snapshot()

    @app.get("/operations/{operation_id}", response_model=Operation)
    async def operation(operation_id: UUID):
        result = state["store"].get(f"operation:{operation_id}")
        if not result:
            raise HTTPException(404, "Operation not found")
        return Operation.model_validate(result)

    async def workflow(operation):
        dispatched = False
        try:
            intent = Intent.model_validate(
                (
                    await call(
                        "assistant",
                        "POST",
                        "/interpret",
                        operation.id,
                        json={"text": operation.text, "operation_id": str(operation.id)},
                        timeout=120,
                    )
                ).json()
            )
            if intent.action == "clarify":
                await update(operation, "clarification", intent.message)
                return
            if intent.action == "get_status":
                arm = ArmStatus.model_validate(
                    (await call("arm-controller", "GET", "/status", operation.id)).json()
                )
                await update(operation, "status", f"Arm: {arm.state}. {arm.message}")
                return
            if intent.tool_id not in {t["id"] for t in cfg["workbench"]["tools"]}:
                raise ValueError("Assistant returned an unknown tool")
            operation.tool_id = intent.tool_id
            await update(operation, "checking", "Checking the tool, delivery tray and arm")
            for name in ("vision", "arm-controller"):
                health = Health.model_validate(
                    (await call(name, "GET", "/health", operation.id)).json()
                )
                if not health.ready or health.mode != cfg["mode"]:
                    raise ValueError(f"{name} is not ready in {cfg['mode']} mode: {health.detail}")
            arm = ArmStatus.model_validate(
                (await call("arm-controller", "GET", "/status", operation.id)).json()
            )
            if arm.recovery_required or arm.state not in {"idle", "completed"}:
                await state["store"].aput("recovery", True)
                raise ValueError("Arm requires inspection and recovery")
            async with gate:
                obs = Observations.model_validate(
                    (await call("vision", "GET", "/observations", operation.id)).json()
                )
                check_observations(obs, cfg, operation.tool_id, "source")
                await update(operation, "moving", "Retrieving the tool")
                # Once dispatch begins, even a timeout can mean movement occurred.
                dispatched = True
                await call(
                    "arm-controller",
                    "POST",
                    "/execute",
                    operation.id,
                    json={"operation_id": str(operation.id), "tool_id": operation.tool_id},
                )
            deadline = time.monotonic() + cfg["motion_timeout_seconds"]
            while time.monotonic() < deadline:
                arm = ArmStatus.model_validate(
                    (await call("arm-controller", "GET", "/status", operation.id)).json()
                )
                if arm.operation_id != operation.id:
                    raise ValueError("Arm operation identity changed unexpectedly")
                if arm.state == "completed":
                    break
                if arm.state != "moving":
                    raise ValueError(arm.message or "Arm stopped unexpectedly")
                await asyncio.sleep(0.1)
            else:
                raise TimeoutError("Motion timed out; completion is unknown")
            await update(operation, "verifying", "Checking source and delivery tray")
            if cfg["mode"] == "mock":
                await call("vision", "POST", f"/mock/deliver/{operation.tool_id}", operation.id)
            deadline = time.monotonic() + 3
            while True:
                obs = Observations.model_validate(
                    (await call("vision", "GET", "/observations", operation.id)).json()
                )
                try:
                    check_observations(obs, cfg, operation.tool_id, "tray")
                    break
                except ValueError:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.2)
            await update(operation, "completed", "Tool delivered to the tray")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = (
                str(exc)
                if not isinstance(exc, httpx.HTTPError)
                else "A required service failed or timed out"
            )
            if dispatched:
                await state["store"].aput("recovery", True)
                message += ". " + await stop_arm()
            await update(operation, "failed" if dispatched else "rejected", message)

    @app.post("/requests", response_model=Operation, status_code=202)
    async def submit(request: TextRequest):
        if not request.text.strip():
            raise HTTPException(422, "Request must contain text")
        async with gate:
            previous = state["store"].get(f"operation:{request.operation_id}")
            if previous:
                if previous["text"] != request.text:
                    raise HTTPException(409, "Operation ID already belongs to different text")
                return Operation.model_validate(previous)
            if state["store"].get("recovery", False):
                raise HTTPException(
                    409, "Inspect the workbench and recover before requesting another tool"
                )
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "A request is already active")
            operation = await save(Operation(id=request.operation_id, text=request.text))
            state["task"] = asyncio.create_task(workflow(operation))
            return operation

    @app.post("/stop", response_model=WorkbenchStatus)
    async def stop():
        async with gate:
            await state["store"].aput("recovery", True)
            if state["task"] and not state["task"].done():
                state["task"].cancel()
                await asyncio.gather(state["task"], return_exceptions=True)
            message = await stop_arm()
            operation = current()
            if operation and operation.state not in TERMINAL:
                await update(operation, "stopped", message)
            await state["store"].aput("stop_message", message)
            return snapshot()

    @app.post("/recover", response_model=WorkbenchStatus)
    async def recover(request: Recovery):
        async with gate:
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "Stop the active request before recovery")
            try:
                await call("arm-controller", "POST", "/recover", json=request.model_dump())
            except httpx.HTTPError as exc:
                raise HTTPException(
                    503, "Arm recovery failed; inspect hardware and try again"
                ) from exc
            await state["store"].aput_many({"recovery": False, "stop_message": ""})
            return snapshot()

    @app.get("/events")
    async def events(request: Request):
        async def stream():
            while not await request.is_disconnected():
                yield f"data: {snapshot().model_dump_json()}\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/camera/preview")
    async def preview():
        try:
            result = await call("vision", "GET", "/preview")
            return Response(
                result.content,
                media_type=result.headers["content-type"],
                headers={"Cache-Control": "no-store"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Camera unavailable") from exc

    @app.post("/transcriptions", response_model=Transcript, openapi_extra=WAV_REQUEST_BODY)
    async def transcribe(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 4_000_000:
                raise HTTPException(413, "Audio exceeds 4 MB")
        try:
            result = await call(
                "voice",
                "POST",
                "/transcribe",
                content=bytes(data),
                headers={"Content-Type": "audio/wav"},
                timeout=120,
            )
            return Transcript.model_validate(result.json())
        except httpx.HTTPError as exc:
            raise HTTPException(
                503, "Transcription failed. Check voice service and WAV input."
            ) from exc

    if cfg["mode"] == "mock":

        @app.post("/mock/reset", response_model=WorkbenchStatus)
        async def reset():
            async with gate:
                if state["task"] and not state["task"].done():
                    raise HTTPException(
                        409, "Wait for the active request before resetting the scene"
                    )
                try:
                    await call("vision", "POST", "/mock/reset")
                except httpx.HTTPError as exc:
                    raise HTTPException(503, "Vision reset failed") from exc
                return snapshot()

    return app
