"""Local intent interpretation shared by ROS and browser transports."""

import asyncio
import json
import re
from pathlib import Path

from workbench_common import ROOT
from workbench_common.inference import EngineUnavailable, run_serialized
from workbench_contracts import Intent


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


class AssistantEngine:
    def __init__(self, config):
        self.config = config
        self.tools = config["workbench"]["tools"]
        self.model = None
        self.error = ""
        self.lock = asyncio.Lock()

    async def start(self):
        if self.config["mode"] == "mock":
            return
        try:
            from llama_cpp import Llama

            path = ROOT / Path(self.config["llm"]["model_path"])
            if not path.is_file():
                raise ValueError(f"Local GGUF model missing: {path}")
            self.model = await asyncio.to_thread(
                Llama,
                model_path=str(path),
                n_gpu_layers=self.config["llm"]["gpu_layers"],
                n_ctx=self.config["llm"]["context_size"],
                verbose=False,
            )
        except Exception as exc:
            self.error = str(exc)

    async def close(self):
        async with self.lock:
            if self.model is not None:
                self.model.close()
                self.model = None

    async def interpret(self, text):
        if self.config["mode"] == "mock":
            return mock_intent(text, self.tools)
        if self.model is None:
            raise EngineUnavailable(self.error or "Model is not loaded")

        def run():
            try:
                result = self.model.create_chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Interpret a workbench request. Never invent tool IDs. Only retrieve when "
                                "the user explicitly requests one tool. For negation, multiple tools, "
                                "uncertainty or unknown tools, use clarify and explain. Return JSON with "
                                "action (retrieve_tool, get_status, clarify), tool_id (string or null), "
                                "and message. Tool catalog: " + json.dumps(self.tools)
                            ),
                        },
                        {"role": "user", "content": text},
                    ],
                    response_format={"type": "json_object", "schema": Intent.model_json_schema()},
                    temperature=0,
                    max_tokens=200,
                )
                intent = Intent.model_validate_json(result["choices"][0]["message"]["content"])
                if intent.action == "retrieve_tool" and intent.tool_id not in {
                    t["id"] for t in self.tools
                }:
                    raise ValueError("Unknown tool identifier")
                return intent
            except Exception:
                return Intent(
                    action="clarify",
                    message="I could not interpret that reliably. Please rephrase.",
                )

        return await run_serialized(self.lock, run)
