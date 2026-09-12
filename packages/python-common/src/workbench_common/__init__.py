"""Configuration, HTTP errors, correlation logs, and durable local state."""

import asyncio
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from workbench_contracts import Error

ROOT = Path(os.environ.get("WORKBENCH_ROOT", Path(__file__).resolve().parents[4]))


def settings() -> dict:
    path = Path(os.environ.get("WORKBENCH_CONFIG", ROOT / "config/development/default.json"))
    data = json.loads(path.read_text())
    data["mode"] = os.environ.get("WORKBENCH_MODE", data["mode"])
    if data["mode"] not in {"mock", "hardware"}:
        raise ValueError("WORKBENCH_MODE must be mock or hardware")
    bench = Path(os.environ.get("WORKBENCH_CALIBRATION", ROOT / "config/workbench/default.json"))
    data["workbench"] = json.loads(bench.read_text())
    return data


def app_base(name: str, lifespan=None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = FastAPI(
        title=f"Workbench {name}",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            400: {"model": Error},
            409: {"model": Error},
            422: {"model": Error},
            503: {"model": Error},
        },
    )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail
        error = (
            detail
            if isinstance(detail, dict)
            else {"code": "request_failed", "message": str(detail)}
        )
        return JSONResponse(error, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"code": "invalid_request", "message": str(exc)}, status_code=422)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        response = await call_next(request)
        operation_id = request.headers.get("x-operation-id", "-")
        logging.getLogger(name).info(
            "operation=%s %s %s %s",
            operation_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    return app


class Store:
    """Single-process durable key/value state. Run one worker per application."""

    def __init__(self, name: str, directory: Path | None = None):
        directory = directory or Path(os.environ.get("WORKBENCH_STATE_DIR", ROOT / ".runtime"))
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / f"{name}.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.lock = threading.Lock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.db.commit()
        self.values = dict(self.db.execute("SELECT key, value FROM state"))

    def get(self, key, default=None):
        value = self.values.get(key)
        return json.loads(value) if value is not None else default

    def put(self, key, value):
        self.put_many({key: value})

    def put_many(self, values):
        encoded = {key: json.dumps(value) for key, value in values.items()}
        with self.lock:
            with self.db:
                self.db.executemany("INSERT OR REPLACE INTO state VALUES (?, ?)", encoded.items())
            self.values.update(encoded)

    async def aput_many(self, values):
        # Finish a pending commit even if the workflow is cancelled. No hardware
        # dispatch may overtake its durable intent, and shutdown must not close
        # SQLite while a worker is still using it.
        task = asyncio.create_task(asyncio.to_thread(self.put_many, values))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def aput(self, key, value):
        await self.aput_many({key: value})

    def close(self):
        self.db.close()
