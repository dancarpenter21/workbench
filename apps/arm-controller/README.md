# arm-controller

Owns the robot connection and execution journal. `POST /execute` accepts `{operation_id, tool_id}` for a configured path. It permits one operation at a time and never replays a submitted ID. `GET /status`, `POST /stop`, and `POST /recover` expose operator control.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_arm_controller.app:create_app --factory --host 127.0.0.1 --port 8104
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8104/health`; interactive API
documentation: `http://127.0.0.1:8104/docs`.

Hardware mode needs the `arm` extra, a USB serial device and reviewed calibration. It checks startup pose, bounded joint targets/speeds, and feedback at each waypoint. It starts with recovery required. Stop requests a software hold; loss of feedback or serial access requires the physical cutoff. No arbitrary motion endpoint is exposed. See the hardware guide before use.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).
