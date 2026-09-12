"""Recorded joint paths for RoArm-M2-S. No arbitrary motion API is exposed."""

import json
import math
import threading
import time

JOINTS = {"base": "b", "shoulder": "s", "elbow": "e", "hand": "t"}


def validate_calibration(config):
    arm = config["workbench"]["arm"]
    if not arm["calibrated"]:
        raise ValueError("Hardware motion disabled: calibrate and review paths first")
    if not 1 <= arm["speed"] <= 300 or not 1 <= arm["acceleration"] <= 20:
        raise ValueError(
            "Speed must be 1–300 steps/s and acceleration 1–20; zero means maximum speed"
        )
    if not 0 < arm["tolerance_radians"] <= 0.1 or not 0 < arm["waypoint_timeout_seconds"] <= 30:
        raise ValueError("Invalid feedback tolerance or waypoint timeout")
    for joint in JOINTS:
        bounds = arm["joint_limits"][joint]
        if len(bounds) != 2 or not all(math.isfinite(v) for v in bounds) or bounds[0] >= bounds[1]:
            raise ValueError(f"Invalid limits for {joint}")
    for tool in config["workbench"]["tools"]:
        path = arm["sequences"].get(tool["id"], [])
        if len(path) < 3:
            raise ValueError(f"Missing reviewed pickup/delivery/home path for {tool['id']}")
        for pose in path:
            if set(pose) != set(JOINTS):
                raise ValueError("Each pose must contain base, shoulder, elbow and hand radians")
            for joint, value in pose.items():
                lo, hi = arm["joint_limits"][joint]
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError(f"Pose outside calibrated {joint} limit")
        if path[0] != path[-1]:
            raise ValueError("Every path must start and finish at the same reviewed home pose")


class SerialArm:
    def __init__(self, config):
        validate_calibration(config)
        import serial

        self.config = config["workbench"]["arm"]
        self.serial = serial.Serial(
            config["arm"]["port"], config["arm"]["baudrate"], timeout=0.1, write_timeout=0.5
        )
        self.lock = threading.Lock()
        self.interrupted = threading.Event()

    def send(self, command):
        self.serial.write((json.dumps(command, allow_nan=False) + "\n").encode())

    def feedback(self):
        with self.lock:
            self.serial.reset_input_buffer()
            self.send({"T": 105})
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    data = json.loads(self.serial.readline())
                except (ValueError, UnicodeError):
                    continue
                if data.get("T") == 1051 and all(key in data for key in JOINTS.values()):
                    pose = {joint: float(data[key]) for joint, key in JOINTS.items()}
                    if all(math.isfinite(v) for v in pose.values()):
                        return pose
            raise TimeoutError("No fresh joint feedback; arm position is unknown")

    def at_pose(self, current, goal):
        return all(abs(current[j] - goal[j]) <= self.config["tolerance_radians"] for j in JOINTS)

    def execute(self, tool_id):
        path = self.config["sequences"][tool_id]
        if not self.at_pose(self.feedback(), path[0]):
            raise ValueError("Arm is not at the calibrated starting pose; recover manually")
        for pose in path[1:]:
            with self.lock:
                if self.interrupted.is_set():
                    raise InterruptedError("Motion interrupted")
                self.send(
                    {
                        "T": 102,
                        **pose,
                        "spd": self.config["speed"],
                        "acc": self.config["acceleration"],
                    }
                )
            deadline = time.monotonic() + self.config["waypoint_timeout_seconds"]
            settled = 0
            while time.monotonic() < deadline:
                if self.interrupted.is_set():
                    raise InterruptedError("Motion interrupted")
                settled = settled + 1 if self.at_pose(self.feedback(), pose) else 0
                if settled >= 3:
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("Waypoint not reached; recovery required")

    def stop(self):
        self.interrupted.set()
        # Hold at freshly observed joint angles. Do not use firmware T:0:
        # that command disables torque temporarily and can drop the load.
        pose = self.feedback()
        with self.lock:
            self.send(
                {"T": 102, **pose, "spd": self.config["speed"], "acc": self.config["acceleration"]}
            )

    def recover(self):
        self.feedback()
        self.interrupted.clear()

    def close(self):
        self.serial.close()
