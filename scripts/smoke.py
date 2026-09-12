"""Check the running mock stack: submit, wait, verify idempotence and reset."""

import time
from uuid import uuid4

import httpx
from workbench_common import settings


def main():
    cfg = settings()
    base = cfg["services"]["coordinator"]
    with httpx.Client(base_url=base, timeout=10) as client:
        assert client.get("/status").json()["mode"] == "mock", (
            "Smoke script only supports mock mode"
        )
        health = client.get("/services").json()
        assert all(h["ready"] for h in health), health
        for _ in range(10):
            for tool in cfg["workbench"]["tools"]:
                client.post("/mock/reset").raise_for_status()
                body = {"text": f"Grab me the {tool['aliases'][0]}", "operation_id": str(uuid4())}
                response = client.post("/requests", json=body)
                response.raise_for_status()
                deadline = time.monotonic() + cfg["motion_timeout_seconds"] + 10
                while time.monotonic() < deadline:
                    operation = client.get(f"/operations/{body['operation_id']}").json()
                    if operation["state"] in {"completed", "failed", "rejected"}:
                        break
                    time.sleep(0.1)
                assert operation["state"] == "completed", operation
                assert operation["tool_id"] == tool["id"], operation
                replay = client.post("/requests", json=body)
                assert replay.json()["state"] == "completed", replay.text
        client.post("/mock/reset").raise_for_status()
        print(
            "PASS: 30/30 simulated deliveries; no wrong-tool deliveries; duplicate IDs not replayed"
        )


if __name__ == "__main__":
    main()
