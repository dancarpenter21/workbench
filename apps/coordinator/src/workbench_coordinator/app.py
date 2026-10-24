import asyncio
import time
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from workbench_common import Store, app_base, settings
from workbench_common.diagnostics import host_check
from workbench_contracts import (
    TERMINAL,
    WAV_REQUEST_BODY,
    ArmStatus,
    CommissionAcquire,
    CommissionAttemptSelect,
    CommissionCommand,
    CommissionCreate,
    CommissionDraft,
    CommissionEdit,
    CommissionExport,
    CommissionFeedback,
    CommissionFinish,
    CommissionMark,
    CommissionPowerOffReset,
    CommissionPrepare,
    CommissionReady,
    CommissionSelect,
    CommissionSession,
    CommissionStatus,
    CommissionStep,
    CommissionStop,
    CommissionTarget,
    CommissionWindow,
    CurrentScaleApply,
    CurrentScaleState,
    CurrentScaleVerification,
    Diagnostic,
    DiagnosticsReport,
    Health,
    Intent,
    ManualAcquire,
    ManualInput,
    ManualPrepare,
    ManualRelease,
    ManualSession,
    ManualStatus,
    MicrophoneDevice,
    MicrophoneResult,
    MicrophoneTest,
    NeutralEvidence,
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
    state = {"store": store, "client": client, "task": None, "manual": None, "commissioning": None}
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

    def retrieval_enabled():
        return cfg.get("profile") != "diagnostics" and not (
            cfg["mode"] == "hardware" and cfg["arm"].get("backend") == "pca9685"
        )

    def snapshot():
        return WorkbenchStatus(
            mode=cfg["mode"],
            profile=cfg.get("profile", "workbench"),
            retrieval_enabled=retrieval_enabled()
            and not bool(state["manual"] and state["manual"].active)
            and not bool(state["commissioning"] and state["commissioning"].reserved),
            operation=current(),
            recovery_required=state["store"].get("recovery", False)
            or bool(state["manual"] and state["manual"].recovery_required),
            message=state["store"].get("stop_message", "")
            or (
                state["manual"].message
                if state["manual"] and state["manual"].recovery_required
                else ""
            ),
            claw_current=(state["manual"].claw_current if state["manual"] else None),
            neutral_evidence=(state["manual"].neutral_evidence if state["manual"] else None),
            manual=state["manual"],
            commissioning=state["commissioning"],
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

    @app.get("/diagnostics", response_model=DiagnosticsReport)
    async def diagnostics():
        async def inspect(name):
            started = time.monotonic()
            try:
                response = await call(name, "GET", "/diagnostics", timeout=3.5)
                checks = [Diagnostic.model_validate(value) for value in response.json()]
                health = Health.model_validate(
                    (await call(name, "GET", "/health", timeout=1)).json()
                )
                if health.service != name or health.mode != cfg["mode"]:
                    raise ValueError("Service identity or mode mismatch")
                checks.append(
                    Diagnostic(
                        component=f"service:{name}",
                        status="ok",
                        summary="Service responding",
                        measurements={
                            "response_ms": round((time.monotonic() - started) * 1000, 1),
                            "mode": health.mode,
                            "capability_ready": health.ready,
                            "detail": health.detail,
                        },
                    )
                )
                return checks
            except Exception:
                return [
                    Diagnostic(
                        component=f"service:{name}",
                        status="unavailable",
                        summary="Diagnostic endpoint unavailable, invalid, or in a different mode",
                    )
                ]

        groups = await asyncio.gather(
            *(inspect(name) for name in cfg["services"] if name != "coordinator")
        )
        host = await host_check(mock=cfg["mode"] == "mock")
        await refresh_manual()
        return DiagnosticsReport(
            neutral_evidence=(state["manual"].neutral_evidence if state["manual"] else None),
            mode=cfg["mode"],
            profile=cfg.get("profile", "workbench"),
            retrieval_enabled=retrieval_enabled(),
            checks=[
                host,
                Diagnostic(
                    component="service:coordinator", status="ok", summary="Coordinator responding"
                ),
                *(check for group in groups for check in group),
            ],
        )

    @app.get("/diagnostics/microphone/devices", response_model=list[MicrophoneDevice])
    async def microphone_devices():
        try:
            response = await call("voice", "GET", "/diagnostics/microphone/devices", timeout=4)
            return [MicrophoneDevice.model_validate(item) for item in response.json()]
        except Exception as exc:
            raise HTTPException(503, "Pi microphone enumeration unavailable") from exc

    @app.post("/diagnostics/microphone/test", response_model=MicrophoneResult)
    async def microphone_test(value: MicrophoneTest, request: Request):
        task = asyncio.create_task(
            call(
                "voice", "POST", "/diagnostics/microphone/test", json=value.model_dump(), timeout=10
            )
        )
        try:
            while not task.done():
                if await request.is_disconnected():
                    raise HTTPException(499, "Microphone client disconnected")
                await asyncio.wait({task}, timeout=0.1)
            return MicrophoneResult.model_validate((await task).json())
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                exc.response.status_code,
                exc.response.json().get("message", "Microphone test failed"),
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(503, "Pi microphone test unavailable") from exc
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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

    async def manual_call(path, value=None, prefix="manual"):
        try:
            result = await call(
                "arm-controller",
                "GET" if value is None else "POST",
                "/" + prefix + "/" + path,
                **({} if value is None else {"json": value.model_dump(mode="json")}),
                timeout=1,
            )
            return result.json()
        except httpx.HTTPStatusError as exc:
            try:
                message = exc.response.json().get("message", "Manual command refused")
            except ValueError:
                message = "Manual command refused"
            raise HTTPException(exc.response.status_code, message) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(503, "Arm controller unavailable; manual control ended") from exc

    async def refresh_manual():
        try:
            state["commissioning"] = CommissionStatus.model_validate(
                await manual_call("status", prefix="commissioning")
            )
        except (HTTPException, ValueError):
            state["commissioning"] = None
        try:
            manual = ManualStatus.model_validate(await manual_call("status"))
            if state["task"] and not state["task"].done():
                manual.available = False
                manual.message = "A retrieval request is active."
            state["manual"] = manual
        except (HTTPException, ValueError):
            state["manual"] = ManualStatus(
                simulated=cfg["mode"] == "mock", message="Arm controller unavailable"
            )
        return state["manual"]

    @app.get("/commissioning/current-scale", response_model=CurrentScaleState)
    async def current_scale_status():
        return await manual_call("current-scale", prefix="commissioning")

    @app.post("/commissioning/current-scale", response_model=CurrentScaleVerification)
    async def current_scale_save(value: CurrentScaleVerification):
        async with gate:
            return await manual_call("current-scale", value, prefix="commissioning")

    @app.post("/commissioning/current-scale/apply", response_model=CurrentScaleState)
    async def current_scale_apply(value: CurrentScaleApply):
        async with gate:
            return await manual_call("current-scale/apply", value, prefix="commissioning")

    @app.post("/commissioning/feedback", response_model=CommissionStatus)
    async def commissioning_feedback(value: CommissionFeedback):
        async with gate:
            return await manual_call("feedback", value, prefix="commissioning")

    @app.post("/commissioning/drafts/{draft_id}/select-attempt", response_model=CommissionDraft)
    async def commissioning_select_attempt(draft_id: str, value: CommissionAttemptSelect):
        async with gate:
            return await manual_call(
                f"drafts/{draft_id}/select-attempt", value, prefix="commissioning"
            )

    @app.get("/commissioning/evidence", response_model=NeutralEvidence)
    async def commissioning_evidence():
        return await manual_call("evidence", prefix="commissioning")

    @app.get("/commissioning/status", response_model=CommissionStatus)
    async def commissioning_status():
        return await manual_call("status", prefix="commissioning")

    @app.get("/commissioning/drafts", response_model=list[CommissionDraft])
    async def commissioning_drafts():
        return await manual_call("drafts", prefix="commissioning")

    @app.get("/commissioning/drafts/{draft_id}", response_model=CommissionDraft)
    async def commissioning_drafts_draft_id(draft_id: str):
        return await manual_call(f"drafts/{draft_id}", prefix="commissioning")

    @app.get("/commissioning/drafts/{draft_id}/export", response_model=CommissionExport)
    async def commissioning_drafts_draft_id_export(draft_id: str):
        return await manual_call(f"drafts/{draft_id}/export", prefix="commissioning")

    @app.post("/commissioning/drafts", response_model=CommissionDraft)
    async def commissioning_post_drafts(value: CommissionCreate):
        async with gate:
            result = await manual_call("drafts", value, prefix="commissioning")
            return result

    @app.post("/commissioning/drafts/{draft_id}", response_model=CommissionDraft)
    async def commissioning_post_drafts_draft_id(value: CommissionEdit, draft_id: str):
        async with gate:
            result = await manual_call(f"drafts/{draft_id}", value, prefix="commissioning")
            return result

    @app.post("/commissioning/initialize", response_model=CommissionSession)
    async def commissioning_initialize(value: CommissionAcquire):
        async with gate:
            return await manual_call("initialize", value, prefix="commissioning")

    @app.post("/commissioning/reset-current", response_model=CommissionStatus)
    async def commissioning_reset_current(value: CommissionPowerOffReset):
        async with gate:
            return await manual_call("reset-current", value, prefix="commissioning")

    @app.post("/commissioning/acquire", response_model=CommissionSession)
    async def commissioning_post_acquire(value: CommissionAcquire):
        async with gate:
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "Retrieval is active")
            result = await manual_call("acquire", value, prefix="commissioning")
            return result

    @app.post("/commissioning/prepare", response_model=CommissionStatus)
    async def commissioning_post_prepare(value: CommissionPrepare):
        async with gate:
            result = await manual_call("prepare", value, prefix="commissioning")
            return result

    @app.post("/commissioning/ready", response_model=CommissionStatus)
    async def commissioning_post_ready(value: CommissionReady):
        async with gate:
            result = await manual_call("ready", value, prefix="commissioning")
            return result

    @app.post("/commissioning/heartbeat", response_model=CommissionStatus)
    async def commissioning_post_heartbeat(value: CommissionCommand):
        async with gate:
            result = await manual_call("heartbeat", value, prefix="commissioning")
            return result

    @app.post("/commissioning/step", response_model=CommissionStatus)
    async def commissioning_post_step(value: CommissionStep):
        async with gate:
            result = await manual_call("step", value, prefix="commissioning")
            return result

    @app.post("/commissioning/target", response_model=CommissionStatus)
    async def commissioning_target(value: CommissionTarget):
        async with gate:
            return await manual_call("target", value, prefix="commissioning")

    @app.post("/commissioning/select", response_model=CommissionStatus)
    async def commissioning_select(value: CommissionSelect):
        async with gate:
            return await manual_call("select", value, prefix="commissioning")

    @app.post("/commissioning/window", response_model=CommissionStatus)
    async def commissioning_window(value: CommissionWindow):
        async with gate:
            return await manual_call("window", value, prefix="commissioning")

    @app.post("/commissioning/mark", response_model=CommissionStatus)
    async def commissioning_post_mark(value: CommissionMark):
        async with gate:
            result = await manual_call("mark", value, prefix="commissioning")
            return result

    @app.post("/commissioning/stop", response_model=CommissionStatus)
    async def commissioning_post_stop(value: CommissionStop):
        async with gate:
            result = await manual_call("stop", value, prefix="commissioning")
            return result

    @app.post("/commissioning/finish", response_model=CommissionStatus)
    async def commissioning_post_finish(value: CommissionFinish):
        async with gate:
            result = await manual_call("finish", value, prefix="commissioning")
            await state["store"].aput_many({"recovery": False, "stop_message": ""})
            return result

    @app.get("/manual/status", response_model=ManualStatus)
    async def manual_status():
        return await refresh_manual()

    @app.post("/manual/prepare", response_model=ManualStatus)
    async def manual_prepare(value: ManualPrepare):
        async with gate:
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "A retrieval request is active")
            result = ManualStatus.model_validate(await manual_call("prepare", value))
            await state["store"].aput_many({"recovery": False, "stop_message": ""})
            state["manual"] = result
            return result

    @app.post("/manual/session", response_model=ManualSession)
    async def manual_acquire(value: ManualAcquire):
        async with gate:
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "A retrieval request is active")
            if state["store"].get("recovery", False):
                raise HTTPException(
                    409, "Inspect the arm and recover before enabling manual control"
                )
            result = ManualSession.model_validate(await manual_call("session", value))
            state["manual"] = result.status
            return result

    @app.post("/manual/input", response_model=ManualStatus)
    async def manual_input(value: ManualInput):
        result = ManualStatus.model_validate(await manual_call("input", value))
        state["manual"] = result
        return result

    @app.post("/manual/release", response_model=ManualStatus)
    async def manual_release(value: ManualRelease):
        result = ManualStatus.model_validate(await manual_call("release", value))
        state["manual"] = result
        return result

    @app.get("/status", response_model=WorkbenchStatus)
    async def status():
        await refresh_manual()
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
        if not retrieval_enabled():
            raise HTTPException(409, "Automatic retrieval is disabled for this profile or backend")
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
            trial = CommissionStatus.model_validate(
                await manual_call("status", prefix="commissioning")
            )
            if trial.reserved:
                raise HTTPException(409, "Finish commissioning before retrieval")
            manual = ManualStatus.model_validate(await manual_call("status"))
            state["manual"] = manual
            if manual.active or manual.recovery_required:
                raise HTTPException(
                    409, "End manual control and recover if required before retrieval"
                )
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
            await refresh_manual()
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
            except httpx.HTTPStatusError as exc:
                try:
                    reason = exc.response.json().get("message")
                except ValueError:
                    reason = None
                raise HTTPException(
                    exc.response.status_code,
                    reason or "Arm recovery refused; check arm controller status",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    503, "Arm controller unreachable; recovery could not be confirmed"
                ) from exc
            await state["store"].aput_many({"recovery": False, "stop_message": ""})
            await refresh_manual()
            return snapshot()

    @app.get("/events")
    async def events(request: Request):
        async def stream():
            while not await request.is_disconnected():
                await refresh_manual()
                yield f"data: {snapshot().model_dump_json()}\n\n"
                await asyncio.sleep(0.1)

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
