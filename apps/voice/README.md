# voice

Owns local speech transcription. `POST /transcribe` accepts a raw `audio/wav` body: mono 16-bit PCM, 8–48 kHz, 0.1–30 seconds, at most 4 MB. Returns `{text}`.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_voice.app:create_app --factory --host 127.0.0.1 --port 8102
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8102/health`; interactive API
documentation: `http://127.0.0.1:8102/docs`.

The dashboard owns browser microphone access and uploads WAV; the voice application owns recognition. Hardware mode needs the `voice` extra and a local faster-whisper model directory. It loads the model once with downloads disabled. Mock mode validates the WAV and returns a fixed screwdriver request. Real audio accuracy has not been evaluated.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).
