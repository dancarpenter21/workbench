# assistant

Owns text-to-intent interpretation. `POST /interpret` accepts `{text, operation_id}` and returns `retrieve_tool`, `get_status`, or `clarify`, with an optional tool ID and message.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_assistant.app:create_app --factory --host 127.0.0.1 --port 8103
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8103/health`; interactive API
documentation: `http://127.0.0.1:8103/docs`.

Hardware mode needs the `assistant` extra and a local instruction/chat GGUF model. The application owns a persistent llama.cpp instance and serializes inference. Mock mode uses an explicit command parser. The assistant never connects to the arm and cannot generate executable joint commands.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).
