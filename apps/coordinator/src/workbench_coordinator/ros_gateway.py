"""HTTP/WebSocket browser gateway backed exclusively by ROS interfaces."""

import asyncio
import inspect
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from workbench_common import app_base, settings
from workbench_contracts import (
    WAV_REQUEST_BODY,
    DiagnosticsReport,
    Health,
    MicrophoneDevice,
    MicrophoneResult,
    MicrophoneTest,
    Operation,
    Recovery,
    TextRequest,
    Transcript,
    VideoAnswer,
    VideoOffer,
    WorkbenchStatus,
)
from workbench_contracts.ros_control import CONTROL_BINDINGS
from workbench_contracts.ros_transport import RemoteControlError
from workbench_vision.stream import VideoSessions
from workbench_voice.app import read_audio


def create_app(config=None, bridge_factory=None):
    cfg = config or settings()
    state = {"bridge": None, "video": None}

    @asynccontextmanager
    async def lifespan(app):
        factory = bridge_factory
        if factory is None:
            from .ros_client import RosBrowserBridge

            factory = RosBrowserBridge
        state["bridge"] = await asyncio.to_thread(factory, cfg)
        state["video"] = VideoSessions(cfg, lambda: state["bridge"], state["bridge"].scene)
        try:
            yield
        finally:
            await state["video"].close()
            await asyncio.to_thread(state["bridge"].close)

    app = app_base("coordinator", lifespan)

    async def invoke(function, *args):
        try:
            return await function(*args)
        except RemoteControlError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(503, str(exc)) from exc

    def snapshot():
        try:
            return state["bridge"].status()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/health", response_model=Health)
    async def health():
        return next(s for s in state["bridge"].services() if s.service == "coordinator")

    @app.get("/services", response_model=list[Health])
    async def services():
        return state["bridge"].services()

    @app.get("/status", response_model=WorkbenchStatus)
    async def status():
        return snapshot()

    @app.post("/requests", response_model=Operation, status_code=202)
    async def submit(value: TextRequest):
        if not value.text.strip():
            raise HTTPException(422, "Request must contain text")
        return await invoke(state["bridge"].submit, value)

    @app.get("/operations/{operation_id}", response_model=Operation)
    async def operation(operation_id: UUID):
        result = await invoke(state["bridge"].lookup, str(operation_id))
        if result is None:
            raise HTTPException(404, "Operation not found")
        return result

    @app.post("/stop", response_model=WorkbenchStatus)
    async def stop():
        return await invoke(state["bridge"].stop)

    @app.post("/recover", response_model=WorkbenchStatus)
    async def recover(value: Recovery):
        return await invoke(state["bridge"].recover)

    @app.post("/transcriptions", response_model=Transcript, openapi_extra=WAV_REQUEST_BODY)
    async def transcribe(request: Request):
        data = await read_audio(request)
        return Transcript(text=await invoke(state["bridge"].transcribe, data))

    @app.get("/diagnostics/microphone/devices", response_model=list[MicrophoneDevice])
    async def microphones():
        return await invoke(state["bridge"].microphones)

    @app.post("/diagnostics/microphone/test", response_model=MicrophoneResult)
    async def test_microphone(value: MicrophoneTest, request: Request):
        task = asyncio.create_task(invoke(state["bridge"].test_microphone, value))
        try:
            while not task.done():
                if await request.is_disconnected():
                    raise HTTPException(499, "Microphone client disconnected")
                await asyncio.wait({task}, timeout=0.1)
            return await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @app.websocket("/ws/status")
    async def stream(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await asyncio.wait_for(websocket.send_text(snapshot().model_dump_json()), timeout=3)
                if await asyncio.wait_for(websocket.receive_text(), timeout=3) != "ack":
                    break
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, TimeoutError, HTTPException, RuntimeError):
            pass
        finally:
            try:
                await websocket.close()
            except RuntimeError:
                pass

    @app.get("/diagnostics", response_model=DiagnosticsReport)
    async def diagnostics():
        status = snapshot()
        return DiagnosticsReport(
            mode=cfg["mode"],
            retrieval_enabled=status.retrieval_enabled,
            checks=state["bridge"].diagnostics(),
        )

    @app.post("/camera/webrtc/offer", response_model=VideoAnswer)
    async def video_offer(value: VideoOffer):
        return await state["video"].offer(value)

    @app.delete("/camera/webrtc/sessions/{session_id}", status_code=204)
    async def video_close(session_id: str):
        await state["video"].remove(session_id)

    if cfg["mode"] == "mock":

        @app.post("/mock/reset", response_model=WorkbenchStatus)
        async def reset():
            return await invoke(state["bridge"].reset)

    occupied = {route.path for route in app.routes}

    def control_route(binding):
        async def command(**arguments):
            return await invoke(state["bridge"].control, binding, arguments)

        command.__signature__ = inspect.Signature(
            [
                inspect.Parameter(
                    name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
                )
                for name, annotation in binding["parameters"].items()
            ]
        )
        return command

    for binding in CONTROL_BINDINGS:
        if binding["path"] not in occupied:
            app.add_api_route(
                binding["path"],
                control_route(binding),
                methods=[binding["method"]],
                response_model=binding["result"],
                name=binding["endpoint"],
            )

    return app
