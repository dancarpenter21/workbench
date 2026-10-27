from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench_contracts import (
    ClawCurrentStatus,
    CommissionBoundsUpdate,
    CommissionSetup,
    Diagnostic,
    ManualInput,
    ManualJoint,
    ManualStatus,
    Recovery,
)
from workbench_contracts.ros_transport import decode, encode, field_type, from_ros, to_ros


def factory(_):
    return SimpleNamespace()


def test_unknown_current_remains_distinct_from_verified_zero():
    sample = ClawCurrentStatus(simulated=True, shunt_mv=0.0, current_a=None, scale_verified=False)
    message = to_ros(sample, factory=factory)
    assert not message.has_current_a
    message.current_a = 0.0  # An unused ROS default must not become a measured reading.
    assert from_ros(message, ClawCurrentStatus).current_a is None
    zero = sample.model_copy(update={"current_a": 0.0, "scale_verified": True})
    message = to_ros(zero, factory=factory)
    assert message.has_current_a
    assert from_ros(message, ClawCurrentStatus) == zero


def test_nested_state_and_metadata_roundtrip_without_loss():
    state = ManualStatus(
        simulated=True,
        prepared=True,
        claw_current=ClawCurrentStatus(shunt_mv=-0.2, simulated=True),
        joints=[
            ManualJoint(
                id="claw",
                label="Claw",
                channel=0,
                min_us=500,
                max_us=2500,
                max_rate_us_per_second=100,
                direction="increasing",
            )
        ],
    )
    assert from_ros(to_ros(state, factory=factory), ManualStatus) == state
    diagnostic = Diagnostic(
        component="claw",
        status="warning",
        summary="Unverified scale",
        measurements={"raw_mv": -0.2, "simulated": True, "amperes": None},
    )
    assert from_ros(to_ros(diagnostic, factory=factory), Diagnostic) == diagnostic


def test_native_requests_still_require_canonical_validation():
    valid = ManualInput(token=uuid4(), sequence=1, joint_id="claw", speed=0.5)
    message = to_ros(valid, factory=factory)
    assert from_ros(message, ManualInput) == valid
    message.speed = 2.0
    with pytest.raises(ValidationError):
        from_ros(message, ManualInput)
    with pytest.raises(ValidationError):
        from_ros(SimpleNamespace(acknowledged=False), Recovery)
    with pytest.raises(ValueError, match="int64"):
        to_ros(valid.model_copy(update={"sequence": 2**80}), factory=factory)


def test_flexible_metadata_rejects_nonfinite_json():
    spec = field_type(dict[str, Any])
    with pytest.raises(ValueError):
        encode({"raw": float("nan")}, spec, factory)
    with pytest.raises(ValueError):
        decode('{"raw": NaN}', spec)
    with pytest.raises(ValueError):
        encode({"nested": [{"raw": float("inf")}]}, spec, factory)
    with pytest.raises(ValueError):
        decode('{"raw": 1e999}', spec)


def test_limit_deletion_flags_survive_gateway_roundtrip():
    request = CommissionBoundsUpdate(
        assembly_id="test",
        setup=CommissionSetup(backend="mock"),
        joints={"base": {"clear_minimum": True}, "claw": {"clear_maximum": True}},
    )
    assert from_ros(to_ros(request, factory=factory), CommissionBoundsUpdate) == request
