import asyncio
import io
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, Request
from workbench_common import ROOT, app_base, settings
from workbench_contracts import WAV_REQUEST_BODY, Health, Transcript

MAX_BYTES = 4_000_000


def validate_audio(data: bytes):
    try:
        with wave.open(io.BytesIO(data)) as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError("Use mono 16-bit PCM WAV")
            if not 8000 <= audio.getframerate() <= 48000:
                raise ValueError("Unsupported sample rate")
            if not 0.1 <= audio.getnframes() / audio.getframerate() <= 30:
                raise ValueError("Record between 0.1 and 30 seconds")
            if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
                raise ValueError("Truncated audio")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid WAV file") from exc


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
    state = {"model": None, "error": ""}
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if cfg["mode"] == "hardware":
            try:
                from faster_whisper import WhisperModel

                path = ROOT / Path(cfg["speech"]["model_path"])
                if not path.is_dir():
                    raise ValueError(f"Local Whisper model missing: {path}")
                state["model"] = await asyncio.to_thread(
                    WhisperModel,
                    str(path),
                    device=cfg["speech"]["device"],
                    compute_type=cfg["speech"]["compute_type"],
                    local_files_only=True,
                )
            except Exception as exc:
                state["error"] = str(exc)
        yield

    app = app_base("voice", lifespan)

    @app.get("/health", response_model=Health)
    async def health():
        return Health(
            service="voice", mode=cfg["mode"], ready=not state["error"], detail=state["error"]
        )

    @app.post("/transcribe", response_model=Transcript, openapi_extra=WAV_REQUEST_BODY)
    async def transcribe(request: Request):
        data = await read_audio(request)
        if cfg["mode"] == "mock":
            return Transcript(text="Grab me the screwdriver")
        if state["model"] is None:
            raise HTTPException(503, state["error"] or "Speech model is not loaded")
        if lock.locked():
            raise HTTPException(409, "Transcriber is busy")

        def run():
            with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
                audio.write(data)
                audio.flush()
                segments, _ = state["model"].transcribe(audio.name, beam_size=1, vad_filter=True)
                return " ".join(s.text.strip() for s in segments).strip()

        async with lock:
            try:
                return Transcript(text=await asyncio.to_thread(run))
            except Exception as exc:
                raise HTTPException(503, "Local transcription failed") from exc

    return app
