import asyncio
import concurrent.futures
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from workbench_assistant.engine import AssistantEngine
from workbench_common import settings
from workbench_contracts import MicrophoneTest, TextRequest
from workbench_contracts.ros_transport import from_ros, to_ros
from workbench_interfaces.action import Interpret, TestMicrophone, Transcribe
from workbench_interfaces.srv import ListMicrophones
from workbench_voice.engine import MAX_BYTES, VoiceEngine
from workbench_voice.microphone import Microphone, devices


class InferenceNode(Node):
    def __init__(self, role):
        super().__init__(role)
        self.role = role
        self.config = settings()
        self.loop = asyncio.new_event_loop()
        self.worker = threading.Thread(target=self.loop.run_forever, name=f"{role}-engine")
        self.worker.start()
        self.reservation = threading.Lock()
        self.busy = False
        self.mic_busy = False
        self.microphone = (
            Microphone(mock=self.config["mode"] == "mock") if role == "voice" else None
        )
        self.mic_server = None
        self.engine = VoiceEngine(self.config) if role == "voice" else AssistantEngine(self.config)
        asyncio.run_coroutine_threadsafe(self.engine.start(), self.loop).result()
        self.action_type = Transcribe if role == "voice" else Interpret
        self.server = ActionServer(
            self,
            self.action_type,
            f"/workbench/{role}/{'transcribe' if role == 'voice' else 'interpret'}",
            execute_callback=self.execute,
            goal_callback=self.accept,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup(),
        )
        self.diagnostics = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.timer = self.create_timer(1.0, self.publish_diagnostics)
        if self.role == "voice":
            self.create_service(
                ListMicrophones,
                "/workbench/voice/microphones",
                self.list_microphones,
                callback_group=ReentrantCallbackGroup(),
            )
            self.mic_server = ActionServer(
                self,
                TestMicrophone,
                "/workbench/voice/test_microphone",
                execute_callback=self.test_microphone,
                goal_callback=self.accept_microphone,
                cancel_callback=lambda _: CancelResponse.ACCEPT,
                callback_group=ReentrantCallbackGroup(),
                result_timeout=10,
            )

    def accept(self, request):
        try:
            if self.role == "voice":
                if not 0 < len(request.wav) <= MAX_BYTES:
                    return GoalResponse.REJECT
            else:
                TextRequest(text=request.text)
        except ValueError:
            return GoalResponse.REJECT
        with self.reservation:
            if self.busy or self.engine.error:
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    def execute(self, goal):
        result = self.action_type.Result()
        future = None
        try:
            feedback = self.action_type.Feedback()
            feedback.stage = "transcribing" if self.role == "voice" else "interpreting"
            goal.publish_feedback(feedback)
            coroutine = (
                self.engine.transcribe(bytes(goal.request.wav))
                if self.role == "voice"
                else self.engine.interpret(goal.request.text)
            )
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
            while True:
                if goal.is_cancel_requested or not rclpy.ok():
                    future.cancel()
                    goal.canceled() if goal.is_cancel_requested else goal.abort()
                    return result
                try:
                    response = future.result(timeout=0.05)
                    break
                except concurrent.futures.TimeoutError:
                    continue
            if goal.is_cancel_requested:
                goal.canceled()
                return result
            if self.role == "voice":
                result.success, result.text = True, response.text
            else:
                result.action = response.action
                result.tool_id = response.tool_id or ""
                result.message = response.message
            goal.succeed()
        except Exception as exc:
            if future is not None:
                future.cancel()
            if self.role == "voice":
                result.error = str(exc)
            else:
                result.action, result.message = "clarify", str(exc)
            goal.abort()
        finally:
            with self.reservation:
                self.busy = False
        return result

    def publish_diagnostics(self):
        message = DiagnosticArray()
        message.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus()
        status.name = f"workbench/{self.role}"
        status.level = DiagnosticStatus.ERROR if self.engine.error else DiagnosticStatus.OK
        status.message = self.engine.error or ("busy" if self.busy else "ready")
        status.values = [KeyValue(key="mode", value=self.config["mode"])]
        message.status = [status]
        self.diagnostics.publish(message)

    def list_microphones(self, request, response):
        response.domain = "simulated" if self.config["mode"] == "mock" else "physical"
        try:
            if request.domain != response.domain:
                raise ValueError("Microphone evidence domain mismatch")
            found = asyncio.run_coroutine_threadsafe(
                devices(self.config["mode"] == "mock"), self.loop
            ).result(5)
            response.devices = [to_ros(device) for device in found]
            response.success = True
        except Exception as exc:
            response.error_message = str(exc)
        return response

    def accept_microphone(self, request):
        domain = "simulated" if self.config["mode"] == "mock" else "physical"
        try:
            if request.domain != domain:
                return GoalResponse.REJECT
            from_ros(request.value, MicrophoneTest)
        except ValueError:
            return GoalResponse.REJECT
        with self.reservation:
            if self.mic_busy:
                return GoalResponse.REJECT
            self.mic_busy = True
        return GoalResponse.ACCEPT

    def test_microphone(self, goal):
        result = TestMicrophone.Result(
            domain="simulated" if self.config["mode"] == "mock" else "physical"
        )
        future = None

        class Client:
            async def is_disconnected(self):
                return goal.is_cancel_requested or not rclpy.ok()

        try:
            request = from_ros(goal.request.value, MicrophoneTest)
            goal.publish_feedback(TestMicrophone.Feedback(stage="recording"))
            future = asyncio.run_coroutine_threadsafe(
                self.microphone.test(Client(), request.device_id), self.loop
            )
            while True:
                if goal.is_cancel_requested or not rclpy.ok():
                    future.cancel()
                    goal.canceled() if goal.is_cancel_requested else goal.abort()
                    return result
                try:
                    value = future.result(timeout=0.05)
                    break
                except concurrent.futures.TimeoutError:
                    continue
            result.value, result.success = to_ros(value), True
            goal.succeed()
        except Exception as exc:
            if future is not None:
                future.cancel()
            result.error_message = str(exc)
            result.error_status = getattr(exc, "status_code", 503)
            goal.abort()
        finally:
            with self.reservation:
                self.mic_busy = False
        return result

    def close(self):
        self.server.destroy()
        if self.mic_server is not None:
            self.mic_server.destroy()
            asyncio.run_coroutine_threadsafe(self.microphone.close(), self.loop).result()
        asyncio.run_coroutine_threadsafe(self.engine.close(), self.loop).result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.worker.join()
        self.loop.close()
        self.destroy_node()


def main(role):
    rclpy.init()
    node = InferenceNode(role)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        executor.shutdown()
        node.close()
        if rclpy.ok():
            rclpy.shutdown()
