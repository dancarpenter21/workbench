# coordinator

Owns the retrieval workflow. `POST /requests` accepts text and an optional UUID operation ID; clients should supply the UUID for idempotent retries. Use `GET /operations/{id}`, `GET /status`, or `GET /events` for progress.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_coordinator.app:create_app --factory --host 127.0.0.1 --port 8100
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8100/health`; interactive API
documentation: `http://127.0.0.1:8100/docs`.

The coordinator calls other applications through HTTP, validates observations before dispatch, verifies delivery afterward, and persists operation history and recovery state. `POST /stop` blocks new retrievals. `POST /recover` requires `{acknowledged: true}` after inspection. It also proxies preview/audio and exposes `/services` and `/tools` for the dashboard. Dependencies must be running; the root launcher starts them.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).
