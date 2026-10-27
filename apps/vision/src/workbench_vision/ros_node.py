"""Lifecycle-owned camera acquisition and typed observation/image ROS topics."""

from datetime import timedelta

import rclpy
from builtin_interfaces.msg import Time
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, Image
from workbench_common import settings
from workbench_contracts import MockScene, Observation, Observations, now
from workbench_interfaces.msg import Observation as RosObservation
from workbench_interfaces.msg import Observations as RosObservations
from workbench_interfaces.srv import SetMockScene

from .app import Camera


def stamp(value):
    seconds = value.timestamp()
    sec = int(seconds)
    return Time(sec=sec, nanosec=int((seconds - sec) * 1_000_000_000))


class VisionNode(LifecycleNode):
    def __init__(self):
        super().__init__("vision")
        self.cfg = None
        self.camera = None
        self.timer = None
        self.mock_service = None
        self.previous_image = None
        self.scene = None
        self.observations = self.create_lifecycle_publisher(
            RosObservations,
            "/workbench/observations",
            1,
        )
        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.images = self.create_lifecycle_publisher(Image, "/camera/image_raw", sensor_qos)
        self.camera_info = self.create_lifecycle_publisher(
            CameraInfo,
            "/camera/camera_info",
            sensor_qos,
        )

    def on_configure(self, state):
        self.cfg = settings()
        self.scene = MockScene(
            tools=[
                Observation(tool_id=t["id"], location="source", confidence=1)
                for t in self.cfg["workbench"]["tools"]
            ]
        )
        if self.cfg["mode"] == "mock":
            self.mock_service = self.create_service(
                SetMockScene,
                "/workbench/mock/scene",
                self.set_scene,
            )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state):
        if self.cfg["mode"] == "hardware":
            try:
                self.camera = Camera(self.cfg)
            except Exception as exc:
                self.get_logger().error(str(exc))
                return TransitionCallbackReturn.FAILURE
        self.timer = self.create_timer(1 / self.cfg["camera"].get("fps", 30), self.publish)
        return super().on_activate(state)

    def stop_capture(self):
        if self.timer is not None:
            self.destroy_timer(self.timer)
            self.timer = None
        if self.camera is not None:
            self.camera.close()
            self.camera = None
        self.previous_image = None

    def on_deactivate(self, state):
        self.stop_capture()
        return super().on_deactivate(state)

    def on_cleanup(self, state):
        self.stop_capture()
        if self.mock_service is not None:
            self.destroy_service(self.mock_service)
            self.mock_service = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state):
        self.stop_capture()
        return TransitionCallbackReturn.SUCCESS

    def set_scene(self, request, response):
        try:
            known = {tool["id"] for tool in self.cfg["workbench"]["tools"]}
            if {tool.tool_id for tool in request.tools} != known or len(request.tools) != len(
                known
            ):
                raise ValueError("Supply each known tool exactly once")
            self.scene = MockScene(
                tools=[
                    Observation(tool_id=t.tool_id, location=t.location, confidence=t.confidence)
                    for t in request.tools
                ],
                tray_clear=request.tray_clear,
                camera_ok=request.camera_ok,
                age_seconds=request.age_seconds,
            )
            response.accepted = True
        except ValueError as exc:
            response.message = str(exc)
        return response

    def publish(self):
        if self.cfg["mode"] == "mock":
            observations = Observations(
                captured_at=now() - timedelta(seconds=self.scene.age_seconds),
                tools=self.scene.tools,
                tray_clear=self.scene.tray_clear,
                camera_ok=self.scene.camera_ok,
            )
        else:
            with self.camera.lock:
                observations = self.camera.observations.model_copy(deep=True)
            sample = self.camera.latest()
            if sample is not None and sample[0] != self.previous_image:
                self.previous_image, frame, captured_at, _ = sample
                image = Image()
                image.header.stamp = stamp(captured_at)
                image.header.frame_id = "camera_optical_frame"
                image.height, image.width = frame.shape[:2]
                image.encoding = "bgr8"
                image.step = image.width * 3
                image.data = frame.tobytes()
                self.images.publish(image)
                info = CameraInfo()
                info.header = image.header
                info.height, info.width = image.height, image.width
                # Standard ROS uncalibrated-camera convention: K[0] == 0.
                # No camera-to-table transform or metric object pose is fabricated.
                self.camera_info.publish(info)
        message = RosObservations()
        message.header.stamp = stamp(observations.captured_at)
        message.header.frame_id = "camera_optical_frame"
        message.domain = "simulated" if self.cfg["mode"] == "mock" else "physical"
        message.camera_ok = observations.camera_ok
        message.tray_clear = observations.tray_clear
        message.tools = [
            RosObservation(
                tool_id=t.tool_id,
                location=t.location,
                confidence=t.confidence,
                pose_valid=False,
            )
            for t in observations.tools
        ]
        self.observations.publish(message)


def main():
    rclpy.init()
    node = VisionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop_capture()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
