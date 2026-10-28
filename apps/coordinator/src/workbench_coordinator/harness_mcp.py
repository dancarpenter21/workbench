"""Local, stateless MCP Streamable HTTP (JSON responses, protocol 2025-11-25).

Supports discovery, tools, prompts, and ping. No SSE subscription, remote hosting,
MCP sampling, or session ownership: motion always requires the Workbench lease.
"""

import json
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .harness import definitions

PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
LOCAL = {"localhost", "127.0.0.1", "::1"}


def mount_mcp(app, service):
    @app.api_route("/mcp", methods=["POST", "GET", "DELETE"], include_in_schema=False)
    async def endpoint(request: Request):
        origin = request.headers.get("origin")
        try:
            host = urlsplit("http://" + request.headers.get("host", "")).hostname
            origin_host = urlsplit(origin).hostname if origin else None
        except ValueError:
            return Response(status_code=403)
        if host not in LOCAL or (origin and origin_host not in LOCAL):
            return Response(status_code=403)
        if (
            request.client
            and request.client.host not in LOCAL
            and request.client.host != "testclient"
        ):
            return Response(status_code=403)
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return Response(status_code=415)
        version = request.headers.get("mcp-protocol-version")
        if version and version not in PROTOCOLS:
            return Response(status_code=400)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 65536:
                return Response(status_code=413)
        request_id = None

        def error(code, message):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
            )

        try:
            body = json.loads(data)
        except (ValueError, UnicodeError):
            return error(-32700, "Invalid JSON")
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("method"), str)
        ):
            return error(-32600, "Invalid JSON-RPC request")
        request_id = body.get("id")
        if "id" in body and (
            isinstance(request_id, bool) or not isinstance(request_id, (str, int))
        ):
            request_id = None
            return error(-32600, "Invalid request ID")
        method = body["method"]
        if "id" not in body:
            # Notifications never dispatch tools.
            return Response(status_code=202)
        params = body.get("params", {})
        if not isinstance(params, dict):
            return error(-32602, "Parameters must be an object")
        if method == "initialize":
            if not isinstance(params.get("protocolVersion"), str):
                return error(-32602, "protocolVersion is required")
            result = {
                "protocolVersion": params.get("protocolVersion")
                if params.get("protocolVersion") in PROTOCOLS
                else "2025-11-25",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "workbench", "version": "0.1.0"},
                "instructions": "Enable physical control in Workbench. Read the workbench prompt. No automatic recovery or motion retries.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in definitions(service.config())
                    if t.enabled
                ]
            }
        elif method == "prompts/list":
            result = {
                "prompts": [
                    {
                        "name": "workbench",
                        "description": "Configured workbench operating prompt",
                        "arguments": [],
                    }
                ]
            }
        elif method == "prompts/get":
            if params.get("name") != "workbench":
                return error(-32602, "Unknown prompt")
            result = {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": service.config().system_prompt},
                    }
                ]
            }
        elif method == "tools/call":
            try:
                value = await service.call(params.get("name"), params.get("arguments", {}))
                image = value.get("image")
                text = {k: v for k, v in value.items() if k != "image"}
                content = [{"type": "text", "text": json.dumps(text)}]
                if image:
                    content.append({"type": "image", "mimeType": "image/jpeg", "data": image})
                result = {"content": content, "isError": False}
            except Exception as exc:
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": str(
                                getattr(
                                    exc,
                                    "detail",
                                    "Tool failed; inspect arm state. Do not retry motion.",
                                )
                            ),
                        }
                    ],
                    "isError": True,
                }
        else:
            return error(-32601, "Method not found")
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
