"""Browser transport adapter: ROS owns workflow state and hardware access."""

import asyncio
import threading
import time
from datetime import datetime, timezone

import rclpy
from action_msgs.msg import GoalStatus
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from rclpy.action import ActionClient
from rclpy.context import Context
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_srvs.srv import Trigger
from workbench_contracts import (
    Diagnostic,
    Health,
    MockScene,
    Observation,
    Operation,
    WorkbenchStatus,
)
from workbench_contracts.ros_transport import (
    RemoteControlError,
    decode,
    encode,
    field_type,
    from_ros,
    ros_field,
    to_ros,
)
from workbench_interfaces.action import RequestTool, TestMicrophone, Transcribe
from workbench_interfaces.msg import ArmReadiness, ArmTelemetry, WorkbenchState
from workbench_interfaces.msg import Observation as RosObservation
from workbench_interfaces.msg import Observations as RosObservations
from workbench_interfaces.srv import GetOperation, ListMicrophones, Recover, SetMockScene


async def ros_future(future, timeout=5):
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def finish(value, error):
        if not result.done():
            result.set_exception(error) if error is not None else result.set_result(value)

    def done(completed):
        try:
            value, error = completed.result(), None
        except Exception as exc:
            value, error = None, exc
        if not loop.is_closed():
            loop.call_soon_threadsafe(finish, value, error)

    future.add_done_callback(done)
    return await asyncio.wait_for(result, timeout)


def operation_from_ros(message):
    if not message.operation_id:
        return None
    return Operation(
        id=message.operation_id,
        text=message.text,
        state=message.state,
        tool_id=message.tool_id or None,
        message=message.message,
        updated_at=datetime.fromtimestamp(
            message.header.stamp.sec + message.header.stamp.nanosec / 1e9,
            timezone.utc,
        ),
    )


