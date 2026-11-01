import copy
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from workbench_common.model_geometry import upgrade_model, validate_geometry
from workbench_common.robot_model import render_preview, validate_model

ROOT = Path(__file__).resolve().parents[2]


def measured_model():
    legacy = json.loads((ROOT / "ros/src/workbench_description/config/simulated.json").read_text())
    model = upgrade_model(legacy)
    for link in model["links"]:
        for box in link["boxes"]:
            box["margin_m"] = 0.001
    model["claw_envelope"] = [
        {
            "name": "sweep",
            "size": [0.02, 0.02, 0.04],
            "xyz": [0, 0, 0.04],
            "rpy": [0, 0, 0],
            "margin_m": 0.001,
        }
    ]
    model["scene"] = [
        {
            "name": "table",
            "kind": "table",
            "size": [1, 1, 0.04],
            "xyz": [0, 0, -0.1],
            "rpy": [0, 0, 0],
            "margin_m": 0.001,
        }
    ]
    model["complete"] = True
    return model


def test_upgrade_preserves_legacy_but_never_invents_envelope_or_allowance():
    legacy = json.loads((ROOT / "ros/src/workbench_description/config/simulated.json").read_text())
    original = copy.deepcopy(legacy)
    model = upgrade_model(legacy)
    assert legacy == original
    assert model["complete"] is False
    assert model["links"][0]["boxes"][0]["margin_m"] is None
    assert not model["claw_envelope"]
    assert model["links"][-2]["size"] == legacy["links"][-2]["size"]
    assert upgrade_model(model) == model
    with pytest.raises(ValueError):
        validate_geometry(model, complete=True)


def test_multiple_boxes_expand_collisions_and_attach_sweep_to_hand():
    model = measured_model()
    box = copy.deepcopy(model["links"][0]["boxes"][0])
    box["name"] = "bracket"
    model["links"][0]["boxes"].append(box)
    validate_model(model, mode="mock")
    root = ElementTree.fromstring(
        render_preview(model, include_scene=False, measured_limits=True)["urdf"]
    )
    link = root.find("link[@name='base_link']")
    assert len(link.findall("collision")) == 2
    sizes = [float(v) for v in link.find("collision/geometry/box").get("size").split()]
    assert sizes == pytest.approx([v + 0.002 for v in box["size"]])
    assert len(root.findall("link[@name='hand_link']/collision")) == 2
    assert root.find("link[@name='claw_link']/collision") is None
    assert root.find("ros2_control") is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["scene"][0].update(margin_m=-1),
        lambda m: m["scene"][0].update(size=[True, 1, 1]),
        lambda m: m["scene"][0].update(kind="unknown"),
        lambda m: m["claw_envelope"][0].update(rpy=[float("nan"), 0, 0]),
        lambda m: m["links"][0]["boxes"].append(copy.deepcopy(m["links"][0]["boxes"][0])),
    ],
)
def test_malformed_box_drafts_rejected(mutation):
    model = measured_model()
    mutation(model)
    with pytest.raises(ValueError):
        validate_geometry(model)


def test_missing_margin_is_draft_only_and_never_zero_collision_allowance():
    model = measured_model()
    model["links"][0]["boxes"][0]["margin_m"] = None
    validate_geometry(model)
    with pytest.raises(ValueError, match="margin_m"):
        validate_geometry(model, complete=True)
    preview = render_preview(model)
    root = ElementTree.fromstring(preview["urdf"])
    assert root.find("link[@name='base_link']/visual") is not None
    assert root.find("link[@name='base_link']/collision") is None
    assert any("allowance missing" in message for message in preview["omissions"])


def test_measured_srdf_requires_explicit_exclusion_reasons():
    from workbench_common.model_geometry import model_srdf

    model = measured_model()
    template = (ROOT / "ros/src/workbench_moveit_config/config/workbench.srdf").read_text()
    assert not ElementTree.fromstring(model_srdf(model, template)).findall("disable_collisions")
    model["collision_exclusions"] = [
        {"link1": "base_link", "link2": "turntable_link", "reason": ""}
    ]
    with pytest.raises(ValueError, match="reasons"):
        validate_geometry(model)
    model["collision_exclusions"][0]["reason"] = "Reviewed mounting interface in synthetic fixture"
    assert (
        len(ElementTree.fromstring(model_srdf(model, template)).findall("disable_collisions")) == 1
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["links"].__setitem__(0, None),
        lambda m: m["links"][0].pop("name"),
        lambda m: m["joints"].__setitem__(0, None),
        lambda m: m["joints"][0].pop("type"),
        lambda m: m.update(collision_exclusions=[{"link1": [], "link2": "hand_link"}]),
    ],
)
def test_malformed_complete_models_raise_validation_errors(mutation):
    model = measured_model()
    mutation(model)
    with pytest.raises(ValueError):
        validate_model(model, mode="mock")


