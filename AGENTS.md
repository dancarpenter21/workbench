# Repository Guidelines

## Project Structure & Module Organization

This monorepo implements a local robotic tool-retrieval workbench. Five Python services live in `apps/{coordinator,vision,voice,assistant,arm-controller}/src/workbench_*`; the TypeScript/Vite dashboard lives in `apps/dashboard/src/`. Shared API models and generated types are in `packages/contracts/`, with configuration, logging, and persistence utilities in `packages/python-common/`.

Use `config/` for development settings and bench calibration, `scripts/` for launch and maintenance tools, and `docs/` for architecture, hardware, and commissioning guidance. Tests live in `tests/integration/` and `tests/e2e/`; physical acceptance procedures are documented in `tests/acceptance/`.

## Build, Test, and Development Commands

Requires Python 3.11+, uv, and Node.js 22.12+; CI uses Node.js 24.

On this computer, run Python through `uv` (for example, `uv run python`). Use `uv` for all Python package management and virtual environment creation. Prefer a project-local environment named `.venv`; create it with `uv venv .venv` or let `uv sync` manage it. Prefer `pyproject.toml` over `requirements.txt` for Python project metadata and dependency declarations. Use `uv add`, `uv remove`, and `uv sync` to manage project dependencies.

- `uv sync --locked` and `npm ci`: install locked dependencies.
- `npm run dev`: launch all services and the dashboard against Pi hardware at `http://127.0.0.1:5173`.
- `npm run check`: type-check the dashboard.
- `npm run build`: type-check and build the dashboard.
- `uv run pytest -q`: run Python tests; add `--extra vision` after `uv run` for optional classifier coverage.
- `uv run ruff check .` and `uv run ruff format --check .`: check Python lint and formatting.
- `npm run contracts`: regenerate OpenAPI and TypeScript contracts after API model changes; commit resulting changes.
- `npx playwright install chromium`, then `npm run test:e2e`: run browser tests against the mock stack on port 5174.

## ROS Runtime and Contract Changes

Normal `npm run dev` launches use the ROS backend (`--transport ros`), with an
HTTP/WebSocket gateway for the dashboard. The Python HTTP services are an explicit
alternative (`--transport http`); do not assume they are the running backend.

ROS uses built project packages and generated interfaces under `ros/install`.
After changing Python service code, shared contracts, or ROS control mappings:

1. Run `npm run contracts` after contract/API changes to regenerate OpenAPI,
   TypeScript, and ROS interfaces; commit the generated artifacts.
2. Run `uv run python scripts/ros-native.py build --apps-only` to rebuild the
   project overlay against the existing native underlay. This build starts no
   hardware nodes. A service restart alone does not rebuild the overlay.
3. Restart the app services and reload the dashboard to use the rebuilt packages.
   For hardware sessions, keep servo power disconnected and the arm supported
   during restart; do not automatically resume motion or bypass preparation.

When a control remains disabled, inspect the live gateway response and compare
the source interfaces in `ros/src/workbench_interfaces` with installed interfaces
in `ros/install/workbench_interfaces/share/workbench_interfaces`. Missing new
fields (for example, `ManualStatus.can_prepare`) can indicate a stale ROS overlay,
even after restarting. Do not work around missing permission fields by enabling
hardware controls in the browser. See `ros/README.md` for setup and smoke tests.

## Coding Style & Naming Conventions

Use four-space indentation, snake_case functions/modules, and PascalCase classes in Python. Ruff targets Python 3.11, enforces import sorting, and configures a 100-character line length. Run `uv run ruff format .` to format Python. Match existing TypeScript conventions: two-space indentation, double quotes, semicolons, and camelCase identifiers.

## Testing Guidelines

Use pytest and pytest-asyncio for service tests, naming files `test_*.py` and tests `test_*`. Browser tests use Playwright and `*.spec.ts`. Cover changed behavior and relevant failure/recovery paths. No numeric coverage threshold is configured. Browser tests reset the mock scene; avoid concurrent manual trials.

## Commit & Pull Request Guidelines

History favors concise, action-oriented subjects such as “Add Raspberry Pi hardware foundations”; follow that style. Describe behavior changes, link relevant issues, and report validation in pull requests. Include screenshots for dashboard changes. Ensure CI passes and regenerated contracts are current.

## Hardware & Configuration

Normal app launches use Pi hardware; use explicit `--mode mock` for simulation and tests. Follow `docs/hardware.md` and commissioning procedures before hardware work. Keep private recordings and runtime state out of Git. Simulated results do not establish physical acceptance; PCA9685 automated retrieval remains disabled.

## Mandatory claw ammeter monitoring

Every physical test that powers, commands, or holds the claw (servo/channel 0 on
PCA9685 HAT `0x40`) MUST read the claw's INA219 ammeter at I2C address `0x41`
on the configured bus. This includes standalone scripts, ad hoc commands, combined
neutral/startup tests, commissioning, and main-app testing. Read it before enabling
claw output and continuously throughout movement and powered holding; an I2C scan
or one initial reading is not sufficient. Stop testing and attempt PWM shutdown on
missing, stale, invalid, or failed readings; instruct the operator to cut physical
servo power. Do not retry motion automatically after a sensor fault.

Use `abs(current)` regardless of wiring direction. With a verified shunt resistance,
require `abs(current) < 2.5 A`; at or above 2.5 A, abort and attempt PWM shutdown.
Until the shunt resistance/current scale is verified, display raw shunt voltage and
explicitly label amperes and the 2.5 A cutoff as unverified. Never silently assume
a shunt value. Raw readings are mandatory even during these supervised scale-
unverified tests. Polling and PWM shutdown are not a physical current limiter.
Mock tests must identify simulated readings as simulated. Physical claw test paths
without this monitoring must be updated before use; do not bypass the rule.
