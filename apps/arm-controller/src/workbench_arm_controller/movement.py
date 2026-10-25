"""Standalone, hardware-free ROB0036 table-clearance model and movement gate.

All geometry is in the table frame at the verified all-1500-us reference. Channel
5 is table-parallel yaw; channels 4..1 form the height chain. Channel 0 is covered
by a fixed full-opening claw envelope. This is not position feedback or a stop.
Callers serialize sessions and retain their current monitoring/output lifecycle.
"""

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

CHANNELS = frozenset(range(6))
HEIGHT_CHANNELS = (4, 3, 2, 1)
REFERENCE_US = 1500
NUMERICAL_MARGIN_MM = 1e-6


def number(value, label, low=-1_000_000, high=1_000_000):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid {label}: expected finite number in [{low}, {high}]")
    return float(value)


def vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    return tuple(number(v, label) for v in value)


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def distance(a, b):
    return math.hypot(*subtract(a, b))


def rotate(point, origin, axis, angle):
    """Rodrigues rotation about a reference-frame revolute joint axis."""
    v = subtract(point, origin)
    x, y, z = axis
    cross = (y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0])
    dot = sum(a * b for a, b in zip(axis, v, strict=True))
    c, s = math.cos(angle), math.sin(angle)
    return tuple(origin[i] + v[i] * c + cross[i] * s + axis[i] * dot * (1 - c) for i in range(3))


@dataclass(frozen=True)
class Joint:
    channel: int
    origin: tuple[float, ...]
    axis: tuple[float, ...]
    samples: tuple[tuple[float, float], ...]
    error: float

    def angle(self, pulse_us):
        pulse_us = number(pulse_us, "encoded pulse", 500, 2500)
        for (p0, q0), (p1, q1) in zip(self.samples, self.samples[1:]):
            if p0 <= pulse_us <= p1:
                return q0 + (q1 - q0) * (pulse_us - p0) / (p1 - p0)
        raise ValueError(f"Channel {self.channel}: pulse outside measured angle mapping")


@dataclass(frozen=True)
class Capsule:
    name: str
    parent_channel: int
    a: tuple[float, ...]
    b: tuple[float, ...]
    radius: float


@dataclass(frozen=True)
class ClearanceResult:
    accepted: bool
    reason: str
    component: str | None = None
    clearance_lower_bound_mm: float | None = None
    required_clearance_mm: float | None = None
    model_revision: str | None = None
    boxes_checked: int = 0
    measured_feedback: bool = False
    position_source: str = "assumed_from_successful_pwm_write"


class MovementRejected(ValueError):
    def __init__(self, result):
        self.result = result
        detail = f" ({result.component})" if result.component else ""
        if result.clearance_lower_bound_mm is not None:
            detail += f"; modeled clearance bound {result.clearance_lower_bound_mm:.3f} mm"
        super().__init__(f"Movement rejected: {result.reason}{detail}")


