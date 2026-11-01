"""Validate ROS model inputs without importing ROS or opening any hardware."""

import json
import math
from pathlib import Path

from .model_geometry import link_boxes, validate_geometry

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
    if type(data.get("schema_version")) is not int or data.get("schema_version") not in (1, 2):
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
        if name == "tool0" or data["schema_version"] == 2:
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
    validate_geometry(data, complete=True)
    return data


def render_preview(data, *, include_scene=True, measured_limits=False):
    """Render only measured draft geometry; never produce control interfaces.

    Unresolved parent transforms omit their subtree. Preview limits are not evidence
    of measured speed or effort and this document cannot qualify physical execution.
    """
    from xml.etree.ElementTree import Element, SubElement, tostring

    def measured(value):
        return (
            isinstance(value, list)
            and len(value) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)
        )

    def coordinates(value):
        return " ".join(str(v) for v in value)

    robot = Element("robot", name="workbench")
    known = {"base_link"}
    omitted = []
    joints = []
    for joint in data.get("joints", []):
        name = joint["name"]
        valid = (
            joint["parent"] in known and measured(joint.get("xyz")) and measured(joint.get("rpy"))
        )
        if not valid:
            omitted.append(f"{name}: incomplete frame or parent")
            continue
        if joint["type"] != "fixed":
            axis = joint.get("axis")
            lower, upper = joint.get("lower"), joint.get("upper")
            movable = (
                measured(axis)
                and math.isclose(sum(v * v for v in axis), 1, abs_tol=1e-6)
                and type(lower) in (int, float)
                and type(upper) in (int, float)
                and math.isfinite(lower)
                and math.isfinite(upper)
                and lower < upper
            )
            if not movable:
                if measured_limits:
                    raise ValueError(f"{name}: complete measured joint limits are required")
                omitted.append(f"{name}: shown fixed at reference; axis or limits incomplete")
                joint = dict(joint, type="fixed")
        known.add(joint["child"])
        joints.append(joint)
    count = 0
    for link in data.get("links", []):
        if link["name"] not in known:
            continue
        element = SubElement(robot, "link", name=link["name"])
        if link["name"] == "tool0":
            continue
        for box in link_boxes(data, link):
            size, xyz, rpy = box.get("size"), box.get("xyz"), box.get("rpy", [0, 0, 0])
            if not (
                measured(size) and all(v > 0 for v in size) and measured(xyz) and measured(rpy)
            ):
                omitted.append(
                    f"{link['name']}/{box.get('name', 'body')}: incomplete box dimensions or pose"
                )
                continue
            margin = box.get("margin_m") if data["schema_version"] == 2 else 0
            if margin is None:
                omitted.append(
                    f"{link['name']}/{box.get('name', 'body')}: uncertainty allowance missing"
                )
            count += 1
            for kind in ("visual", "collision"):
                if kind == "collision" and margin is None:
                    continue
                shape = SubElement(element, kind)
                SubElement(shape, "origin", xyz=coordinates(xyz), rpy=coordinates(rpy))
                dimensions = size if kind == "visual" else [v + 2 * margin for v in size]
                SubElement(SubElement(shape, "geometry"), "box", size=coordinates(dimensions))
                if kind == "visual":
                    SubElement(
                        SubElement(shape, "material", name="measured"),
                        "color",
                        rgba="0.2 0.65 0.85 0.65",
                    )
    for box in data.get("scene", []) if include_scene and data["schema_version"] == 2 else []:
        size, xyz, rpy = box.get("size"), box.get("xyz"), box.get("rpy")
        if not (measured(size) and all(v > 0 for v in size) and measured(xyz) and measured(rpy)):
            omitted.append(f"scene/{box['name']}: incomplete dimensions or pose")
            continue
        name = "scene_" + box["name"]
        element = SubElement(robot, "link", name=name)
        shape = SubElement(element, "visual")
        SubElement(SubElement(shape, "geometry"), "box", size=coordinates(size))
        SubElement(
            SubElement(shape, "material", name="environment"), "color", rgba="0.65 0.55 0.3 0.45"
        )
        attachment = SubElement(robot, "joint", name=name + "_mount", type="fixed")
        SubElement(attachment, "parent", link="base_link")
        SubElement(attachment, "child", link=name)
        SubElement(attachment, "origin", xyz=coordinates(xyz), rpy=coordinates(rpy))
        count += 1
    for joint in joints:
        element = SubElement(robot, "joint", name=joint["name"], type=joint["type"])
        SubElement(element, "parent", link=joint["parent"])
        SubElement(element, "child", link=joint["child"])
        SubElement(element, "origin", xyz=coordinates(joint["xyz"]), rpy=coordinates(joint["rpy"]))
        if joint["type"] != "fixed":
            SubElement(element, "axis", xyz=coordinates(joint["axis"]))
            SubElement(
                element,
                "limit",
                lower=str(joint["lower"]),
                upper=str(joint["upper"]),
                effort=str(joint["effort"]) if measured_limits else "1",
                velocity=str(joint["velocity"]) if measured_limits else "1",
            )
    return {
        "urdf": tostring(robot, encoding="unicode"),
        "omissions": omitted,
        "visible_boxes": count,
    }
