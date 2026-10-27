"""Exercise real ROS actions and lifecycle topics using strictly mock components."""

import io
import os
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait(future, timeout=15):
    ready = threading.Event()
    future.add_done_callback(lambda _: ready.set())
    if not ready.wait(timeout):
        raise TimeoutError("ROS operation did not finish before its deadline")
    return future.result()


def main():
    # Enforce the domain before importing or constructing any application component.
    os.environ["WORKBENCH_MODE"] = "mock"
    os.environ["WORKBENCH_CONFIG"] = str(ROOT / "config/development/default.json")
    os.environ["WORKBENCH_CALIBRATION"] = str(ROOT / "config/workbench/default.json")

    import rclpy
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState
    from rclpy.action import ActionClient
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from workbench_inference.node import InferenceNode
    from workbench_interfaces.action import Interpret, Transcribe
    from workbench_interfaces.msg import Observations
    from workbench_vision.ros_node import VisionNode

    rclpy.init()
    assistant = InferenceNode("assistant")
    voice = InferenceNode("voice")
    vision = VisionNode()
    client = Node("workbench_smoke")
    executor = MultiThreadedExecutor(num_threads=6)
    for node in (assistant, voice, vision, client):
        executor.add_node(node)
    spinning = threading.Thread(target=executor.spin)
    spinning.start()
    try:
        interpret = ActionClient(client, Interpret, "/workbench/assistant/interpret")
        transcribe = ActionClient(client, Transcribe, "/workbench/voice/transcribe")
        assert interpret.wait_for_server(timeout_sec=10)
        assert transcribe.wait_for_server(timeout_sec=10)
        handle = wait(
            interpret.send_goal_async(
                Interpret.Goal(
                    operation_id="ros-smoke-intent",
                    text="fetch the screwdriver",
                )
            )
        )
        assert handle.accepted
        result = wait(handle.get_result_async()).result
        assert result.action == "retrieve_tool" and result.tool_id == "screwdriver"

        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 1600)
        handle = wait(
            transcribe.send_goal_async(
                Transcribe.Goal(
                    operation_id="ros-smoke-speech",
                    wav=list(audio.getvalue()),
                )
            )
        )
        assert handle.accepted
        result = wait(handle.get_result_async()).result
        assert result.success and result.text == "Grab me the screwdriver"

        received = threading.Event()
        observations = []

        def observe(message):
            observations.append(message)
            received.set()

        subscription = client.create_subscription(
            Observations,
            "/workbench/observations",
            observe,
            1,
        )
        lifecycle = client.create_client(ChangeState, "/vision/change_state")
        assert lifecycle.wait_for_service(timeout_sec=10)
        for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            response = wait(
                lifecycle.call_async(
                    ChangeState.Request(
                        transition=Transition(id=transition),
                    )
                )
            )
            assert response.success
        assert received.wait(10), "No observation after vision activation"
        message = observations[-1]
        assert message.domain == "simulated" and message.camera_ok and message.tray_clear
        assert len(message.tools) == 3 and all(not t.pose_valid for t in message.tools)
        assert wait(
            lifecycle.call_async(
                ChangeState.Request(
                    transition=Transition(id=Transition.TRANSITION_DEACTIVATE),
                )
            )
        ).success
        time.sleep(0.1)  # Drain any already-delivered callback before checking inactivity.
        received.clear()
        assert not received.wait(0.2), "Inactive camera continued publishing"
        client.destroy_subscription(subscription)
        print("PASS: ROS speech/intent actions, vision lifecycle, simulated observations")
    finally:
        executor.shutdown()
        spinning.join()
        assistant.close()
        voice.close()
        vision.stop_capture()
        vision.destroy_node()
        client.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
