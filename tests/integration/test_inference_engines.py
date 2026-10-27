import asyncio
import io
import json
import threading
import wave

import pytest
from workbench_assistant.engine import AssistantEngine
from workbench_common import ROOT
from workbench_common.inference import EngineBusy, run_serialized
from workbench_voice.engine import VoiceEngine


def config():
    cfg = json.loads((ROOT / "config/development/default.json").read_text())
    cfg["workbench"] = json.loads((ROOT / "config/workbench/default.json").read_text())
    cfg["mode"] = "mock"
    return cfg


async def test_transport_independent_engines_validate_mock_inputs():
    assistant = AssistantEngine(config())
    voice = VoiceEngine(config())
    await assistant.start()
    await voice.start()
    assert (await assistant.interpret("fetch the screwdriver")).tool_id == "screwdriver"
    assert (await assistant.interpret("don't fetch the screwdriver")).action == "clarify"
    with pytest.raises(ValueError, match="Invalid WAV"):
        await voice.transcribe(b"invalid")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    assert (await voice.transcribe(audio.getvalue())).text == "Grab me the screwdriver"
    await voice.close()
    await assistant.close()


async def test_repeated_cancellation_keeps_model_locked_until_native_worker_exits():
    lock = asyncio.Lock()
    started = threading.Event()
    finish = threading.Event()

    def worker():
        started.set()
        assert finish.wait(timeout=5)
        return "done"

    task = asyncio.create_task(run_serialized(lock, worker))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            with pytest.raises(EngineBusy):
                await run_serialized(lock, lambda: "must not run")
        assert lock.locked()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not lock.locked()
    assert await run_serialized(lock, lambda: "next") == "next"
