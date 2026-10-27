"""Exercise native ROS arm lifecycle and fail-closed controls with simulated I/O only."""

import asyncio
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def wait(future, timeout=15):
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    if not event.wait(timeout):
        raise TimeoutError("ROS control future timed out")
    return future.result()


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("ROS control state timed out")
        time.sleep(0.02)


def main():
    with tempfile.TemporaryDirectory(prefix="workbench-ros-control-") as directory:
        config = json.loads((ROOT / "config/development/default.json").read_text())
        config["mode"] = "mock"
        config["arm"]["backend"] = "pca9685"
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config))
        os.environ.update(
            WORKBENCH_MODE="mock",
            WORKBENCH_CONFIG=str(path),
            WORKBENCH_STATE_DIR=directory,
            WORKBENCH_CALIBRATION=str(ROOT / "config/workbench/default.json"),
        )
        import rclpy
        from lifecycle_msgs.msg import Transition
        from lifecycle_msgs.srv import ChangeState
        from rclpy.action import ActionClient, ActionServer, CancelResponse
        from rclpy.callback_groups import ReentrantCallbackGroup
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.node import Node
        from workbench_arm_controller.ros_node import ArmNode
        from workbench_interfaces.action import RetrieveTool
        from workbench_interfaces.msg import ArmReadiness, ClawCurrent, ExecutionEvidence
        from workbench_interfaces.srv import (
            ControlHardwareStatus,
            ControlManualInput,
            ControlManualStatus,
        )

        rclpy.init()
        arm, client = ArmNode(), Node("control_smoke")
        planner = Node("control_smoke_planner")
        released = threading.Event()

        def execute(goal):
            deadline = time.monotonic() + 10
            while not released.wait(0.02):
                if goal.is_cancel_requested:
                    goal.canceled()
                    return RetrieveTool.Result(success=False, message="Simulated cancellation")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Fake planner release timed out")
            goal.succeed()
            evidence = ExecutionEvidence(domain="simulated", operation_id=goal.request.operation_id)
            evidence.header.stamp = planner.get_clock().now().to_msg()
            return RetrieveTool.Result(success=True, evidence=evidence)

        planner_server = ActionServer(
            planner,
            RetrieveTool,
            "/workbench/planning/retrieve",
            execute_callback=execute,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup(),
        )
        planner_pub = planner.create_publisher(ArmReadiness, "/workbench/planning/readiness", 1)

        def ready():
            message = ArmReadiness(domain="simulated", ready=True)
            message.header.stamp = planner.get_clock().now().to_msg()
            planner_pub.publish(message)

        planner.create_timer(0.05, ready)
        executor = MultiThreadedExecutor(num_threads=6)
        for node in (arm, client, planner):
            executor.add_node(node)
        samples = []
        client.create_subscription(ClawCurrent, "/workbench/claw_current", samples.append, 10)
        thread = threading.Thread(target=executor.spin)
        thread.start()
        try:
            lifecycle = client.create_client(ChangeState, "/workbench_arm_owner/change_state")
            assert lifecycle.wait_for_service(timeout_sec=10)

            def transition(value):
                assert wait(
                    lifecycle.call_async(ChangeState.Request(transition=Transition(id=value)))
                ).success

            transition(Transition.TRANSITION_CONFIGURE)
            transition(Transition.TRANSITION_ACTIVATE)

            def call(kind, endpoint, domain="simulated", **kwargs):
                service = client.create_client(kind, "/workbench/arm/control/" + endpoint)
                try:
                    assert service.wait_for_service(timeout_sec=10)
                    return wait(service.call_async(kind.Request(domain=domain, **kwargs)))
                finally:
                    client.destroy_client(service)

            until(lambda: bool(samples))
            assert all(sample.domain == "simulated" for sample in samples)
            assert math.isfinite(samples[-1].shunt_voltage_mv)
            assert not samples[-1].scale_verified and math.isnan(samples[-1].current_a)
            response = call(ControlHardwareStatus, "hardware_status")
            assert response.success and response.value.claw_current.has_shunt_mv
            assert not response.value.claw_current.has_current_a
            response = call(ControlManualStatus, "manual_status", domain="physical")
            assert not response.success and response.error_status == 409
            bad = ControlManualInput.Request().request
            bad.token, bad.sequence, bad.joint_id, bad.speed = "invalid", 1, "claw", 2.0
            response = call(ControlManualInput, "manual_input", request=bad)
            assert not response.success and response.error_status == 422

            # The public action reserves the owner before forwarding to the internal planner.
            retrieval = ActionClient(client, RetrieveTool, "/workbench/arm/retrieve")
            assert retrieval.wait_for_server(timeout_sec=10)
            until(arm.eligible)
            request = RetrieveTool.Goal(operation_id=str(uuid4()), tool_id="screwdriver")
            goal = wait(retrieval.send_goal_async(request))
            assert goal.accepted
            until(lambda: arm.child is not None)
            bad.speed = 0.1
            bad.token = str(uuid4())
            response = call(ControlManualInput, "manual_input", request=bad)
            assert not response.success and response.error_status == 409
            assert "owns the arm" in response.error_message
            status = call(ControlManualStatus, "manual_status")
            assert status.success and not status.value.available
            released.set()
            assert wait(goal.get_result_async()).result.success
            until(arm.eligible)
            assert not wait(retrieval.send_goal_async(request)).accepted
            released.clear()
            goal = wait(
                retrieval.send_goal_async(
                    RetrieveTool.Goal(operation_id=str(uuid4()), tool_id="pliers")
                )
            )
            assert goal.accepted
            until(lambda: arm.child is not None)
            wait(goal.cancel_goal_async())
            assert not wait(goal.get_result_async()).result.success
            assert arm.store.get("recovery") and not arm.eligible()
            retrieval.destroy()

            async def fail_monitor():
                arm.app.state.monitor_worker.cancel()
                await asyncio.gather(arm.app.state.monitor_worker, return_exceptions=True)

            arm.run_async(fail_monitor())
            until(lambda: bool(arm.control_error))
            assert "monitoring stopped" in arm.control_error
            assert not arm.eligible()
            transition(Transition.TRANSITION_DEACTIVATE)
            transition(Transition.TRANSITION_CLEANUP)
            print(
                "PASS: simulated ROS arm lifecycle, typed validation, current evidence, monitor fault"
            )
        finally:
            executor.shutdown(timeout_sec=10)
            thread.join(timeout=10)
            arm.close()
            planner_server.destroy()
            planner.destroy_node()
            client.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
