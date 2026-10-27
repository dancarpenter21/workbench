"""Durable request coordination over ROS actions; hardware retrieval remains gated."""

import threading
import time
from datetime import datetime, timezone
from uuid import UUID

import rclpy
from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.set_message import set_message_fields
from std_srvs.srv import Trigger
from workbench_common import Store, settings
from workbench_contracts import Observation, Observations, Operation, TextRequest, now
from workbench_interfaces.action import Interpret, RequestTool, RetrieveTool
from workbench_interfaces.msg import (
    ArmReadiness,
    ExecutionEvidence,
    OperationStatus,
    WorkbenchState,
)
from workbench_interfaces.msg import Observation as RosObservation
from workbench_interfaces.msg import Observations as RosObservations
from workbench_interfaces.srv import GetOperation, Recover, SetMockScene

from .app import TERMINAL, check_observations


class RequestCancelled(Exception):
    pass


class CoordinatorNode(Node):
    def __init__(self):
        super().__init__("coordinator")
        self.cfg = settings()
        self.domain = "simulated" if self.cfg["mode"] == "mock" else "physical"
        self.store = Store("coordinator")
        self.guard = threading.RLock()
        self.stop_requested = threading.Event()
        self.active_id = None
        self.recovering = False
        self.stop_revision = 0
        self.fault = self.store.get("recovery", False)
        self.active_child = None
        self.latest_observations = None
        self.arm_readiness = None
        self.arm_received_at = 0.0
        previous = self.store.get("current")
        if previous and previous["state"] not in TERMINAL:
            operation = Operation.model_validate(previous)
            operation.state = "failed"
            operation.message = "Coordinator restarted; inspect the workbench and recover"
            self.store.put("recovery", True)
            self.fault = True
            self.save(operation)
        callbacks = ReentrantCallbackGroup()
        self.interpret = ActionClient(
            self, Interpret, "/workbench/assistant/interpret", callback_group=callbacks
        )
        self.retrieve = ActionClient(
            self, RetrieveTool, "/workbench/arm/retrieve", callback_group=callbacks
        )
        self.mock_scene = self.create_client(
            SetMockScene, "/workbench/mock/scene", callback_group=callbacks
        )
        self.arm_stop = self.create_client(Trigger, "/workbench/arm/stop", callback_group=callbacks)
        self.arm_recover = self.create_client(
            Recover, "/workbench/arm/recover", callback_group=callbacks
        )
        self.publisher = self.create_publisher(WorkbenchState, "/workbench/status", 1)
        self.create_subscription(RosObservations, "/workbench/observations", self.observe, 1)
        self.create_subscription(ArmReadiness, "/workbench/arm/readiness", self.readiness, 1)
        self.create_service(Trigger, "/workbench/stop", self.stop, callback_group=callbacks)
        self.create_service(Recover, "/workbench/recover", self.recover, callback_group=callbacks)
        self.create_service(
            GetOperation, "/workbench/operations/get", self.get_operation, callback_group=callbacks
        )
        self.server = ActionServer(
            self,
            RequestTool,
            "/workbench/requests",
            execute_callback=self.execute,
            goal_callback=self.accept,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=callbacks,
        )
        self.create_timer(0.2, self.publish)

    def save(self, operation):
        operation.updated_at = now()
        value = operation.model_dump(mode="json")
        self.store.put_many({f"operation:{operation.id}": value, "current": value})

    def message(self, operation):
        result = OperationStatus()
        if operation:
            value = (
                Operation.model_validate(operation) if isinstance(operation, dict) else operation
            )
            result.operation_id, result.text = str(value.id), value.text
            result.state, result.tool_id, result.message = (
                value.state,
                value.tool_id or "",
                value.message,
            )
            result.header.stamp.sec = int(value.updated_at.timestamp())
            result.header.stamp.nanosec = value.updated_at.microsecond * 1000
            result.evidence = ExecutionEvidence(
                domain=self.domain,
                operation_id=str(value.id),
                position_source="unknown",
            )
            evidence = self.store.get(f"evidence:{value.id}")
            if evidence:
                set_message_fields(result.evidence, evidence)
        return result

    def publish(self):
        message = WorkbenchState()
        message.header.stamp = self.get_clock().now().to_msg()
        message.domain = self.domain
        message.recovery_required = (
            self.fault
            or self.store.get("recovery", False)
            or bool(self.arm_readiness and self.arm_readiness.recovery_required)
        )
        message.retrieval_enabled = (
            self.cfg["mode"] == "mock" and self.arm_ready() and not self.recovering
        )
        message.operation = self.message(self.store.get("current"))
        message.message = self.store.get("stop_message", "") or (
            self.arm_readiness.message
            if self.arm_readiness and self.arm_readiness.recovery_required
            else ""
        )
        self.publisher.publish(message)

    def observe(self, message):
        self.latest_observations = message

    def readiness(self, message):
        if message.domain == self.domain:
            self.arm_readiness = message
            self.arm_received_at = time.monotonic()

    def arm_ready(self):
        status = self.arm_readiness
        age = (
            now().timestamp() - status.header.stamp.sec - status.header.stamp.nanosec / 1e9
            if status
            else float("inf")
        )
        return bool(
            status
            and status.domain == self.domain
            and status.ready
            and not status.recovery_required
            and not status.manual_active
            and not status.commissioning_reserved
            and time.monotonic() - self.arm_received_at <= 1.0
            and 0 <= age <= 1.0
            and not self.fault
            and not self.store.get("recovery", False)
        )

    def observations(self):
        message = self.latest_observations
        if message is None or message.domain != self.domain:
            raise ValueError("No observations in the current evidence domain")
        return Observations(
            captured_at=datetime.fromtimestamp(
                message.header.stamp.sec + message.header.stamp.nanosec / 1e9,
                timezone.utc,
            ),
            camera_ok=message.camera_ok,
            tray_clear=message.tray_clear,
            tools=[
                Observation(tool_id=t.tool_id, location=t.location, confidence=t.confidence)
                for t in message.tools
            ],
        )

    def accept(self, request):
        try:
            validated = TextRequest(text=request.text, operation_id=request.operation_id)
            if not request.text.strip():
                return GoalResponse.REJECT
        except ValueError:
            return GoalResponse.REJECT
        with self.guard:
            operation_id = str(validated.operation_id)
            previous = self.store.get(f"operation:{operation_id}")
            if previous:
                return (
                    GoalResponse.ACCEPT
                    if previous["text"] == request.text and previous["state"] in TERMINAL
                    else GoalResponse.REJECT
                )
            # A ROS transport migration is not evidence qualifying open-loop hardware.
            if (
                self.cfg["mode"] != "mock"
                or self.active_id
                or self.recovering
                or self.fault
                or self.store.get("recovery", False)
            ):
                return GoalResponse.REJECT
            operation = Operation(id=validated.operation_id, text=request.text)
            try:
                self.save(operation)  # Commit before accepting or dispatching any goal.
            except Exception as exc:
                self.fault = True
                self.get_logger().error(f"Request journal unavailable: {exc}")
                return GoalResponse.REJECT
            self.active_id = operation_id
            self.stop_requested.clear()
            return GoalResponse.ACCEPT

    def wait(self, future, *, goal=None, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            if goal is not None and (
                goal.is_cancel_requested or self.stop_requested.is_set() or not rclpy.ok()
            ):
                raise RequestCancelled()
            if future.done():
                return future.result()
            if time.monotonic() >= deadline:
                raise TimeoutError("ROS operation timed out; no automatic retry")
            time.sleep(0.02)

    def action(self, client, request, goal, timeout):
        if goal.is_cancel_requested or self.stop_requested.is_set():
            raise RequestCancelled()
        if not client.server_is_ready():
            raise ValueError("Required action server is unavailable")
        pending = client.send_goal_async(request)
        try:
            handle = self.wait(pending, goal=goal)
        except Exception:
            # If acceptance arrives after cancellation/timeout, cancel that goal as well.
            def cancel_late(future):
                try:
                    late = future.result()
                    if late.accepted:
                        late.cancel_goal_async()
                except Exception:
                    pass

            pending.add_done_callback(cancel_late)
            raise
        if not handle.accepted:
            raise ValueError("Required action server rejected the request")
        self.active_child = handle
        try:
            result = self.wait(handle.get_result_async(), goal=goal, timeout=timeout)
            if result.status != GoalStatus.STATUS_SUCCEEDED:
                raise ValueError("Required action did not complete successfully")
            return result.result
        except Exception:
            handle.cancel_goal_async()
            raise
        finally:
            self.active_child = None

    def execute(self, goal):
        operation = Operation.model_validate(
            self.store.get(f"operation:{UUID(goal.request.operation_id)}")
        )
        result = RequestTool.Result()
        if operation.state in TERMINAL:
            result.success = operation.state == "completed"
            result.status = self.message(operation)
            goal.succeed()
            return result
        dispatched = False

        def update(state, message):
            operation.state, operation.message = state, message
            self.save(operation)
            if goal.status == GoalStatus.STATUS_EXECUTING:
                goal.publish_feedback(RequestTool.Feedback(status=self.message(operation)))

        try:
            intent = self.action(
                self.interpret,
                Interpret.Goal(
                    operation_id=str(operation.id),
                    text=operation.text,
                ),
                goal,
                120,
            )
            if intent.action == "clarify":
                update("clarification", intent.message)
            elif intent.action == "get_status":
                update(
                    "status",
                    "Arm ready" if self.arm_ready() else "Arm unavailable or recovery required",
                )
            else:
                if intent.action != "retrieve_tool" or intent.tool_id not in {
                    t["id"] for t in self.cfg["workbench"]["tools"]
                }:
                    raise ValueError("Assistant returned an invalid tool request")
                operation.tool_id = intent.tool_id
                update("checking", "Checking tool, tray, and arm readiness")
                if not self.arm_ready():
                    raise ValueError("Arm unavailable, reserved, stale, or recovery required")
                check_observations(self.observations(), self.cfg, intent.tool_id, "source")
                update("moving", "Retrieving the tool")
                dispatched = True
                retrieved = self.action(
                    self.retrieve,
                    RetrieveTool.Goal(
                        operation_id=str(operation.id),
                        tool_id=intent.tool_id,
                    ),
                    goal,
                    self.cfg["motion_timeout_seconds"],
                )
                if not retrieved.success or retrieved.evidence.domain != self.domain:
                    raise ValueError(retrieved.message or "Invalid execution evidence")
                if retrieved.evidence.operation_id != str(operation.id):
                    raise ValueError("Execution operation identity changed")
                finished_at = (
                    retrieved.evidence.header.stamp.sec
                    + retrieved.evidence.header.stamp.nanosec / 1e9
                )
                if not 0 <= time.time() - finished_at <= self.cfg["observation_max_age_seconds"]:
                    raise ValueError("Execution completion evidence is stale or future-dated")
                evidence = retrieved.evidence
                self.store.put(f"evidence:{operation.id}", message_to_ordereddict(evidence))
                update("verifying", "Checking source and delivery tray")
                if self.cfg["mode"] == "mock":
                    obs = self.observations()
                    request = SetMockScene.Request(
                        camera_ok=True, tray_clear=False, age_seconds=0.0
                    )
                    request.tools = [
                        RosObservation(
                            tool_id=t.tool_id,
                            location="tray" if t.tool_id == intent.tool_id else t.location,
                            confidence=1.0,
                            pose_valid=False,
                        )
                        for t in obs.tools
                    ]
                    if not self.mock_scene.service_is_ready():
                        raise ValueError("Simulated scene service unavailable")
                    if not self.wait(self.mock_scene.call_async(request), goal=goal).accepted:
                        raise ValueError("Simulated scene update rejected")
                deadline = time.monotonic() + 3
                while True:
                    if goal.is_cancel_requested or self.stop_requested.is_set():
                        raise RequestCancelled()
                    try:
                        observations = self.observations()
                        check_observations(observations, self.cfg, intent.tool_id, "tray")
                        if observations.captured_at.timestamp() < finished_at:
                            raise ValueError("Delivery observation predates execution completion")
                        break
                    except ValueError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                evidence.delivery_observed = True
                self.store.put(f"evidence:{operation.id}", message_to_ordereddict(evidence))
                update("completed", "Tool delivered to the tray")
            goal.succeed()
        except RequestCancelled:
            self.fault = True
            self.store.put("recovery", True)
            update("stopped", "Request stopped; inspect the arm before recovery")
            goal.canceled() if goal.is_cancel_requested else goal.abort()
        except Exception as exc:
            if dispatched:
                self.fault = True
                self.store.put("recovery", True)
                if self.arm_stop.service_is_ready():
                    self.arm_stop.call_async(Trigger.Request())
            update("failed" if dispatched else "rejected", str(exc))
            goal.abort()
        finally:
            with self.guard:
                self.active_id = None
        result.success = operation.state == "completed"
        result.status = self.message(operation)
        return result

    def stop(self, request, response):
        with self.guard:
            self.fault = True
            self.stop_revision += 1
            self.stop_requested.set()
            try:
                self.store.put_many(
                    {"recovery": True, "stop_message": "Stop requested; physical stop unconfirmed"}
                )
            except Exception as exc:
                self.get_logger().error(f"Stop journal unavailable: {exc}")
        child = self.active_child
        if child is not None:
            child.cancel_goal_async()
        response.message = "Stop requested; physical stop unconfirmed"
        if self.arm_stop.service_is_ready():
            try:
                stopped = self.wait(self.arm_stop.call_async(Trigger.Request()), timeout=3)
                response.success, response.message = stopped.success, stopped.message
            except Exception as exc:
                response.message += f": {exc}"
        return response

    def recover(self, request, response):
        with self.guard:
            if (
                self.active_id
                or self.recovering
                or not request.acknowledged
                or not request.inspection_evidence.strip()
            ):
                response.message = "Recovery requires an idle workflow and acknowledged inspection"
                return response
            if not self.arm_recover.service_is_ready():
                response.message = "Arm recovery service unavailable"
                return response
            self.recovering = True
            revision = self.stop_revision
        try:
            recovered = self.wait(self.arm_recover.call_async(request), timeout=3)
            with self.guard:
                if revision != self.stop_revision:
                    response.message = "A new stop occurred during recovery"
                    return response
                if recovered.recovered:
                    self.store.put_many({"recovery": False, "stop_message": ""})
                    self.fault = False
                    self.stop_requested.clear()
                return recovered
        except Exception as exc:
            response.message = str(exc)
            return response
        finally:
            with self.guard:
                self.recovering = False

    def get_operation(self, request, response):
        try:
            operation_id = str(UUID(request.operation_id))
        except ValueError:
            return response
        value = self.store.get(f"operation:{operation_id}")
        response.found = value is not None
        response.operation = self.message(value)
        return response


def main():
    rclpy.init()
    node = CoordinatorNode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop_requested.set()
        executor.shutdown()
        node.server.destroy()
        node.store.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
