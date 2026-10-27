"""Validate ROS model inputs without importing ROS or opening any hardware."""

import json
import math
from pathlib import Path

ARM_JOINTS = ("base_joint", "shoulder_joint", "elbow_joint", "wrist_joint", "wrist_roll_joint")
ACTUATED_JOINTS = (*ARM_JOINTS, "claw_joint")
CHANNELS = dict(zip(ACTUATED_JOINTS, (5, 4, 3, 2, 1, 0), strict=True))
LINK_NAMES = {
    "base_link",
    "turntable_link",
    "upper_arm_link",
    "forearm_link",
    "wrist_link",
    "hand_link",
    "claw_link",
    "tool0",
}
JOINT_LINKS = dict(
    zip(
        ACTUATED_JOINTS,
        (
            ("base_link", "turntable_link"),
            ("turntable_link", "upper_arm_link"),
            ("upper_arm_link", "forearm_link"),
            ("forearm_link", "wrist_link"),
            ("wrist_link", "hand_link"),
            ("hand_link", "claw_link"),
        ),
        strict=True,
    )
)


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: a measured finite number is required")
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{label}: invalid value")
    return value


def vector(value, label, *, positive=False):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label}: three coordinates are required")
    return [number(v, label, positive=positive) for v in value]


def load_model(path, *, mode):
    return validate_model(json.loads(Path(path).read_text()), mode=mode)


def validate_model(data, *, mode):
    """Reject incomplete or mixed-domain models before launching planning processes.

    Validation establishes structural completeness, never physical qualification.
    Local link coordinates are in metres; joint and collision rotations are in radians.
    """
    if mode not in {"mock", "hardware"}:
        raise ValueError("mode must be mock or hardware")
    if not isinstance(data, dict):
        raise ValueError("Robot model must be an object")
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("Unsupported robot model schema")
    expected = "simulated" if mode == "mock" else "physical"
    if data.get("domain") != expected:
        raise ValueError(f"{mode} requires a {expected} robot model")
    if data.get("complete") is not True:
        raise ValueError("Robot measurements are incomplete; no robot description was activated")
    if not isinstance(data.get("revision"), str) or not data["revision"].strip():
        raise ValueError("A model revision is required")
    links, joints = data.get("links"), data.get("joints")
    if not isinstance(links, list) or not isinstance(joints, list):
        raise ValueError("links and joints are required")
    names = set()
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("Each link must be an object")
        name = link.get("name")
        if not isinstance(name, str) or not name.isidentifier() or name in names:
            raise ValueError("Links require unique identifiers")
        names.add(name)
        if name == "tool0":
            continue  # Tool datum frame has no physical collision geometry.
        vector(link.get("size"), f"{name}.size", positive=True)
        vector(link.get("xyz"), f"{name}.xyz")
        # Legacy boxes omitted their collision orientation and were link-aligned.
        vector(link.get("rpy", [0, 0, 0]), f"{name}.rpy")
    if names != LINK_NAMES:
        raise ValueError("Link names must match the workbench SRDF")
    seen = set()
    attached = {"base_link"}
    for joint in joints:
        if not isinstance(joint, dict):
            raise ValueError("Each joint must be an object")
        name = joint.get("name")
        if not isinstance(name, str) or not name.isidentifier() or name in seen:
            raise ValueError("Joints require unique identifiers")
        seen.add(name)
        parent, child = joint.get("parent"), joint.get("child")
        if parent not in attached or child not in names or child in attached:
            raise ValueError(f"{name}: joints must form one ordered tree rooted at base_link")
        attached.add(child)
        vector(joint.get("xyz"), f"{name}.xyz")
        vector(joint.get("rpy"), f"{name}.rpy")
        kind = joint.get("type")
        if kind == "fixed":
            if name in ACTUATED_JOINTS:
                raise ValueError(f"{name} cannot be fixed")
            continue
        if kind != "revolute" or name not in ACTUATED_JOINTS:
            raise ValueError(f"{name}: unsupported movable joint")
        if (parent, child) != JOINT_LINKS[name]:
            raise ValueError(f"{name}: link relationship must match SRDF collision exclusions")
        axis = vector(joint.get("axis"), f"{name}.axis")
        if not math.isclose(sum(v * v for v in axis), 1, abs_tol=1e-6):
            raise ValueError(f"{name}: axis must be a unit vector")
        lower, upper = (number(joint.get(k), f"{name}.{k}") for k in ("lower", "upper"))
        if not lower < upper or not lower <= 0 <= upper:
            raise ValueError(f"{name}: limits must contain the neutral reference")
        for key in ("velocity", "effort"):
            number(joint.get(key), f"{name}.{key}", positive=True)
        if type(joint.get("channel")) is not int or joint["channel"] != CHANNELS[name]:
            raise ValueError(f"{name}: wrong PCA9685 channel")
    if attached != names or not set(ACTUATED_JOINTS).issubset(seen):
        raise ValueError("Model must connect every link and all six actuated joints")
    # SRDF assumes this serial chain; accepting a different tree silently changes IK.
    arm = [j for j in joints if j["name"] in ARM_JOINTS]
    if arm[0]["parent"] != "base_link":
        raise ValueError("Arm chain must start at base_link")
    if tuple(j["name"] for j in arm) != ARM_JOINTS:
        raise ValueError("Arm joints must be in base-to-wrist order")
    for previous, current in zip(arm, arm[1:]):
        if current["parent"] != previous["child"]:
            raise ValueError("Arm joints must form a serial chain")
    tool = next(j for j in joints if j["child"] == "tool0")
    if tool["type"] != "fixed" or tool["parent"] != arm[-1]["child"]:
        raise ValueError("tool0 must be fixed to the wrist roll link")
    return data