def test_v2_xacro_matches_backend_collision_boxes(tmp_path):
    import subprocess

    model = measured_model()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    executable = ROOT / "ros/tooling/.venv/bin/xacro"
    if not executable.exists():
        pytest.skip("Xacro tooling is unavailable")
    output = subprocess.run(
        [
            str(executable),
            str(ROOT / "ros/src/workbench_description/urdf/workbench.urdf.xacro"),
            f"model:={path}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    xacro = ElementTree.fromstring(output.stdout)
    backend = ElementTree.fromstring(
        render_preview(model, include_scene=False, measured_limits=True)["urdf"]
    )
    for link in model["links"]:
        for kind in ("collision", "visual"):
            left = xacro.findall(f"link[@name='{link['name']}']/{kind}")
            right = backend.findall(f"link[@name='{link['name']}']/{kind}")
            assert len(left) == len(right)
            for a, b in zip(left, right, strict=True):
                assert a.find("origin").attrib == b.find("origin").attrib
                assert a.find("geometry/box").attrib == b.find("geometry/box").attrib


def test_export_keeps_evidence_domain_and_never_authorizes_motion():
    from workbench_arm_controller.model_inspection import inspect_model
    from workbench_contracts import RobotModelInspectionRequest

    model = measured_model()
    request = RobotModelInspectionRequest(
        context_key="simulated", revision=3, model=model, check_collisions=False
    )
    result = inspect_model(model, request)
    assert not result.errors
    assert not result.available
    assert result.bundle["physical_execution_authorized"] is False
    assert result.bundle["model"]["domain"] == "simulated"
    assert result.bundle["model_hash"] == result.model_hash
    assert "ros2_control" not in result.bundle["urdf"]
    assert "scene_table" not in result.bundle["urdf"]
    assert result.bundle["scene"] == model["scene"]


def test_inspection_unavailable_never_reports_pass(monkeypatch):
    import subprocess

    from workbench_arm_controller.model_inspection import inspect_model
    from workbench_common.robot_model import ACTUATED_JOINTS
    from workbench_contracts import RobotModelInspectionRequest

    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("model_validator", 15)

    monkeypatch.setattr(subprocess, "run", unavailable)
    model = measured_model()
    request = RobotModelInspectionRequest(
        context_key="simulated",
        revision=0,
        model=model,
        joint_positions=dict.fromkeys(ACTUATED_JOINTS, 0.0),
    )
    result = inspect_model(model, request)
    assert result.available is False
    assert result.collision is None and result.in_bounds is None
    assert result.errors


@pytest.mark.parametrize(
    "change",
    [
        {"collision": "false"},
        {"in_bounds": 1},
        {"tool_xyz": [0, 0]},
        {"tool_xyzw": [0, 0, 0, float("nan")]},
        {"pairs": [["only_one_link"]]},
        {"reference_results": [{"name": "pose", "status": "approved"}]},
    ],
)
def test_malformed_inspector_output_discards_entire_result(monkeypatch, change):
    import subprocess

    from workbench_arm_controller.model_inspection import inspect_model
    from workbench_common.robot_model import ACTUATED_JOINTS
    from workbench_contracts import RobotModelInspectionRequest

    report = {
        "in_bounds": True,
        "collision": False,
        "pairs": [],
        "tool_xyz": [0, 0, 0],
        "tool_xyzw": [0, 0, 0, 1],
        "reference_results": [],
    }
    report.update(change)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(report)),
    )
    model = measured_model()
    request = RobotModelInspectionRequest(
        context_key="simulated",
        revision=0,
        model=model,
        joint_positions=dict.fromkeys(ACTUATED_JOINTS, 0.0),
    )
    result = inspect_model(model, request)
    assert result.available is False and result.errors
    assert result.collision is None and result.in_bounds is None
    assert not result.tool_xyz and not result.tool_xyzw
    assert "reference_results" not in result.bundle


def test_reference_measurements_require_finite_angles_and_explicit_tolerances():
    from workbench_common.robot_model import ACTUATED_JOINTS

    model = measured_model()
    model["reference_poses"] = [
        {
            "name": "Measured pose",
            "joint_positions": dict.fromkeys(ACTUATED_JOINTS, 0.0),
            "observed_xyz": None,
            "observed_rpy": None,
            "position_tolerance_m": None,
            "orientation_tolerance_rad": None,
        }
    ]
    validate_geometry(model, complete=True)  # Complete geometry is not complete physical evidence.
    model["reference_poses"][0]["position_tolerance_m"] = -0.001
    with pytest.raises(ValueError, match="tolerance"):
        validate_geometry(model)


def run_native_inspection(model, positions=None):
    import os
    import subprocess

    from workbench_common.model_geometry import model_srdf
    from workbench_common.robot_model import ACTUATED_JOINTS

    binary = Path(
        os.environ.get(
            "WORKBENCH_TEST_MODEL_VALIDATOR",
            ROOT / "ros/install/workbench_planning/lib/workbench_planning/model_validator",
        )
    )
    if not binary.is_file():
        pytest.skip("Build the planning overlay for real MoveIt collision tests")
    template = (ROOT / "ros/src/workbench_moveit_config/config/workbench.srdf").read_text()
    payload = {
        "urdf": render_preview(model, include_scene=False, measured_limits=True)["urdf"],
        "srdf": model_srdf(model, template),
        "scene": model["scene"],
        "joint_positions": (
            dict.fromkeys(ACTUATED_JOINTS, 0.0) if positions is None else positions
        ),
        "reference_poses": model.get("reference_poses", []),
    }
    output = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; exec "$2"',
            "model-test",
            str(ROOT / "scripts/ros-env.bash"),
            str(binary),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(output.stdout.strip().splitlines()[-1])


