# vision

Owns webcam capture and reference-image classification. `GET /observations` returns timestamped tool locations, confidence and tray clearance; `GET /preview` returns a JPEG in hardware mode or an SVG scene in mock mode.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_vision.app:create_app --factory --host 127.0.0.1 --port 8101
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8101/health`; interactive API
documentation: `http://127.0.0.1:8101/docs`.

Hardware mode needs the `vision` optional extra, configured camera regions and per-region reference images. See the hardware guide. Mock-only routes: `PUT /mock/scene`, `POST /mock/deliver/{tool_id}`, and `POST /mock/reset`. The scene endpoint supports stale timestamps and missing/uncertain tools for integration tests.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).