@dataclass(frozen=True)
class ArmGeometry:
    revision: str
    domain: str
    joints: tuple[Joint, ...]
    capsules: tuple[Capsule, ...]
    clearance: float
    geometry_error: float
    max_boxes: int = 4096

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Movement geometry requires schema_version 1")
        if data.get("calibrated") is not True:
            raise ValueError("Measured movement geometry is required")
        if data.get("reference_pulse_us") != REFERENCE_US:
            raise ValueError("Movement reference must be all six channels at 1500 us")
        if data.get("base_axis_perpendicular_to_table") is not True:
            raise ValueError("Base axis must be perpendicular to the table")
        if data.get("claw_envelope") != "full_opening_range":
            raise ValueError("A full_opening_range claw envelope is required")
        revision = data.get("revision")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("Movement geometry revision is required")
        domain = data.get("domain")
        if domain not in ("physical", "simulated"):
            raise ValueError("Movement geometry domain must be physical or simulated")
        clearance = number(data.get("clearance_mm"), "clearance_mm", 0)
        if clearance <= 0:
            raise ValueError("clearance_mm must be positive")
        error = number(data.get("geometry_error_mm"), "geometry_error_mm", 0)
        budget = data.get("max_boxes", 4096)
        if type(budget) is not int or not 1 <= budget <= 100_000:
            raise ValueError("max_boxes must be an integer in [1, 100000]")
        entries = data.get("joints")
        if not isinstance(entries, list) or len(entries) != 4:
            raise ValueError("Provide joints in channel order 4, 3, 2, 1")
        joints = []
        for channel, entry in zip(HEIGHT_CHANNELS, entries, strict=True):
            if not isinstance(entry, dict) or type(entry.get("channel")) is not int:
                raise ValueError("Invalid height joint")
            if entry["channel"] != channel:
                raise ValueError("Provide joints in channel order 4, 3, 2, 1")
            origin = vector(entry.get("origin_mm"), "origin_mm")
            axis = vector(entry.get("axis"), "axis")
            norm = math.hypot(*axis)
            if norm < 1e-12:
                raise ValueError("Joint axis must be nonzero")
            axis = tuple(v / norm for v in axis)
            samples = entry.get("pulse_angle_deg")
            if not isinstance(samples, list) or len(samples) < 3:
                raise ValueError("Provide measured pulse/angle pairs including [1500, 0]")
            parsed = []
            for pair in samples:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("pulse_angle_deg entries must be [pulse_us, angle_deg]")
                parsed.append(
                    (
                        number(pair[0], "pulse_us", 500, 2500),
                        math.radians(number(pair[1], "angle_deg", -360, 360)),
                    )
                )
            if (1500, 0) not in parsed:
                raise ValueError("Each joint mapping must include [1500, 0]")
            deltas = [b[1] - a[1] for a, b in zip(parsed, parsed[1:])]
            if any(a[0] >= b[0] for a, b in zip(parsed, parsed[1:])) or not (
                all(d > 0 for d in deltas) or all(d < 0 for d in deltas)
            ):
                raise ValueError("Pulse samples must increase; angles must be strictly monotonic")
            angular_error = math.radians(number(entry.get("error_deg"), "error_deg", 0, 180))
            joints.append(Joint(channel, origin, axis, tuple(parsed), angular_error))
        entries = data.get("capsules")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Component capsules are required")
        capsules = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid capsule")
            name, channel = entry.get("name"), entry.get("parent_channel")
            if not isinstance(name, str) or not name.strip() or name in [c.name for c in capsules]:
                raise ValueError("Capsules require unique nonempty names")
            if type(channel) is not int or channel not in HEIGHT_CHANNELS:
                raise ValueError("Capsule parent_channel must be 4, 3, 2 or 1")
            radius = number(entry.get("radius_mm"), "radius_mm", 0)
            if radius <= 0:
                raise ValueError("Capsule radius must be positive")
            capsules.append(
                Capsule(
                    name,
                    channel,
                    vector(entry.get("a_mm"), "a_mm"),
                    vector(entry.get("b_mm"), "b_mm"),
                    radius,
                )
            )
        if {c.parent_channel for c in capsules} != set(HEIGHT_CHANNELS) or not any(
            c.name == "claw" and c.parent_channel == 1 for c in capsules
        ):
            raise ValueError("Cover every moving link and include a channel-1 'claw' capsule")
        return cls(revision, domain, tuple(joints), tuple(capsules), clearance, error, budget)

    def angles(self, pulses):
        return tuple(j.angle(pulses[j.channel]) for j in self.joints)

    def _point(self, point, count, angles):
        # Product of exponentials: apply the downstream rotation first. Every
        # axis and point is expressed in the SAME neutral/table reference frame.
        for index in reversed(range(count)):
            j = self.joints[index]
            point = rotate(point, j.origin, j.axis, angles[index])
        return point

    def _radii(self, capsule):
        count = HEIGHT_CHANNELS.index(capsule.parent_channel) + 1
        # Triangle-inequality bound valid for every intervening joint angle,
        # unlike a Jacobian evaluated at just the center configuration.
        reach = max(distance(p, self.joints[count - 1].origin) for p in (capsule.a, capsule.b))
        radii = [0.0] * 4
        for index in reversed(range(count)):
            radii[index] = reach
            if index:
                reach += distance(self.joints[index].origin, self.joints[index - 1].origin)
        return tuple(radii)

    def clearances(self, angles):
        return tuple(
            min(
                self._point(p, HEIGHT_CHANNELS.index(c.parent_channel) + 1, angles)[2]
                for p in (c.a, c.b)
            )
            - c.radius
            - self.geometry_error
            for c in self.capsules
        )

    def check_motion(self, ranges, *, checkpoint=None):
        """Certify the complete joint box; ranges exclude calibrated error (added here)."""
        if len(ranges) != 4:
            raise ValueError("Four joint-angle intervals required")
        box = []
        for j, pair in zip(self.joints, ranges, strict=True):
            if len(pair) != 2:
                raise ValueError("Each angle interval requires two bounds")
            lo, hi = (number(v, "angle interval", -4 * math.pi, 4 * math.pi) for v in pair)
            if lo > hi:
                raise ValueError("Reversed angle interval")
            box.append((lo - j.error, hi + j.error))
        radii = tuple(self._radii(c) for c in self.capsules)
        pending = [tuple(box)]
        certified_bound, limiting = math.inf, None
        checked = 0
        while pending:
            if checkpoint is not None and checked % 16 == 0:
                checkpoint()  # Keep the ammeter serviced during longer physical preflights.
            if checked >= self.max_boxes:
                return ClearanceResult(
                    False,
                    "clearance_unproven_budget",
                    model_revision=self.revision,
                    required_clearance_mm=self.clearance,
                    boxes_checked=checked,
                )
            current = pending.pop()
            centers = tuple((lo + hi) / 2 for lo, hi in current)
            halves = tuple((hi - lo) / 2 for lo, hi in current)
            clearances = self.clearances(centers)
            checked += 1
            bounds = tuple(
                c - sum(r * h for r, h in zip(rs, halves, strict=True)) - NUMERICAL_MARGIN_MM
                for c, rs in zip(clearances, radii, strict=True)
            )
            index = min(range(len(bounds)), key=bounds.__getitem__)
            if min(clearances) <= self.clearance + NUMERICAL_MARGIN_MM:
                witness = min(range(len(clearances)), key=clearances.__getitem__)
                return ClearanceResult(
                    False,
                    "table_clearance",
                    self.capsules[witness].name,
                    bounds[witness],
                    self.clearance,
                    self.revision,
                    checked,
                )
            if bounds[index] > self.clearance:
                if bounds[index] < certified_bound:
                    certified_bound, limiting = bounds[index], self.capsules[index].name
                continue
            split = max(range(4), key=lambda j: max(rs[j] for rs in radii) * halves[j])
            lo, hi = current[split]
            middle = centers[split]
            if not lo < middle < hi:
                return ClearanceResult(
                    False,
                    "clearance_unproven_precision",
                    self.capsules[index].name,
                    bounds[index],
                    self.clearance,
                    self.revision,
                    checked,
                )
            for interval in ((lo, middle), (middle, hi)):
                child = list(current)
                child[split] = interval
                pending.append(tuple(child))
        return ClearanceResult(
            True, "clear", limiting, certified_bound, self.clearance, self.revision, checked
        )

    def check_pose(self, pulses):
        return self.check_motion(tuple((q, q) for q in self.angles(pulses)))


