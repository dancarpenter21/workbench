# Python common utilities

Shared configuration loading, FastAPI error handling and operation correlation logging,
plus a small SQLite key/value store. This package contains no vision, language or robot
workflow behavior.

Applications read settings once on startup. Repository defaults can be overridden with
`WORKBENCH_ROOT`, `WORKBENCH_CONFIG`, `WORKBENCH_CALIBRATION`, `WORKBENCH_MODE`, and
`WORKBENCH_STATE_DIR`; see the root README. The SQLite store is for a single process per
application, not a distributed lock. Coordinator and arm journals are separate files.

Install with `uv sync` at the repository root. Integration tests exercise its consumers.
