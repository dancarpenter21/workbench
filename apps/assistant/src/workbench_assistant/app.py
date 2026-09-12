import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException
from workbench_common import ROOT, app_base, settings
from workbench_contracts import Health, Intent, TextRequest


def mock_intent(text: str, tools: list[dict]) -> Intent:
    text = text.lower().strip()
    if re.search(r"\b(not|don't|do not|never|stop|cancel)\b", text):
        return Intent(action="clarify", message="No retrieval requested. Use Stop to halt the arm.")
    if re.fullmatch(r"(get |what is (the )?)?status[?.!]?", text):
        return Intent(action="get_status")
    matches = [
        t["id"]
        for t in tools
        if any(re.search(r"\b" + re.escape(alias) + r"\b", text) for alias in t["aliases"])
    ]
    if len(matches) != 1 or not re.search(r"\b(grab|fetch|retrieve|bring|get)\b", text):
        return Intent(action="clarify", message="Ask me to retrieve exactly one known tool.")
    return Intent(action="retrieve_tool", tool_id=matches[0])


def create_app(config=None):
    cfg = config or settings()
    tools = cfg["workbench"]["tools"]
    state = {"model": None, "error": ""}
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if cfg["mode"] == "hardware":
            try:
                from llama_cpp import Llama

                path = ROOT / Path(cfg["llm"]["model_path"])
                if not path.is_file():
                    raise ValueError(f"Local GGUF model missing: {path}")
                state["model"] = await asyncio.to_thread(
                    Llama,
                    model_path=str(path),
                    n_gpu_layers=cfg["llm"]["gpu_layers"],
                    n_ctx=cfg["llm"]["context_size"],
                    verbose=False,
                )
            except Exception as exc:
                state["error"] = str(exc)
        yield
        if state["model"]:
            state["model"].close()

    app = app_base("assistant", lifespan)

    @app.get("/health", response_model=Health)
    async def health():
        return Health(
            service="assistant", mode=cfg["mode"], ready=not state["error"], detail=state["error"]
        )

    @app.post("/interpret", response_model=Intent)
    async def interpret(request: TextRequest):
        if cfg["mode"] == "mock":
            return mock_intent(request.text, tools)
        if state["model"] is None:
            raise HTTPException(503, state["error"] or "Model is not loaded")
        if lock.locked():
            raise HTTPException(409, "Assistant is busy")
        async with lock:
            try:
                result = await asyncio.to_thread(
                    state["model"].create_chat_completion,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Interpret a workbench request. Never invent tool IDs. Only retrieve when "
                                "the user explicitly requests one tool. For negation, multiple tools, "
                                "uncertainty or unknown tools, use clarify and explain. Return JSON with "
                                "action (retrieve_tool, get_status, clarify), tool_id (string or null), "
                                "and message. Tool catalog: " + json.dumps(tools)
                            ),
                        },
                        {"role": "user", "content": request.text},
                    ],
                    response_format={"type": "json_object", "schema": Intent.model_json_schema()},
                    temperature=0,
                    max_tokens=200,
                )
                intent = Intent.model_validate_json(result["choices"][0]["message"]["content"])
                if intent.action == "retrieve_tool" and intent.tool_id not in {
                    t["id"] for t in tools
                }:
                    raise ValueError("Unknown tool identifier")
                return intent
            except Exception:
                return Intent(
                    action="clarify",
                    message="I could not interpret that reliably. Please rephrase.",
                )

    return app
