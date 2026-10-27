"""Model validity must never promote synthetic or incomplete measurements to hardware."""

import copy
import json
import math
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
from workbench_common.robot_model import ACTUATED_JOINTS, load_model

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = ROOT / "ros/src/workbench_description"
DEMO = DESCRIPTION / "config/simulated.json"


def expand(*, mock_control, model=DEMO):
    executable = ROOT / "ros/tooling/.venv/bin/xacro"
    if not executable.exists():
        pytest.skip("Install ros/tooling to run Xacro expansion tests")
    result = subprocess.run(
        [
            str(executable),
            str(DESCRIPTION / "urdf/workbench.urdf.xacro"),
            f"model:={model}",
            f"mock_control:={str(mock_control).lower()}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return ElementTree.fromstring(result.stdout)


def test_demo_is_explicitly_simulated():
    model = load_model(DEMO, mode="mock")
    assert model["domain"] == "simulated"
    with pytest.raises(ValueError, match="physical"):
        load_model(DEMO, mode="hardware")


def test_physical_template_cannot_activate():
    with pytest.raises(ValueError, match="incomplete"):
        load_model(DESCRIPTION / "config/physical.example.json", mode="hardware")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["links"][0].update(size=[0, 1, 1]),
        lambda d: d["links"][0].update(xyz=[True, 0, 0]),
        lambda d: d["links"][0].update(rpy=[0, float("nan"), 0]),
        lambda d: d["links"][0].update(rpy=[0, 0]),
        lambda d: d["links"][0].update(rpy=None),
        lambda d: d["joints"][0].update(axis=[0, 0, 2]),
        lambda d: d["joints"][0].update(upper=float("nan")),
        lambda d: d["joints"][0].update(lower=2),
        lambda d: d["joints"][0].update(channel=0),
        lambda d: d["joints"][0].update(type="fixed"),
        lambda d: d["joints"][0].update(child="hand_link"),
        lambda d: d["joints"][1].update(parent="upper_arm_link"),
        lambda d: d["joints"][-1].update(parent="claw_link"),
        lambda d: d["joints"].pop(2),
        lambda d: d["links"].append(copy.deepcopy(d["links"][0])),
    ],
)
def test_invalid_model_rejected(tmp_path, mutate):
    model = json.loads(DEMO.read_text())
    mutate(model)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    with pytest.raises(ValueError):
        load_model(path, mode="mock")


def test_xacro_expands_with_six_controlled_joints_and_collision_geometry():
    load_model(DEMO, mode="mock")
    robot = expand(mock_control=True)
    controlled = {joint.attrib["name"] for joint in robot.findall("ros2_control/joint")}
    assert controlled == set(ACTUATED_JOINTS)
    assert robot.find("ros2_control/hardware/plugin").text == "mock_components/GenericSystem"
    assert all(
        link.find("collision") is not None
        for link in robot.findall("link")
        if link.attrib["name"] != "tool0"
    )
    assert robot.find("link[@name='tool0']/collision") is None
    assert len(robot.findall("joint")) == 7


def test_description_without_mock_control_has_no_hardware_plugin():
    robot = expand(mock_control=False)
    assert robot.find("ros2_control") is None


def test_srdf_disables_only_adjacent_collisions():
    model = load_model(DEMO, mode="mock")
    adjacent = {frozenset((j["parent"], j["child"])) for j in model["joints"]}
    srdf = ElementTree.parse(ROOT / "ros/src/workbench_moveit_config/config/workbench.srdf")
    for entry in srdf.findall("disable_collisions"):
        assert frozenset((entry.attrib["link1"], entry.attrib["link2"])) in adjacent


def test_oriented_box_export_preserves_box_dimensions_and_joint_frame(tmp_path):
    model = json.loads(DEMO.read_text())
    link = next(link for link in model["links"] if link["name"] == "upper_arm_link")
    link.update(size=[0.254, 0.0508, 0.0254], xyz=[0.11, 0, 0.0635], rpy=[0, -math.pi / 6, 0])
    path = tmp_path / "oriented.json"
    path.write_text(json.dumps(model))
    load_model(path, mode="mock")
    robot = expand(mock_control=False, model=path)
    for kind in ("visual", "collision"):
        shape = robot.find(f"link[@name='upper_arm_link']/{kind}")
        assert [float(v) for v in shape.find("geometry/box").attrib["size"].split()] == link["size"]
        origin = shape.find("origin").attrib
        assert [float(v) for v in origin["xyz"].split()] == link["xyz"]
        assert [float(v) for v in origin["rpy"].split()] == link["rpy"]
    joint = next(j for j in model["joints"] if j["name"] == "shoulder_joint")
    exported = robot.find("joint[@name='shoulder_joint']/origin").attrib
    assert [float(v) for v in exported["rpy"].split()] == joint["rpy"]
    assert [float(v) for v in exported["xyz"].split()] == joint["xyz"]
    # Box +X follows the component upward, independently of the joint frame.
    pitch = float(
        robot.find("link[@name='upper_arm_link']/collision/origin").attrib["rpy"].split()[1]
    )
    assert -math.sin(pitch) == pytest.approx(0.5)


def test_legacy_box_without_rotation_keeps_link_alignment(tmp_path):
    model = json.loads(DEMO.read_text())
    for link in model["links"]:
        link.pop("rpy", None)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(model))
    loaded = load_model(path, mode="mock")
    assert all("rpy" not in link for link in loaded["links"])
    robot = expand(mock_control=False, model=path)
    for link in robot.findall("link"):
        if link.attrib["name"] != "tool0":
            assert link.find("collision/origin").attrib["rpy"] == "0 0 0"
