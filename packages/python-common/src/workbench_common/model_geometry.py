"""Versioned measured box geometry. No hardware or ROS dependencies."""

import copy
import math


def upgrade_model(model):
    result = copy.deepcopy(model)
    if result.get("schema_version") == 2:
        return result
    result["schema_version"] = 2
    result["complete"] = False
    result["scene"] = []
    result["collision_exclusions"] = []
    result["claw_envelope"] = []
    for link in result["links"]:
        if link["name"] in {"tool0", "claw_link"}:
            link["boxes"] = []
        else:
            link["boxes"] = [
                {
                    "name": "body",
                    "size": link.get("size"),
                    "xyz": link.get("xyz"),
                    "rpy": link.get("rpy", [0, 0, 0]),
                    "margin_m": None,
                }
            ]
    # Legacy fields remain in the document; the old rotating claw box is not a
    # measured full-sweep envelope in the hand frame and cannot be promoted.
    return result


def link_boxes(model, link):
    if model.get("schema_version") == 1:
        return [] if link["name"] == "tool0" else [link]
    boxes = link.get("boxes", [])
    if link["name"] == "hand_link":
        return [*boxes, *model.get("claw_envelope", [])]
    return boxes


def validate_geometry(model, *, complete=False):
    """Drafts allow null measurements, but never malformed values or identifiers."""
    if model.get("schema_version") != 2:
        return

    model_srdf(model, "<robot name='workbench'/>")

    def number(value, path, positive=False):
        if value is None and not complete:
            return
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{path}: a finite measurement is required")
        if value < 0 or (positive and value == 0):
            raise ValueError(f"{path}: must be {'positive' if positive else 'nonnegative'}")

    def boxes(values, path, required=False):
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError(f"{path}: expected at most 64 boxes")
        if complete and required and not values:
            raise ValueError(f"{path}: measured geometry is required")
        seen = set()
        for i, box in enumerate(values):
            prefix = f"{path}[{i}]"
            if not isinstance(box, dict):
                raise ValueError(f"{prefix}: expected a box")
            name = box.get("name")
            if not isinstance(name, str) or not name.isidentifier() or name in seen:
                raise ValueError(f"{prefix}.name: a unique identifier is required")
            seen.add(name)
            for key in ("size", "xyz", "rpy"):
                value = box.get(key)
                if value is None and not complete:
                    continue
                if not isinstance(value, list) or len(value) != 3:
                    raise ValueError(f"{prefix}.{key}: three coordinates are required")
                for n in value:
                    if n is None and not complete:
                        continue
                    if type(n) not in (int, float) or not math.isfinite(n):
                        raise ValueError(f"{prefix}.{key}: finite coordinates are required")
                    if key == "size" and n <= 0:
                        raise ValueError(f"{prefix}.size: dimensions must be positive")
            number(box.get("margin_m"), f"{prefix}.margin_m")
            if path == "scene" and box.get("kind") not in {"table", "obstacle"}:
                raise ValueError(f"{prefix}.kind: expected table or obstacle")

    references = model.get("reference_poses", [])
    if not isinstance(references, list) or len(references) > 12:
        raise ValueError("reference_poses: expected at most 12 measured poses")
    names = set()
    joint_names = {j["name"] for j in model.get("joints", []) if j["type"] != "fixed"}
    for pose in references:
        if (
            not isinstance(pose, dict)
            or not isinstance(pose.get("name"), str)
            or not pose["name"].strip()
            or pose["name"] in names
        ):
            raise ValueError("reference_poses: unique names are required")
        names.add(pose["name"])
        positions = pose.get("joint_positions")
        if not isinstance(positions, dict) or set(positions) != joint_names:
            raise ValueError("reference_poses: every joint angle is required")
        for value in positions.values():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("reference_poses: joint angles must be finite")
        for key in ("observed_xyz", "observed_rpy"):
            values = pose.get(key)
            if values is None:
                continue
            if (
                not isinstance(values, list)
                or len(values) != 3
                or any(
                    v is not None and (type(v) not in (int, float) or not math.isfinite(v))
                    for v in values
                )
            ):
                raise ValueError(f"reference_poses.{key}: three finite coordinates are required")
        for key in ("position_tolerance_m", "orientation_tolerance_rad"):
            value = pose.get(key)
            if value is not None:
                number(value, f"reference_poses.{key}", positive=True)

    for link in model.get("links", []):
        name = link["name"]
        boxes(link.get("boxes"), f"{name}.boxes", name not in {"tool0", "claw_link"})
        if name in {"tool0", "claw_link"} and link.get("boxes"):
            raise ValueError(f"{name}: use the hand-frame claw envelope; tool0 is a datum")
    boxes(model.get("claw_envelope"), "claw_envelope", True)
    boxes(model.get("scene"), "scene", True)
    if complete and not any(b["kind"] == "table" for b in model["scene"]):
        raise ValueError("scene: a measured table is required")


def model_srdf(model, template):
    """Use only explicitly recorded exclusions for a v2 measured model."""
    from xml.etree.ElementTree import SubElement, fromstring, tostring

    root = fromstring(template)
    if model.get("schema_version") == 2:
        for entry in list(root.findall("disable_collisions")):
            root.remove(entry)
        names = {link["name"] for link in model["links"]}
        seen = set()
        entries = model.get("collision_exclusions", [])
        if not isinstance(entries, list) or len(entries) > 28:
            raise ValueError("collision_exclusions: expected at most 28 reviewed pairs")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("collision_exclusions: expected reviewed pairs")
            left, right = entry.get("link1"), entry.get("link2")
            reason = entry.get("reason")
            if (
                not isinstance(left, str)
                or not isinstance(right, str)
                or left not in names
                or right not in names
                or left == right
            ):
                raise ValueError("collision_exclusions: unknown or identical links")
            pair = frozenset((left, right))
            if pair in seen or not isinstance(reason, str) or not reason.strip():
                raise ValueError("collision_exclusions: unique pairs and review reasons required")
            seen.add(pair)
            SubElement(root, "disable_collisions", link1=left, link2=right, reason=reason)
    return tostring(root, encoding="unicode")
