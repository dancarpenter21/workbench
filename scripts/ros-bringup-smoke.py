"""Launch all native backend owners in mock mode and verify the browser boundary."""

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--planning", action="store_true", help="Exercise real mock MoveIt retrieval"
    )
    parser.add_argument(
        "--tool", choices=("screwdriver", "adjustable_wrench", "pliers"), default="screwdriver"
    )
    parser.add_argument("--gateway-port", type=int, default=18100)
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.gateway_port}"
    if not (ROOT / "ros/install/.workbench_ready").is_file():
        raise RuntimeError("Build the native ROS project overlay first")
    with tempfile.TemporaryDirectory(prefix="workbench-bringup-") as directory:
        config = json.loads((ROOT / "config/development/default.json").read_text())
        config["arm"]["backend"] = "pca9685"
        config["motion_timeout_seconds"] = 180
        config_path = Path(directory) / "config.json"
        config_path.write_text(json.dumps(config))
        env = {
            **os.environ,
            "WORKBENCH_STATE_DIR": directory,
            "WORKBENCH_CALIBRATION": str(ROOT / "config/workbench/default.json"),
            "WORKBENCH_GATEWAY_PORT": str(args.gateway_port),
        }
        log_path = ROOT / ".runtime/ros/bringup-smoke.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [
                    "ros2",
                    "launch",
                    "workbench_bringup",
                    "workbench.launch.py",
                    "mode:=mock",
                    f"config:={config_path}",
                    f"planning:={'true' if args.planning else 'false'}",
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"Bringup exited; inspect {log_path}")
                    try:
                        with urllib.request.urlopen(
                            base_url + "/hardware/status", timeout=2
                        ) as response:
                            status = json.load(response)
                        assert status["claw_current"]["simulated"]
                        break
                    except (OSError, urllib.error.HTTPError):
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.2)
                with urllib.request.urlopen(base_url + "/harness/arm", timeout=5) as response:
                    harness = json.load(response)
                    assert harness["simulated"] and not harness["active"]
                request = urllib.request.Request(
                    base_url + "/mcp",
                    data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    tools = json.load(response)["result"]["tools"]
                    assert "execute_sequence" in {tool["name"] for tool in tools}
                    assert "harness_enable" not in {tool["name"] for tool in tools}
                with urllib.request.urlopen(base_url + "/manual/status", timeout=5) as response:
                    assert not json.load(response)["active"]
                with urllib.request.urlopen(
                    base_url + "/commissioning/robot-model", timeout=5
                ) as response:
                    model = json.load(response)
                    assert model["domain"] == "simulated" and model["valid"]
                model["model"]["complete"] = False
                request = urllib.request.Request(
                    base_url + "/commissioning/robot-model",
                    data=json.dumps(
                        {key: model[key] for key in ("context_key", "revision", "model")}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.load(response)
                    assert saved["revision"] == model["revision"] + 1 and not saved["valid"]
                with urllib.request.urlopen(
                    base_url + "/diagnostics/microphone/devices", timeout=5
                ) as response:
                    assert json.load(response)[0]["id"] == "mock"
                request = urllib.request.Request(
                    base_url + "/diagnostics/microphone/test",
                    data=b'{"device_id":"mock"}',
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    recording = json.load(response)
                    assert recording["simulated"] and recording["duration_seconds"] == 5
                print(
                    "PASS: mock ROS bringup, lifecycle, typed controls, microphone service/action"
                )
                if args.planning:
                    deadline = time.monotonic() + 90
                    while True:
                        with urllib.request.urlopen(base_url + "/status", timeout=5) as response:
                            status = json.load(response)
                        if status["retrieval_enabled"]:
                            break
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError(f"Mock planning did not become ready: {status}")
                        time.sleep(0.2)
                    operation_id = str(uuid4())
                    request = urllib.request.Request(
                        base_url + "/requests",
                        data=json.dumps(
                            {
                                "operation_id": operation_id,
                                "text": "fetch the " + args.tool.replace("_", " "),
                            }
                        ).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        assert response.status == 202
                    deadline = time.monotonic() + 210
                    while True:
                        with urllib.request.urlopen(
                            f"{base_url}/operations/{operation_id}", timeout=5
                        ) as response:
                            operation = json.load(response)
                        if operation["state"] == "completed":
                            break
                        if operation["state"] in ("failed", "rejected", "stopped", "clarification"):
                            raise RuntimeError(f"Mock retrieval failed: {operation}")
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise TimeoutError(f"Mock retrieval timed out: {operation}")
                        time.sleep(0.2)
                    print("PASS: real MoveIt Task Constructor retrieval through browser gateway")
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise RuntimeError("Bringup did not shut down within 40 seconds") from None


if __name__ == "__main__":
    main()
