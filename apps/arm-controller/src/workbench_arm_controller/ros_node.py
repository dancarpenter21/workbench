"""ROS owner for guarded commissioning and planned retrieval admission.

The existing domain handlers and monitoring worker run in one asyncio loop, with
no HTTP listener. Retrieval uses the separate MoveIt action and an exclusive lease.
"""

import asyncio
import concurrent.futures
import importlib
import threading
import time
from uuid import UUID

import rclpy
from action_msgs.msg import GoalStatus
from fastapi import HTTPException
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn
from std_srvs.srv import Trigger
from workbench_common import Store, settings
from workbench_contracts import Recovery
from workbench_contracts.ros_control import CONTROL_BINDINGS
from workbench_contracts.ros_transport import decode, encode, field_type, ros_field, to_ros
from workbench_interfaces.action import RetrieveTool
from workbench_interfaces.msg import ArmReadiness, ArmTelemetry, ClawCurrent
from workbench_interfaces.srv import Recover

from .app import create_app


class ArmNode(LifecycleNode):
    def __init__(self):
        super().__init__("workbench_arm_owner")
        self.cfg = settings()
        self.domain = "simulated" if self.cfg["mode"] == "mock" else "physical"
        if self.cfg["mode"] == "hardware" and self.cfg["arm"].get("backend") != "pca9685":
            raise ValueError("Native ROS hardware commissioning requires the PCA9685 backend")
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="arm-monitor-loop")
        self.guard = threading.RLock()
        self.active = False
        self.pending_writes = 0
        self.reservation = None
        self.recovering = False
        self.stop_revision = 0
        self.child = None
        self.store = None
        self.app = None
        self.lifespan = None
        self.publisher_task = None
        self.control_error = ""
        self.snapshot = None
        self.snapshot_at = 0.0
        self.planner = None
        self.planner_at = 0.0
        self.stop_requested = threading.Event()
        self.group = ReentrantCallbackGroup()
        self.control_services = []
        self.telemetry = self.create_publisher(ArmTelemetry, "/workbench/arm/telemetry", 1)
        self.current = self.create_publisher(ClawCurrent, "/workbench/claw_current", 1)
        self.readiness = self.create_publisher(ArmReadiness, "/workbench/arm/readiness", 1)
        self.plan_client = ActionClient(
            self, RetrieveTool, "/workbench/planning/retrieve", callback_group=self.group
        )
        self.plan_stop = self.create_client(
            Trigger, "/workbench/planning/stop", callback_group=self.group
        )
        self.plan_recover = self.create_client(
            Recover, "/workbench/planning/recover", callback_group=self.group
        )
        self.create_subscription(
            ArmReadiness, "/workbench/planning/readiness", self.receive_planner, 1
        )
        self.server = ActionServer(
            self,
            RetrieveTool,
            "/workbench/arm/retrieve",
            execute_callback=self.retrieve,
            goal_callback=self.accept,
            cancel_callback=self.cancel,
            callback_group=self.group,
        )
        self.create_service(Trigger, "/workbench/arm/stop", self.stop, callback_group=self.group)
        self.create_service(
            Recover, "/workbench/arm/recover", self.recover, callback_group=self.group
        )
        self.create_timer(0.1, self.publish_readiness)
        self.thread.start()

    def run_async(self, coroutine, timeout=15):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError("Arm operation timed out; inspect state before retrying") from None

    def on_configure(self, state):
        try:
            self.store = Store("arm-ros")
            if self.store.get("active_operation"):
                self.store.put("recovery", True)
                self.store.put("active_operation", None)
            self.app = create_app(self.cfg)
            self.handlers = {
                route.endpoint.__name__: route.endpoint
                for route in self.app.routes
                if hasattr(route, "endpoint")
            }
            module = importlib.import_module("workbench_interfaces.srv")
            for binding in CONTROL_BINDINGS:
                self.control_services.append(
                    self.create_service(
                        getattr(module, binding["name"]),
                        "/workbench/arm/control/" + binding["endpoint"],
                        self.control_callback(binding),
                        callback_group=self.group,
                    )
                )
            return TransitionCallbackReturn.SUCCESS
        except Exception as exc:
            self.control_error = str(exc)
            self.get_logger().error(str(exc))
            return TransitionCallbackReturn.FAILURE

    async def refresh(self):
        values = {}
        for key, endpoint in (
            ("hardware", "hardware_status"),
            ("manual", "manual_status"),
            ("commissioning", "commissioning_status"),
            ("arm", "status"),
        ):
            values[key] = await self.handlers[endpoint]()
        with self.guard:
            self.snapshot = values
            self.snapshot_at = time.monotonic()
        return values

    async def start(self):
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        await self.refresh()
        self.active = True
        self.control_error = ""
        self.publisher_task = asyncio.create_task(self.publish_telemetry())

    def on_activate(self, state):
        try:
            self.run_async(self.start())
            return TransitionCallbackReturn.SUCCESS
        except Exception as exc:
            self.control_error = str(exc)
            self.get_logger().error(str(exc))
            return TransitionCallbackReturn.FAILURE

    async def finish(self):
        # Let admitted calls finish while the established monitoring worker remains alive.
        while self.pending_writes or self.reservation:
            await asyncio.sleep(0.02)
        if self.publisher_task is not None:
            self.publisher_task.cancel()
            await asyncio.gather(self.publisher_task, return_exceptions=True)
            self.publisher_task = None
        if self.lifespan is not None:
            await self.lifespan.__aexit__(None, None, None)
            self.lifespan = None

    def on_deactivate(self, state):
        self.active = False
        if self.reservation or self.cfg["mode"] == "hardware":
            self.halt()
        try:
            self.run_async(self.finish(), timeout=30)
            return TransitionCallbackReturn.SUCCESS
        except Exception as exc:
            self.control_error = str(exc)
            self.get_logger().error("Shutdown unconfirmed; cut physical servo power: " + str(exc))
            return TransitionCallbackReturn.FAILURE

    def on_cleanup(self, state):
        for service in self.control_services:
            self.destroy_service(service)
        self.control_services.clear()
        if self.store is not None:
            self.store.close()
            self.store = None
        self.app = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state):
        result = self.on_deactivate(state)
        if result == TransitionCallbackReturn.SUCCESS:
            self.on_cleanup(state)
        return result

    def control_callback(self, binding):
        def handle(request, response):
            response.domain = self.domain
            try:
                if request.domain != self.domain:
                    raise HTTPException(409, "Control evidence domain mismatch")
                arguments = {
                    name: decode(
                        getattr(request, ros_field(name, field_type(annotation))),
                        field_type(annotation),
                    )
                    for name, annotation in binding["parameters"].items()
                }
                value = self.run_async(self.control(binding, arguments))
                response.value = encode(value, field_type(binding["result"]))
                response.success = True
            except HTTPException as exc:
                response.error_status = exc.status_code
                response.error_message = (
                    exc.detail.get("message", str(exc.detail))
                    if isinstance(exc.detail, dict)
                    else str(exc.detail)
                )
            except ValueError as exc:
                response.error_status, response.error_message = 422, str(exc)
            except Exception as exc:
                response.error_status, response.error_message = 503, str(exc)
            return response

        return handle

    async def control(self, binding, arguments):
        writing = binding["method"] != "GET"
        with self.guard:
            if not self.active:
                raise HTTPException(409, "Arm lifecycle is not active")
            if (
                writing
                and self.app.state.harness.active
                and not binding["endpoint"].startswith("harness_")
                and binding["endpoint"] != "stop"
            ):
                raise HTTPException(409, "Harness owns the arm")
            if writing and self.reservation and binding["endpoint"] != "stop":
                raise HTTPException(409, "Planned retrieval owns the arm")
            if writing and self.recovering and binding["endpoint"] != "stop":
                raise HTTPException(409, "Recovery owns the arm")
            if writing and self.control_error and binding["endpoint"] != "stop":
                raise HTTPException(503, self.control_error)
            if writing:
                self.pending_writes += 1
        try:
            if binding["endpoint"] == "stop":
                self.halt()
            worker = getattr(self.app.state, "monitor_worker", None)
            if writing and worker is not None and worker.done() and binding["endpoint"] != "stop":
                raise HTTPException(
                    503, "Continuous claw monitoring stopped; cut physical servo power"
                )
            value = await self.handlers[binding["endpoint"]](**arguments)
            await self.refresh()
            if self.reservation and binding["endpoint"] in (
                "manual_status",
                "commissioning_status",
            ):
                value = value.model_copy(
                    update={"available": False, "message": "Planned retrieval owns the arm"}
                )
            return value
        finally:
            if writing:
                with self.guard:
                    self.pending_writes -= 1

    async def publish_telemetry(self):
        try:
            while self.active:
                worker = getattr(self.app.state, "monitor_worker", None)
                if worker is not None and worker.done():
                    self.control_error = (
                        "Continuous claw monitoring stopped; cut physical servo power"
                    )
                    await self.app.state.emergency_shutdown(self.control_error)
                    break
                data = await self.refresh()
                message = ArmTelemetry(domain=self.domain, available=True, error=self.control_error)
                message.header.stamp = self.get_clock().now().to_msg()
                for field, value in data.items():
                    if self.reservation and field in ("manual", "commissioning"):
                        value = value.model_copy(
                            update={"available": False, "message": "Planned retrieval owns the arm"}
                        )
                    setattr(message, field, to_ros(value))
                self.telemetry.publish(message)
                sample = data["hardware"].claw_current
                raw = ClawCurrent(
                    domain=self.domain,
                    scale_verified=sample.scale_verified,
                    valid=bool(
                        sample.reading_healthy
                        and sample.age_seconds is not None
                        and sample.age_seconds < 0.3
                    ),
                    bus_voltage_v=sample.bus_v if sample.bus_v is not None else float("nan"),
                    shunt_voltage_mv=sample.shunt_mv
                    if sample.shunt_mv is not None
                    else float("nan"),
                    current_a=sample.current_a
                    if sample.scale_verified and sample.current_a is not None
                    else float("nan"),
                    verification_evidence=sample.verification_evidence,
                    fault_latched=sample.reset_required,
                    fault=sample.fault,
                )
                if sample.sampled_at is not None:
                    raw.header.stamp = encode(
                        sample.sampled_at, field_type(type(sample.sampled_at))
                    )
                self.current.publish(raw)
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.context.ok():
                return
            self.control_error = str(exc)
            self.get_logger().error("Arm telemetry failed: " + str(exc))

    def receive_planner(self, message):
        if message.domain == self.domain:
            self.planner, self.planner_at = message, time.monotonic()

    def eligible(self):
        data, planner = self.snapshot, self.planner
        if (
            not self.active
            or self.control_error
            or data is None
            or planner is None
            or time.monotonic() - self.snapshot_at > 0.5
        ):
            return False
        age = time.time() - planner.header.stamp.sec - planner.header.stamp.nanosec / 1e9
        return bool(
            self.cfg["mode"] == "mock"
            and not self.reservation
            and not self.app.state.harness.active
            and not self.pending_writes
            and not self.store.get("recovery", False)
            and not data["manual"].active
            and not data["manual"].recovery_required
            and not data["commissioning"].reserved
            and not data["arm"].recovery_required
            and planner.ready
            and not planner.recovery_required
            and 0 <= age <= 1
            and time.monotonic() - self.planner_at <= 1
        )

    def publish_readiness(self):
        message = ArmReadiness(domain=self.domain)
        message.header.stamp = self.get_clock().now().to_msg()
        with self.guard:
            message.ready = self.eligible()
            message.recovery_required = bool(
                self.control_error
                or self.store
                and self.store.get("recovery", False)
                or self.planner
                and self.planner.recovery_required
            )
            if self.snapshot is not None:
                message.manual_active = self.snapshot["manual"].active
                message.commissioning_reserved = self.snapshot["commissioning"].reserved
                message.recovery_required |= self.snapshot["arm"].recovery_required
            message.message = self.control_error or (
                "Physical retrieval awaits commissioning"
                if self.cfg["mode"] == "hardware"
                else "Planned retrieval active"
                if self.reservation
                else "ROS arm admission ready"
                if message.ready
                else "Waiting for planning and supervised control readiness"
            )
        self.readiness.publish(message)

    def accept(self, request):
        try:
            operation_id = str(UUID(request.operation_id))
        except ValueError:
            return GoalResponse.REJECT
        with self.guard:
            if not self.eligible() or request.tool_id not in {
                t["id"] for t in self.cfg["workbench"]["tools"]
            }:
                return GoalResponse.REJECT
            if self.store.get("operation:" + operation_id):
                return GoalResponse.REJECT
            try:
                self.store.put_many(
                    {
                        "active_operation": operation_id,
                        "operation:" + operation_id: {
                            "tool_id": request.tool_id,
                            "state": "accepted",
                        },
                    }
                )
            except Exception as exc:
                self.control_error = "Arm journal unavailable: " + str(exc)
                return GoalResponse.REJECT
            self.reservation = operation_id
            self.stop_requested.clear()
        return GoalResponse.ACCEPT

    def wait(self, future, timeout=5, goal=None):
        deadline = time.monotonic() + timeout
        while True:
            if goal is not None and (
                self.stop_requested.is_set() or goal.is_cancel_requested or not self.active
            ):
                raise RuntimeError("Planned retrieval cancelled")
            if future.done():
                return future.result()
            if time.monotonic() >= deadline:
                raise TimeoutError("Planning action timed out")
            time.sleep(0.02)

    def halt(self):
        with self.guard:
            self.stop_requested.set()
            self.stop_revision += 1
            if self.store is not None:
                try:
                    self.store.put("recovery", True)
                except Exception as exc:
                    self.control_error = "Arm journal failed: " + str(exc)
        # ROS may already be down during process shutdown. Local monitored output
        # cleanup must still run even when remote preemption cannot be requested.
        if self.context.ok():
            try:
                child = self.child
                if child is not None:
                    child.cancel_goal_async()
                if self.plan_stop.service_is_ready():
                    self.plan_stop.call_async(Trigger.Request())
            except Exception as exc:
                self.control_error = "Planning preemption unconfirmed: " + str(exc)

    def cancel(self, goal):
        self.halt()
        return CancelResponse.ACCEPT

    def retrieve(self, goal):
        operation_id = str(UUID(goal.request.operation_id))
        result = RetrieveTool.Result()
        result.evidence.domain, result.evidence.operation_id = self.domain, operation_id
        pending = None
        try:
            if not self.plan_client.server_is_ready():
                raise RuntimeError("MoveIt planning executor unavailable")
            pending = self.plan_client.send_goal_async(
                RetrieveTool.Goal(operation_id=operation_id, tool_id=goal.request.tool_id)
            )
            self.child = self.wait(pending, goal=goal)
            if not self.child.accepted:
                self.child = None
                raise RuntimeError("MoveIt executor rejected retrieval")
            outcome = self.wait(
                self.child.get_result_async(), self.cfg["motion_timeout_seconds"], goal
            )
            if outcome.status != GoalStatus.STATUS_SUCCEEDED or not outcome.result.success:
                raise RuntimeError(outcome.result.message or "MoveIt execution failed")
            if (
                outcome.result.evidence.domain != self.domain
                or outcome.result.evidence.operation_id != operation_id
            ):
                raise RuntimeError("Execution evidence identity or domain mismatch")
            result = outcome.result
            self.store.put_many(
                {
                    "active_operation": None,
                    "operation:" + operation_id: {
                        "tool_id": goal.request.tool_id,
                        "state": "completed",
                    },
                }
            )
            goal.succeed()
        except Exception as exc:
            self.halt()
            if pending is not None and self.child is None:

                def cancel_late(future):
                    try:
                        child = future.result()
                        if child.accepted:
                            child.cancel_goal_async()
                    except Exception:
                        pass

                pending.add_done_callback(cancel_late)
            result.message = str(exc)
            result.evidence.recovery_required = True
            self.store.put_many(
                {
                    "active_operation": None,
                    "operation:" + operation_id: {
                        "tool_id": goal.request.tool_id,
                        "state": "failed",
                        "error": str(exc),
                    },
                }
            )
            goal.canceled() if goal.is_cancel_requested else goal.abort()
        finally:
            with self.guard:
                self.reservation = None
                self.child = None
        return result

    def stop(self, request, response):
        self.halt()
        try:
            if self.active:
                status = self.run_async(self.handlers["stop"]())
                response.message = status.message
            else:
                response.message = "Arm controller inactive; physical stop unconfirmed"
            response.success = self.cfg["mode"] == "mock"
            if self.cfg["mode"] == "hardware":
                response.message += (
                    " Physical stop is unconfirmed; use the servo power cutoff if needed."
                )
        except Exception as exc:
            response.message = "Stop unconfirmed; cut physical servo power: " + str(exc)
        return response

    def recover(self, request, response):
        with self.guard:
            if (
                not self.active
                or self.reservation
                or self.pending_writes
                or self.recovering
                or not request.acknowledged
                or not request.inspection_evidence.strip()
            ):
                response.message = "Recovery requires idle control and acknowledged inspection"
                return response
            self.pending_writes += 1
            self.recovering = True
            revision = self.stop_revision
        try:
            self.run_async(self.handlers["recover"](Recovery(acknowledged=True)))
            if self.cfg["mode"] == "mock":
                if not self.plan_recover.service_is_ready():
                    raise RuntimeError("Planning recovery service unavailable")
                planning = self.wait(self.plan_recover.call_async(request))
                if not planning.recovered:
                    raise RuntimeError(planning.message)
            with self.guard:
                if revision != self.stop_revision:
                    raise RuntimeError("A new stop occurred during recovery")
                self.store.put_many({"recovery": False, "active_operation": None})
                self.stop_requested.clear()
            self.run_async(self.refresh())
            response.recovered = True
            response.message = (
                "Operator recovery acknowledged; enable the next control mode explicitly"
            )
        except Exception as exc:
            response.message = str(exc)
        finally:
            with self.guard:
                self.pending_writes -= 1
                self.recovering = False
        return response

    def close(self):
        self.active = False
        if self.reservation or self.cfg["mode"] == "hardware":
            self.halt()
        if self.lifespan is not None:
            self.run_async(self.finish(), timeout=30)
        self.server.destroy()
        if self.store is not None:
            self.store.close()
            self.store = None
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()
        self.destroy_node()


def main():
    rclpy.init()
    node = ArmNode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop_requested.set()
        executor.shutdown()
        node.close()
        if rclpy.ok():
            rclpy.shutdown()