class RosBrowserBridge:
    def __init__(self, config):
        self.cfg = config
        self.domain = "simulated" if config["mode"] == "mock" else "physical"
        self.context = Context()
        rclpy.init(context=self.context)
        self.node = Node("browser_gateway", context=self.context)
        self.lock = threading.Lock()
        self.workbench = None
        self.arm = None
        self.observations = None
        self.frame = None
        self.control_state = None
        self.control_clients = {}
        self.checks = {}
        self.request = ActionClient(self.node, RequestTool, "/workbench/requests")
        self.transcription = ActionClient(self.node, Transcribe, "/workbench/voice/transcribe")
        self.mic_test = ActionClient(self.node, TestMicrophone, "/workbench/voice/test_microphone")
        self.mic_list = self.node.create_client(ListMicrophones, "/workbench/voice/microphones")
        self.stop_client = self.node.create_client(Trigger, "/workbench/stop")
        self.recover_client = self.node.create_client(Recover, "/workbench/recover")
        self.lookup_client = self.node.create_client(GetOperation, "/workbench/operations/get")
        self.scene_client = self.node.create_client(SetMockScene, "/workbench/mock/scene")
        self.node.create_subscription(WorkbenchState, "/workbench/status", self.receive_status, 1)
        self.node.create_subscription(ArmReadiness, "/workbench/arm/readiness", self.receive_arm, 1)
        self.node.create_subscription(
            ArmTelemetry, "/workbench/arm/telemetry", self.receive_control, 1
        )
        self.node.create_subscription(
            RosObservations, "/workbench/observations", self.receive_observations, 1
        )
        self.node.create_subscription(
            Image, "/camera/image_raw", self.receive_image, qos_profile_sensor_data
        )
        self.node.create_subscription(DiagnosticArray, "/diagnostics", self.receive_diagnostics, 10)
        self.executor = MultiThreadedExecutor(num_threads=2, context=self.context)
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, name="ros-browser-gateway")
        self.thread.start()

    @staticmethod
    def fresh(message, received_at, maximum=1):
        age = time.time() - message.header.stamp.sec - message.header.stamp.nanosec / 1e9
        return 0 <= age <= maximum and time.monotonic() - received_at <= maximum

    def receive_status(self, message):
        if message.domain == self.domain:
            with self.lock:
                self.workbench = (message, time.monotonic())

    def receive_arm(self, message):
        if message.domain == self.domain:
            with self.lock:
                self.arm = (message, time.monotonic())

    def receive_observations(self, message):
        if message.domain == self.domain:
            with self.lock:
                self.observations = (message, time.monotonic())

    def receive_image(self, message):
        import numpy as np

        if (
            message.encoding != "bgr8"
            or not 0 < message.width <= 4096
            or not 0 < message.height <= 4096
            or message.step < message.width * 3
            or len(message.data) != message.height * message.step
        ):
            return
        stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
        if not 0 <= time.time() - stamp <= self.cfg["observation_max_age_seconds"]:
            return
        pixels = np.frombuffer(bytes(message.data), dtype=np.uint8).reshape(
            message.height, message.step
        )
        pixels = pixels[:, : message.width * 3].reshape(message.height, message.width, 3).copy()
        with self.lock:
            self.frame = (stamp, pixels, datetime.fromtimestamp(stamp, timezone.utc), "capture")

    def receive_control(self, message):
        if message.domain != self.domain:
            return
        from workbench_contracts import ArmHardwareStatus, CommissionStatus, ManualStatus

        try:
            hardware = from_ros(message.hardware, ArmHardwareStatus)
            manual = from_ros(message.manual, ManualStatus)
            commissioning = from_ros(message.commissioning, CommissionStatus)
            if hardware.mode != self.cfg["mode"] or hardware.claw_current.simulated != (
                self.cfg["mode"] == "mock"
            ):
                return
            with self.lock:
                self.control_state = (hardware, manual, commissioning, time.monotonic())
        except ValueError as exc:
            self.node.get_logger().error("Invalid arm telemetry: " + str(exc))

    def receive_diagnostics(self, message):
        states = {
            DiagnosticStatus.OK: "ok",
            DiagnosticStatus.WARN: "warning",
            DiagnosticStatus.ERROR: "error",
            DiagnosticStatus.STALE: "unavailable",
        }
        with self.lock:
            for status in message.status:
                values = {value.key: value.value for value in status.values}
                if values.get("mode") != self.cfg["mode"]:
                    continue
                name = status.name.removeprefix("workbench/")
                self.checks[name] = (
                    Diagnostic(
                        component=name,
                        status=states.get(status.level, "unavailable"),
                        summary=status.message,
                        measurements={**values, "simulated": self.cfg["mode"] == "mock"},
                    ),
                    time.monotonic(),
                )

    def latest(self):
        with self.lock:
            sample = self.frame
        if sample and 0 <= time.time() - sample[0] <= self.cfg["observation_max_age_seconds"]:
            return sample
        return None

    def scene(self):
        with self.lock:
            sample = self.observations
        if sample is None:
            return MockScene(tools=[], camera_ok=False, tray_clear=False)
        message = sample[0]
        return MockScene(
            tools=[
                Observation(tool_id=t.tool_id, location=t.location, confidence=t.confidence)
                for t in message.tools
            ],
            camera_ok=message.camera_ok,
            tray_clear=message.tray_clear,
        )

    def status(self):
        with self.lock:
            value = self.workbench
            control = self.control_state
        if value is None or not self.fresh(*value):
            raise RuntimeError("ROS coordinator status is unavailable or stale")
        message = value[0]
        hardware = manual = commissioning = None
        if control is not None and time.monotonic() - control[3] <= 1:
            hardware, manual, commissioning, received = control
            elapsed = time.monotonic() - received
            current = hardware.claw_current
            if current.age_seconds is not None:
                age = current.age_seconds + elapsed
                current = current.model_copy(
                    update={
                        "age_seconds": age,
                        "reading_healthy": bool(current.reading_healthy and age < 0.3),
                        "healthy": current.healthy and age < 0.3,
                        "reading_state": "stale" if age >= 0.3 else current.reading_state,
                    }
                )
            hat = hardware.servo_hat
            if hat.age_seconds is not None:
                hat = hat.model_copy(update={"age_seconds": hat.age_seconds + elapsed})
            hardware = hardware.model_copy(update={"claw_current": current, "servo_hat": hat})
            manual = manual.model_copy(update={"claw_current": current, "servo_hat": hat})
            commissioning = commissioning.model_copy(update={"claw_current": current})
        return WorkbenchStatus(
            hardware=hardware,
            manual=manual,
            commissioning=commissioning,
            claw_current=hardware.claw_current if hardware else None,
            servo_hat=hardware.servo_hat if hardware else None,
            neutral_evidence=hardware.neutral_evidence if hardware else None,
            mode=self.cfg["mode"],
            operation=operation_from_ros(message.operation),
            recovery_required=message.recovery_required,
            retrieval_enabled=message.retrieval_enabled,
            message=message.message,
            commissioning_ready=self.cfg["mode"] == "mock",
        )

    def services(self):
        try:
            self.status()
            coordinator = Health(service="coordinator", mode=self.cfg["mode"], ready=True)
        except RuntimeError as exc:
            coordinator = Health(
                service="coordinator", mode=self.cfg["mode"], ready=False, detail=str(exc)
            )
        with self.lock:
            arm, observations, checks = self.arm, self.observations, dict(self.checks)
        result = [coordinator]
        for name in ("assistant", "voice"):
            check = checks.get(name)
            fresh = check is not None and time.monotonic() - check[1] <= 3
            result.append(
                Health(
                    service=name,
                    mode=self.cfg["mode"],
                    ready=bool(fresh and check[0].status == "ok"),
                    detail=check[0].summary if fresh else "ROS diagnostics unavailable",
                )
            )
        for name, sample, ready in (
            ("arm-controller", arm, bool(arm and arm[0].ready)),
            ("vision", observations, bool(observations and observations[0].camera_ok)),
        ):
            result.append(
                Health(
                    service=name,
                    mode=self.cfg["mode"],
                    ready=bool(sample and self.fresh(*sample) and ready),
                    detail="" if sample else "ROS topic unavailable",
                )
            )
        return result

    def diagnostics(self):
        with self.lock:
            checks = list(self.checks.values())
        return [
            check
            if time.monotonic() - received <= 3
            else check.model_copy(
                update={
                    "status": "unavailable",
                    "summary": "ROS diagnostics stale",
                }
            )
            for check, received in checks
        ]

    async def call(self, client, request, timeout=5):
        if not client.service_is_ready():
            raise RuntimeError("Required ROS service is unavailable")
        return await ros_future(client.call_async(request), timeout)

    async def control(self, binding, arguments):
        import importlib

        name = binding["name"]
        if name not in self.control_clients:
            service = getattr(importlib.import_module("workbench_interfaces.srv"), name)
            client = self.node.create_client(
                service, "/workbench/arm/control/" + binding["endpoint"]
            )
            self.control_clients[name] = (service, client)
        service, client = self.control_clients[name]
        request = service.Request(domain=self.domain)
        for parameter, annotation in binding["parameters"].items():
            spec = field_type(annotation)
            setattr(request, ros_field(parameter, spec), encode(arguments[parameter], spec))
        response = await self.call(client, request, timeout=15)
        if response.domain != self.domain:
            raise RuntimeError("Control response evidence domain mismatch")
        if not response.success:
            raise RemoteControlError(response.error_status or 503, response.error_message)
        return decode(response.value, field_type(binding["result"]))

    async def send(self, client, goal):
        if not client.server_is_ready():
            raise RuntimeError("Required ROS action server is unavailable")
        pending = client.send_goal_async(goal)
        try:
            handle = await ros_future(pending)
        except BaseException:

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
            raise ValueError(
                "ROS action rejected the request; check readiness and active operations"
            )
        return handle

    async def submit(self, request):
        handle = await self.send(
            self.request,
            RequestTool.Goal(
                operation_id=str(request.operation_id),
                text=request.text,
            ),
        )
        handle.get_result_async()  # Retain the result subscription independently of HTTP lifetime.
        result = await self.lookup(str(request.operation_id))
        if result is None:
            raise RuntimeError(
                f"Request {request.operation_id} accepted; status unavailable. Check status before retrying."
            )
        return result

    async def lookup(self, operation_id):
        result = await self.call(
            self.lookup_client, GetOperation.Request(operation_id=operation_id)
        )
        return operation_from_ros(result.operation) if result.found else None

    async def transcribe(self, data):
        handle = await self.send(self.transcription, Transcribe.Goal(wav=list(data)))
        try:
            result = await ros_future(handle.get_result_async(), timeout=120)
        except BaseException:
            handle.cancel_goal_async()
            raise
        if result.status != GoalStatus.STATUS_SUCCEEDED or not result.result.success:
            raise RuntimeError(result.result.error or "ROS transcription failed")
        return result.result.text

    async def microphones(self):
        from workbench_contracts import MicrophoneDevice

        response = await self.call(self.mic_list, ListMicrophones.Request(domain=self.domain))
        if not response.success or response.domain != self.domain:
            raise RuntimeError(response.error_message or "Microphone service domain mismatch")
        return [from_ros(device, MicrophoneDevice) for device in response.devices]

    async def test_microphone(self, value):
        from workbench_contracts import MicrophoneResult

        handle = await self.send(
            self.mic_test, TestMicrophone.Goal(domain=self.domain, value=to_ros(value))
        )
        try:
            outcome = await ros_future(handle.get_result_async(), timeout=10)
        except BaseException:
            handle.cancel_goal_async()
            raise
        result = outcome.result
        if result.domain != self.domain:
            raise RuntimeError("Microphone result domain mismatch")
        if outcome.status != GoalStatus.STATUS_SUCCEEDED or not result.success:
            raise RemoteControlError(result.error_status or 503, result.error_message)
        return from_ros(result.value, MicrophoneResult)

    async def stop(self):
        result = await self.call(self.stop_client, Trigger.Request())
        return self.status().model_copy(
            update={"recovery_required": True, "message": result.message}
        )

    async def recover(self):
        result = await self.call(
            self.recover_client,
            Recover.Request(
                acknowledged=True,
                inspection_evidence="Browser operator recovery acknowledgement",
            ),
        )
        if not result.recovered:
            raise ValueError(result.message)
        return self.status().model_copy(
            update={"recovery_required": False, "message": result.message}
        )

    async def reset(self):
        if self.cfg["mode"] != "mock":
            raise ValueError("Scene reset is available only in mock mode")
        status = self.status()
        if status.operation and status.operation.state not in {
            "completed",
            "clarification",
            "rejected",
            "failed",
            "stopped",
            "status",
        }:
            raise ValueError("Stop the active operation before resetting the scene")
        request = SetMockScene.Request(
            camera_ok=True,
            tray_clear=True,
            age_seconds=0.0,
            tools=[
                RosObservation(tool_id=t["id"], location="source", confidence=1.0)
                for t in self.cfg["workbench"]["tools"]
            ],
        )
        result = await self.call(self.scene_client, request)
        if not result.accepted:
            raise ValueError(result.message)
        return self.status()

    def close(self):
        self.executor.shutdown()
        self.thread.join()
        self.node.destroy_node()
        rclpy.shutdown(context=self.context)