class MovementSession:
    """Single-owner preflight/write/commit gate using the v1 open-loop assumption.

    Guarded sessions with missing geometry permit only the full neutral startup.
    Manual Jog explicitly opts into free_play, bypassing geometry but not the
    output adapter's pulse validation, current monitoring or fault handling.
    A successful complete write advances the assumed position to the encoded
    target. This is assumed movement success, explicitly not measured feedback.
    No model reload or automatic recovery is allowed in an active session.
    """

    def __init__(
        self, model=None, *, unavailable_reason="movement_geometry_missing", free_play=False
    ):
        if free_play and model is not None:
            raise ValueError("Free play does not use a geometry model")
        self.model = model
        self.free_play = free_play
        self.unavailable_reason = unavailable_reason
        self.commanded = {}
        self.requested = {}
        self.ranges = None
        self.faulted = False
        self.last_result = None
        self.check_current = None

    @classmethod
    def from_config(cls, config):
        data = config.get("arm", {}).get("movement_geometry_data")
        path = config.get("arm", {}).get("movement_geometry")
        if data is None and not path:
            return cls()
        from workbench_common import ROOT

        try:
            model = ArmGeometry.from_dict(
                data if data is not None else json.loads((ROOT / Path(path)).read_text())
            )
            domain = "simulated" if config["mode"] == "mock" else "physical"
            if model.domain != domain:
                raise ValueError("Movement geometry domain does not match operating mode")
            return cls(model)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return cls(unavailable_reason=f"movement_geometry_invalid: {exc}")

    def invalidate(self):
        self.faulted = True
        self.commanded = {}
        self.requested = {}
        self.ranges = None

    def _preflight(self, requested, encoded):
        if not isinstance(requested, dict) or not isinstance(encoded, dict) or not requested:
            raise ValueError("Nonempty channel/pulse mappings required")
        if set(requested) != set(encoded) or any(
            type(c) is not int or c not in CHANNELS for c in requested
        ):
            raise ValueError("Requested and encoded channel mappings must match, channels 0..5")
        for pulse in (*requested.values(), *encoded.values()):
            number(pulse, "pulse_us", 500, 2500)
        if self.faulted:
            return ClearanceResult(False, "unknown_position_after_fault"), None
        startup = not self.commanded
        if startup and (
            set(requested) != CHANNELS or any(p != REFERENCE_US for p in requested.values())
        ):
            return ClearanceResult(False, "establish_all_six_at_1500_us_first"), None
        if self.free_play:
            return ClearanceResult(True, "manual_free_play_no_table_check"), None
        if not self.model:
            if startup:
                return ClearanceResult(True, "verified_1500_us_startup_only"), None
            return ClearanceResult(False, self.unavailable_reason), None
        try:
            targets = self.model.angles({**self.commanded, **encoded})
        except ValueError as exc:
            return ClearanceResult(False, str(exc), model_revision=self.model.revision), None
        ranges = (
            tuple((min(0, q), max(0, q)) for q in targets)
            if startup
            else tuple(
                (min(lo, q), max(hi, q)) for (lo, hi), q in zip(self.ranges, targets, strict=True)
            )
        )
        try:
            return self.model.check_motion(ranges, checkpoint=self.check_current), ranges
        except Exception:
            self.invalidate()
            raise

    def check(self, requested, encoded):
        result, _ = self._preflight(requested, encoded)
        self.last_result = result
        return result

    def move(self, requested, encoded, dispatch):
        result, ranges = self._preflight(requested, encoded)
        self.last_result = result
        if not result.accepted:
            raise MovementRejected(result)
        try:
            dispatch()
        except Exception:
            self.invalidate()
            raise
        self.requested.update(requested)
        self.commanded.update(encoded)
        # V1 deliberately assumes that a successful PWM write completes movement.
        # Preserve error allowances in the model, but do not accumulate old targets.
        self.ranges = (
            tuple((q, q) for q in self.model.angles(self.commanded)) if self.model else ranges
        )
        return result


def main(argv=None):
    """Offline movement check; never imports I2C or sends output."""
    parser = argparse.ArgumentParser(description="Offline ROB0036 table-clearance movement checker")
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument(
        "--calibration", type=Path, required=True, help="Existing PulsePlan calibration"
    )
    parser.add_argument(
        "--target", type=Path, required=True, help="Complete joint-name/pulse JSON pose"
    )
    args = parser.parse_args(argv)
    try:
        from .pca9685 import PulsePlan

        model = ArmGeometry.from_dict(json.loads(args.geometry.read_text()))
        plan = PulsePlan(json.loads(args.calibration.read_text()))
        session = MovementSession(model)
        startup = {name: REFERENCE_US for name in plan.channels}
        for pose in (startup, json.loads(args.target.read_text())):
            commands = plan.encode(pose)
            session.move(
                {p.channel: p.requested_us for p in commands},
                {p.channel: p.commanded_us for p in commands},
                lambda: None,
            )
        result = session.last_result
    except MovementRejected as exc:
        result = exc.result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps({"mode": "dry-run", "domain": model.domain, **asdict(result)}))
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
