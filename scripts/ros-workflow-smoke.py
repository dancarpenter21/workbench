"""Test ROS coordination with a fake arm, never with a hardware or MoveIt executor."""

import asyncio
import io
import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def wait(future, timeout=15):
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    if not event.wait(timeout):
        raise TimeoutError("ROS future timed out")
    return future.result()


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("ROS state transition timed out")
        time.sleep(0.02)


def main():
    os.environ["WORKBENCH_MODE"] = "mock"
    os.environ["WORKBENCH_CONFIG"] = str(ROOT / "config/development/default.json")
    os.environ["WORKBENCH_CALIBRATION"] = str(ROOT / "config/workbench/default.json")
    directory = tempfile.TemporaryDirectory(prefix="workbench-ros-workflow-")
    os.environ["WORKBENCH_STATE_DIR"] = directory.name

    import rclpy
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState
    from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from std_srvs.srv import Trigger
    from workbench_coordinator.ros_gateway import create_app as gateway_app
    from workbench_coordinator.ros_node import CoordinatorNode
    from workbench_inference.node import InferenceNode
    from workbench_interfaces.action import RequestTool, RetrieveTool
    from workbench_interfaces.msg import ArmReadiness, ExecutionEvidence, Observation
    from workbench_interfaces.srv import GetOperation, Recover, SetMockScene
    from workbench_vision.ros_node import VisionNode

    class FakeArm(Node):
        def __init__(self):
            super().__init__("fake_arm")
            self.calls = []
            self.slow = False
            self.busy = False
            self.recovery = False
            self.closing = threading.Event()
            group = ReentrantCallbackGroup()
            self.server = ActionServer(
                self,
                RetrieveTool,
                "/workbench/arm/retrieve",
                execute_callback=self.execute,
                goal_callback=lambda _: (
                    GoalResponse.REJECT if self.busy or self.recovery else GoalResponse.ACCEPT
                ),
                cancel_callback=lambda _: CancelResponse.ACCEPT,
                callback_group=group,
            )
            self.pub = self.create_publisher(ArmReadiness, "/workbench/arm/readiness", 1)
            self.create_timer(0.05, self.publish)
            self.create_service(
                Recover, "/workbench/arm/recover", self.recover, callback_group=group
            )
            self.create_service(Trigger, "/workbench/arm/stop", self.stop, callback_group=group)

        def publish(self):
            message = ArmReadiness(
                domain="simulated",
                ready=not self.busy and not self.recovery,
                recovery_required=self.recovery,
            )
            message.header.stamp = self.get_clock().now().to_msg()
            self.pub.publish(message)

        def execute(self, goal):
            self.busy = True
            self.calls.append(goal.request.operation_id)
            try:
                deadline = time.monotonic() + 10
                while self.slow and not self.closing.is_set():
                    if goal.is_cancel_requested:
                        self.recovery = True
                        goal.canceled()
                        return RetrieveTool.Result(success=False, message="Simulated cancellation")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Fake arm test deadline")
                    time.sleep(0.02)
                goal.succeed()
                result = RetrieveTool.Result(
                    success=True,
                    evidence=ExecutionEvidence(
                        domain="simulated",
                        operation_id=goal.request.operation_id,
                        position_source="simulated",
                        measured_feedback=False,
                    ),
                )
                result.evidence.header.stamp = self.get_clock().now().to_msg()
                return result
            finally:
                self.busy = False

        def stop(self, request, response):
            self.recovery = True
            response.success, response.message = True, "Simulated stop requested"
            return response

        def recover(self, request, response):
            response.recovered = request.acknowledged and not self.busy
            if response.recovered:
                self.recovery = False
            return response

    rclpy.init()
    assistant = InferenceNode("assistant")
    voice = InferenceNode("voice")
    vision = VisionNode()
    arm = FakeArm()
    coordinator = CoordinatorNode()
    client = Node("workflow_smoke")
    executor = MultiThreadedExecutor(num_threads=8)
    for node in (assistant, voice, vision, arm, coordinator, client):
        executor.add_node(node)
    thread = threading.Thread(target=executor.spin)
    thread.start()
    try:
        lifecycle = client.create_client(ChangeState, "/vision/change_state")
        assert lifecycle.wait_for_service(timeout_sec=10)
        for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            assert wait(
                lifecycle.call_async(ChangeState.Request(transition=Transition(id=transition)))
            ).success
        action = ActionClient(client, RequestTool, "/workbench/requests")
        assert action.wait_for_server(timeout_sec=10)
        until(lambda: coordinator.arm_ready() and coordinator.latest_observations is not None)

        def submit(text, operation_id=None):
            return wait(
                action.send_goal_async(
                    RequestTool.Goal(
                        operation_id=operation_id or str(uuid4()),
                        text=text,
                    )
                )
            )

        first_id = str(uuid4())
        handle = submit("fetch the screwdriver", first_id)
        assert handle.accepted
        result = wait(handle.get_result_async()).result
        assert result.success and result.status.state == "completed"
        assert result.status.evidence.delivery_observed
        assert result.status.evidence.domain == "simulated"
        assert arm.calls == [first_id]
        duplicate = submit("fetch the screwdriver", first_id.upper())
        assert duplicate.accepted
        assert wait(duplicate.get_result_async()).result.success
        assert arm.calls == [first_id], "Duplicate request executed again"
        assert not submit("fetch the pliers", first_id).accepted
        assert not submit("fetch the pliers", "not-a-uuid").accepted

        # Tray is occupied after the simulated delivery: this request must not reach the arm.
        handle = submit("fetch the pliers")
        assert handle.accepted
        assert wait(handle.get_result_async()).result.status.state == "rejected"
        assert arm.calls == [first_id]

        scene = client.create_client(SetMockScene, "/workbench/mock/scene")
        assert scene.wait_for_service(timeout_sec=10)

        def reset_scene(age):
            request = SetMockScene.Request(
                camera_ok=True,
                tray_clear=True,
                age_seconds=float(age),
                tools=[
                    Observation(tool_id=name, location="source", confidence=1.0)
                    for name in ("adjustable_wrench", "screwdriver", "pliers")
                ],
            )
            assert wait(scene.call_async(request)).accepted
            until(
                lambda: (
                    coordinator.observations().tray_clear
                    and (
                        (time.time() - coordinator.observations().captured_at.timestamp() > 5)
                        if age
                        else (time.time() - coordinator.observations().captured_at.timestamp() < 1)
                    )
                )
            )

        reset_scene(10)
        handle = submit("fetch the pliers")
        assert handle.accepted
        assert wait(handle.get_result_async()).result.status.state == "rejected"
        assert arm.calls == [first_id], "Stale observations authorized motion"

        reset_scene(0)
        until(coordinator.arm_ready)
        arm.slow = True
        handle = submit("fetch the pliers")
        assert handle.accepted
        until(lambda: len(arm.calls) == 2)
        wait(handle.cancel_goal_async())
        assert wait(handle.get_result_async()).result.status.state == "stopped"
        until(lambda: not arm.busy)
        assert coordinator.store.get("recovery")
        assert not submit("fetch the pliers").accepted

        lookup = client.create_client(GetOperation, "/workbench/operations/get")
        assert lookup.wait_for_service(timeout_sec=10)
        historical = wait(lookup.call_async(GetOperation.Request(operation_id=first_id.upper())))
        assert historical.found and historical.operation.evidence.delivery_observed
        assert historical.operation.evidence.operation_id == first_id

        recover = client.create_client(Recover, "/workbench/recover")
        assert recover.wait_for_service(timeout_sec=10)
        assert not wait(recover.call_async(Recover.Request(acknowledged=True))).recovered
        assert wait(
            recover.call_async(
                Recover.Request(
                    acknowledged=True,
                    inspection_evidence="Simulated fixture inspection",
                )
            )
        ).recovered
        until(coordinator.arm_ready)
        arm.slow = False

        async def browser_gateway():
            import httpx

            app = gateway_app(coordinator.cfg)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url="http://ros-gateway",
                ) as browser:
                    deadline = time.monotonic() + 10
                    while not (await browser.get("/health")).json()["ready"]:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Gateway did not receive coordinator status")
                        await asyncio.sleep(0.05)
                    audio = io.BytesIO()
                    with wave.open(audio, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        wav.writeframes(b"\0\0" * 1600)
                    transcript = await browser.post("/transcriptions", content=audio.getvalue())
                    assert transcript.status_code == 200, transcript.text
                    assert transcript.json()["text"] == "Grab me the screwdriver"
                    browser_id = str(uuid4())
                    response = await browser.post(
                        "/requests",
                        json={
                            "operation_id": browser_id,
                            "text": "fetch the adjustable wrench",
                        },
                    )
                    assert response.status_code == 202, response.text
                    deadline = time.monotonic() + 10
                    while True:
                        operation = (await browser.get(f"/operations/{browser_id}")).json()
                        if operation.get("state") == "completed":
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError(operation)
                        await asyncio.sleep(0.05)
                    assert arm.calls.count(browser_id) == 1
                    assert (await browser.get(f"/operations/{first_id}")).json()[
                        "state"
                    ] == "completed"

        asyncio.run(browser_gateway())
        print(
            "PASS: ROS workflow, durable deduplication, stale/occupied rejection, cancellation, recovery"
        )
        print("PASS: browser gateway over real ROS actions/topics with a fake arm")
    finally:
        coordinator.stop_requested.set()
        arm.closing.set()
        executor.shutdown()
        thread.join()
        coordinator.server.destroy()
        coordinator.store.close()
        coordinator.destroy_node()
        arm.server.destroy()
        arm.destroy_node()
        assistant.close()
        voice.close()
        vision.stop_capture()
        vision.destroy_node()
        client.destroy_node()
        rclpy.shutdown()
        directory.cleanup()


if __name__ == "__main__":
    main()
