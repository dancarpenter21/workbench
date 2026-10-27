from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from workbench_common import app_base, settings
from workbench_common.diagnostics import model_check
from workbench_common.inference import EngineBusy, EngineUnavailable
from workbench_contracts import (
    WAV_REQUEST_BODY,
    Diagnostic,
    Health,
    MicrophoneDevice,
    MicrophoneResult,
    MicrophoneTest,
    Transcript,
)

from .engine import MAX_BYTES, VoiceEngine, validate_audio
from .microphone import Microphone, devices


async def read_audio(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Audio exceeds 4 MB")
    try:
        validate_audio(bytes(data))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return bytes(data)


def create_app(config=None):
    cfg = config or settings()
    engine = VoiceEngine(cfg)
    microphone = Microphone(mock=cfg["mode"] == "mock")

    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        try:
            yield
        finally:
            await microphone.close()
            await engine.close()

    app = app_base("voice", lifespan)

    @app.get("/diagnostics", response_model=list[Diagnostic])
    async def diagnostics():
        checks = [
            model_check(
                "voice",
                cfg,
                "faster_whisper",
                cfg["speech"]["model_path"],
                engine.model is not None,
            )
        ]
        try:
            found = await devices(cfg["mode"] == "mock")
            checks.append(
                Diagnostic(
                    component="microphone",
                    status="ok" if found else "unavailable",
                    summary="Pi capture devices available"
                    if found
                    else "No Pi capture devices found",
                    measurements={
                        "device_count": len(found),
                        "recording": microphone.lock.locked(),
                        "simulated": cfg["mode"] == "mock",
                    },
                )
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            checks.append(
                Diagnostic(
                    component="microphone",
                    status="error",
                    summary=str(exc) or "Device enumeration timed out",
                )
            )
        return checks

    @app.get("/diagnostics/microphone/devices", response_model=list[MicrophoneDevice])
    async def microphone_devices():
        try:
            return await devices(cfg["mode"] == "mock")
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(503, str(exc) or "Device enumeration timed out") from exc

    @app.post("/diagnostics/microphone/test", response_model=MicrophoneResult)
    async def microphone_test(value: MicrophoneTest, request: Request):
        try:
            return await microphone.test(request, value.device_id)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            raise HTTPException(503, str(exc) or "Microphone capture timed out") from exc

    @app.get("/health", response_model=Health)
    async def health():
        return Health(
            service="voice",
            mode=cfg["mode"],
            ready=not engine.error,
            detail=engine.error,
        )

    @app.post("/transcribe", response_model=Transcript, openapi_extra=WAV_REQUEST_BODY)
    async def transcribe(request: Request):
        data = await read_audio(request)
        try:
            return await engine.transcribe(data)
        except EngineBusy as exc:
            raise HTTPException(409, "Transcriber is busy") from exc
        except EngineUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    return app