def separated_synthetic_model():
    model = measured_model()
    for i, link in enumerate(model["links"]):
        for box in link["boxes"]:
            box.update(size=[0.01, 0.01, 0.01], xyz=[float(i + 1), 0, 0])
    model["claw_envelope"][0].update(size=[0.01, 0.01, 0.01], xyz=[20, 0, 0])
    model["scene"][0]["xyz"] = [0, 0, -10]
    return model


def test_native_clear_pose_and_bounds():
    from workbench_common.robot_model import ACTUATED_JOINTS

    model = separated_synthetic_model()
    report = run_native_inspection(model)
    assert report["in_bounds"] and not report["collision"]
    positions = dict.fromkeys(ACTUATED_JOINTS, 0.0)
    positions["shoulder_joint"] = 100
    assert not run_native_inspection(model, positions)["in_bounds"]


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("invalid", ["missing", "unknown", "boolean"])
def test_native_rejects_incomplete_or_invalid_joint_inputs(reference, invalid):
    import subprocess

    from workbench_common.robot_model import ACTUATED_JOINTS

    model = separated_synthetic_model()
    positions = dict.fromkeys(ACTUATED_JOINTS, 0.0)
    if invalid == "missing":
        positions.pop("shoulder_joint")
    elif invalid == "unknown":
        positions["unknown_joint"] = positions.pop("shoulder_joint")
    else:
        positions["shoulder_joint"] = True
    if reference:
        model["reference_poses"] = [{"name": "Invalid reference", "joint_positions": positions}]
    with pytest.raises(subprocess.CalledProcessError):
        run_native_inspection(model, None if reference else positions)


@pytest.mark.parametrize("kind", ["table", "obstacle", "self"])
def test_native_in_bounds_collisions_rejected(kind):
    model = separated_synthetic_model()
    if kind == "self":
        model["links"][3]["boxes"][0].update(size=[100, 100, 100], xyz=[0, 0, 0])
    else:
        model["scene"].append(
            {
                "name": "contact",
                "kind": kind,
                "size": [0.2, 0.2, 0.2],
                "xyz": [1, 0, 0],
                "rpy": [0, 0, 0],
                "margin_m": 0.001,
            }
        )
    report = run_native_inspection(model)
    assert report["in_bounds"] and report["collision"] and report["pairs"]
    if kind == "self":
        assert any("forearm_link" in pair and "base_link" in pair for pair in report["pairs"])
    else:
        assert any("measured_contact" in pair and "base_link" in pair for pair in report["pairs"])


def test_native_reference_residual_and_missing_evidence():
    from workbench_common.robot_model import ACTUATED_JOINTS

    model = separated_synthetic_model()
    pose = {
        "name": "Synthetic reference",
        "joint_positions": dict.fromkeys(ACTUATED_JOINTS, 0.0),
        "observed_xyz": [0, 0, 0],
        "observed_rpy": [0, 0, 0],
        "position_tolerance_m": 0.0001,
        "orientation_tolerance_rad": 0.0001,
    }
    model["reference_poses"] = [pose]
    report = run_native_inspection(model)
    pose["observed_xyz"] = report["tool_xyz"]
    # This synthetic model's zero pose is axis-aligned.
    report = run_native_inspection(model)
    assert report["reference_results"][0]["position_error_m"] == pytest.approx(0)
    pose["observed_xyz"][0] += 0.01
    assert run_native_inspection(model)["reference_results"][0]["status"] == "failed"
    pose["observed_xyz"] = None
    assert run_native_inspection(model)["reference_results"][0]["status"] == "incomplete"


def test_native_allowances_affect_environment_and_self_collision():
    model = separated_synthetic_model()
    model["scene"].append(
        {
            "name": "nearby",
            "kind": "obstacle",
            "size": [0.01, 0.01, 0.01],
            "xyz": [1.016, 0, 0],
            "rpy": [0, 0, 0],
            "margin_m": 0,
        }
    )
    assert not run_native_inspection(model)["collision"]
    model["links"][0]["boxes"][0]["margin_m"] = 0.01
    assert run_native_inspection(model)["collision"]
    model = separated_synthetic_model()
    model["links"][0]["boxes"][0]["margin_m"] = 5
    report = run_native_inspection(model)
    assert any("base_link" in pair and "forearm_link" in pair for pair in report["pairs"])
    model["collision_exclusions"] = [
        {"link1": "base_link", "link2": "forearm_link", "reason": "Synthetic regression only"}
    ]
    report = run_native_inspection(model)
    assert not any("base_link" in pair and "forearm_link" in pair for pair in report["pairs"])
    assert report["collision"]  # Other colliding pairs stay enabled.
