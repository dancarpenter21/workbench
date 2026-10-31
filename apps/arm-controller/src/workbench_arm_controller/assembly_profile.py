"""Versioned DF Robot V2 assembly constraints; no hardware access or pose calibration."""

import math
from dataclasses import dataclass

PROFILE_VERSION = "dfrobot-v2/1"


@dataclass(frozen=True)
class ServoProfile:
    channel: int
    minimum_us: float
    maximum_us: float
    nominal_degrees_per_us: float
    observation: str
    reference_us: float | None = None
    reference_description: str | None = None
    calibrated_direction: int | None = None
    measured_uncertainty_deg: float | None = None

    def nominal_travel_degrees(self, pulse_delta_us):
        """Unsigned shaft travel scale, not a calibrated joint angle or claw aperture."""
        return abs(pulse_delta_us) * self.nominal_degrees_per_us


SERVOS = (
    ServoProfile(0, 850, 1600, 0.09, "Increasing pulse closes claw; aperture unmeasured."),
    ServoProfile(1, 500, 2500, 0.09, "Claw twist spans nominally 180 degrees."),
    ServoProfile(2, 500, 2500, 0.09, "Reported +Y; parent horizontal: 500 points -Z, 2500 +Z."),
    ServoProfile(3, 775, 2500, 0.09, "Reported -Y; increasing pulse points elbow downward."),
    ServoProfile(
        4,
        600,
        2100,
        0.09,
        "Reported +Y; increasing raises link; roughly upright at 2000, 45 down at 600. "
        "Bounds address base self-collision only.",
    ),
    ServoProfile(5, 500, 2500, 0.135, "Base rotates about +Z.", 1500, "Base +X reference"),
)


def intersect_limits(channel, minimum, maximum):
    """Intersect saved limits with independent bounds; never relax or clamp a target."""
    if type(channel) is not int or not 0 <= channel < len(SERVOS):
        raise ValueError("Assembly channels must be integers from 0 to 5")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (minimum, maximum)):
        raise ValueError("Pulse limits must be finite numbers")
    if not 500 <= minimum < maximum <= 2500:
        raise ValueError("Pulse limits must lie within 500-2500 microseconds")
    profile = SERVOS[channel]
    low, high = max(minimum, profile.minimum_us), min(maximum, profile.maximum_us)
    if low >= high:
        raise ValueError("Saved pulse limits are incompatible with assembly profile")
    return low, high
