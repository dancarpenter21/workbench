"""Model validity must never promote synthetic or incomplete measurements to hardware."""

import copy
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
from workbench_common.robot_model import ACTUATED_JOINTS, load_model

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = ROOT / "ros/src/workbench_description"
DEMO = DESCRIPTION / "config/simulated.json"


def expand(*, mock_control):
    executable = ROOT / "ros/tooling/.venv/bin/xacro"
    if not executable.exists():
        pytest.skip("Install ros/tooling to run Xacro expansion tests")
    result = subprocess.run(
        [
            str(executable),
            str(DESCRIPTION / "urdf/workbench.urdf.xacro"),
            f"model:={DEMO}",
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
