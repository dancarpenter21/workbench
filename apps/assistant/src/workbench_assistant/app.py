from contextlib import asynccontextmanager

from fastapi import HTTPException
from workbench_common import app_base, settings
from workbench_common.diagnostics import model_check
from workbench_common.inference import EngineBusy, EngineUnavailable
from workbench_contracts import Diagnostic, Health, Intent, TextRequest

from .engine import AssistantEngine, mock_intent  # noqa: F401 -- retained public import


def create_app(config=None):
    cfg = config or settings()
    engine = AssistantEngine(cfg)

    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        try:
            yield
        finally:
            await engine.close()

    app = app_base("assistant", lifespan)

    @app.get("/diagnostics", response_model=list[Diagnostic])
    async def diagnostics():
        return [
            model_check(
                "assistant", cfg, "llama_cpp", cfg["llm"]["model_path"], engine.model is not None
            )
        ]

    @app.get("/health", response_model=Health)
    async def health():
        return Health(
            service="assistant", mode=cfg["mode"], ready=not engine.error, detail=engine.error
        )

    @app.post("/interpret", response_model=Intent)
    async def interpret(request: TextRequest):
        try:
            return await engine.interpret(request.text)
        except EngineBusy as exc:
            raise HTTPException(409, "Assistant is busy") from exc
        except EngineUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    return app
