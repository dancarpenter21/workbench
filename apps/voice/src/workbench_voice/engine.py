"""Transport-independent local speech recognition and WAV validation."""

import asyncio
import io
import tempfile
import wave
from pathlib import Path

from workbench_common import ROOT
from workbench_common.inference import EngineUnavailable, run_serialized
from workbench_contracts import Transcript

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


class VoiceEngine:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.error = ""
        self.lock = asyncio.Lock()

    async def start(self):
        if self.config["mode"] == "mock":
            return
        try:
            from faster_whisper import WhisperModel

            path = ROOT / Path(self.config["speech"]["model_path"])
            if not path.is_dir():
                raise ValueError(f"Local Whisper model missing: {path}")
            self.model = await asyncio.to_thread(
                WhisperModel,
                str(path),
                device=self.config["speech"]["device"],
                compute_type=self.config["speech"]["compute_type"],
                local_files_only=True,
            )
        except Exception as exc:
            self.error = str(exc)

    async def close(self):
        async with self.lock:
            self.model = None

    async def transcribe(self, data):
        if len(data) > MAX_BYTES:
            raise ValueError("Audio exceeds 4 MB")
        validate_audio(data)
        if self.config["mode"] == "mock":
            return Transcript(text="Grab me the screwdriver")
        if self.model is None:
            raise EngineUnavailable(self.error or "Speech model is not loaded")

        def run():
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
                    audio.write(data)
                    audio.flush()
                    segments, _ = self.model.transcribe(audio.name, beam_size=1, vad_filter=True)
                    return Transcript(text=" ".join(s.text.strip() for s in segments).strip())
            except Exception as exc:
                raise EngineUnavailable("Local transcription failed") from exc

        return await run_serialized(self.lock, run)
