"""Offline assembly constraints; no physical acceptance or device access."""

import pytest
from workbench_arm_controller.assembly_profile import SERVOS, intersect_limits
from workbench_arm_controller.manual import free_play_configuration
from workbench_arm_controller.pca9685 import Pca9685Output, PulsePlan
from workbench_arm_controller.servo_trial import _write_pulse


@pytest.mark.parametrize("servo", SERVOS, ids=lambda s: f"channel-{s.channel}")
def test_bounds_reject_before_output_and_quantize_inside(servo):
    plan = PulsePlan({"calibrated": True, **free_play_configuration()})
    neutral = dict.fromkeys(plan.channels, 1500)
    joint = next(name for name, data in plan.channels.items() if data[0] == servo.channel)
    for edge in (servo.minimum_us, servo.maximum_us):
        pulses = plan.encode({**neutral, joint: edge})
        command = next(p for p in pulses if p.channel == servo.channel)
        assert servo.minimum_us <= command.commanded_us <= servo.maximum_us
    for outside in (servo.minimum_us - 1, servo.maximum_us + 1):
        with pytest.raises(ValueError):
            plan.encode({**neutral, joint: outside})
        with pytest.raises(ValueError):
            _write_pulse(None, 0x40, outside, servo.channel, minimum=500, maximum=2500)


def test_saved_limits_intersect_without_relaxation():
    assert intersect_limits(0, 900, 1550) == (900, 1550)
    assert intersect_limits(0, 500, 2500) == (850, 1600)
    config = {"calibrated": True, **free_play_configuration()}
    config["channels"]["claw"].update(min_us=1700, max_us=2000)
    with pytest.raises(ValueError, match="incompatible"):
        Pca9685Output(config, bus_factory=lambda _: pytest.fail("Opened bus"))


def test_profile_load_is_offline_and_preserves_neutral():
    plan = PulsePlan({"calibrated": True, **free_play_configuration()})
    assert all(p.requested_us == 1500 for p in plan.encode(dict.fromkeys(plan.channels, 1500)))


@pytest.mark.parametrize("servo", SERVOS)
def test_nominal_scale_does_not_rescale_restricted_bounds(servo):
    assert servo.nominal_travel_degrees(2000) == (270 if servo.channel == 5 else 180)
    assert servo.calibrated_direction is None
    assert servo.measured_uncertainty_deg is None
