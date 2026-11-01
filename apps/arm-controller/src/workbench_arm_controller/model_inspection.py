"""Offline model export and MoveIt inspection; no motion interfaces."""

import hashlib
import json
import subprocess
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from workbench_common import ROOT
from workbench_common.model_geometry import model_srdf
from workbench_common.robot_model import ACTUATED_JOINTS, render_preview, validate_model
from workbench_contracts import RobotModelInspectionResult


class ReferenceResult(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str
    status: Literal["incomplete", "within_tolerance", "failed"]
    position_error_m: Annotated[FiniteFloat, Field(ge=0)] | None = None
    orientation_error_rad: Annotated[FiniteFloat, Field(ge=0)] | None = None
    collision: bool | None = None


class InspectionReport(BaseModel):
    """Validate the entire subprocess response before publishing any result."""

    model_config = ConfigDict(strict=True)
    in_bounds: bool
    collision: bool
    pairs: list[Annotated[list[str], Field(min_length=2, max_length=2)]]
    tool_xyz: Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
    tool_xyzw: Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
    reference_results: list[ReferenceResult]


def inspect_model(model, request):
    digest = hashlib.sha256(json.dumps(model, sort_keys=True, allow_nan=False).encode()).hexdigest()
    result = RobotModelInspectionResult(
        context_key=request.context_key, revision=request.revision, model_hash=digest
    )
    candidate = dict(model, complete=True)
    try:
        if candidate.get("schema_version") != 2:
            raise ValueError("Upgrade to measured boxes and scene before inspection")
        validate_model(candidate, mode="mock" if candidate["domain"] == "simulated" else "hardware")
    except ValueError as exc:
        result.errors = [str(exc)]
        return result
    urdf = render_preview(candidate, include_scene=False, measured_limits=True)["urdf"]
    srdf = (ROOT / "ros/src/workbench_moveit_config/config/workbench.srdf").read_text()
    srdf = model_srdf(model, srdf)
    # Export has explicit evidence provenance and no control plugin.
    result.bundle = {
        "model": model,
        "urdf": urdf,
        "scene": model["scene"],
        "srdf": srdf,
        "physical_execution_authorized": False,
        "model_hash": digest,
    }
    if not request.check_collisions:
        return result
    if set(request.joint_positions) != set(ACTUATED_JOINTS):
        result.errors = ["An explicit angle for each of the six joints is required"]
        return result
    payload = {
        "urdf": urdf,
        "srdf": srdf,
        "scene": model["scene"],
        "joint_positions": request.joint_positions,
        "reference_poses": [
            {
                key: pose.get(key)
                for key in (
                    "name",
                    "joint_positions",
                    "observed_xyz",
                    "observed_rpy",
                    "position_tolerance_m",
                    "orientation_tolerance_rad",
                )
            }
            for pose in model.get("reference_poses", [])
        ],
    }
    try:
        process = subprocess.run(
            ["bash", str(ROOT / "scripts/model-inspect.bash")],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
        )
        report = InspectionReport.model_validate_json(process.stdout.strip().splitlines()[-1])
        result.in_bounds = report.in_bounds
        result.collision = report.collision
        result.colliding_pairs = report.pairs
        result.tool_xyz = report.tool_xyz
        result.tool_xyzw = report.tool_xyzw
        result.bundle["reference_results"] = [
            pose.model_dump(exclude_none=True) for pose in report.reference_results
        ]
        result.available = True
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        result.errors = [
            f"MoveIt inspection unavailable ({type(exc).__name__}); rebuild the planning overlay"
        ]
    return result
