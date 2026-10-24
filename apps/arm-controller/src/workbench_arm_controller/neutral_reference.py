"""Recorded neutral electrical evidence; no motion authorization or safety limits."""

import json
import math
from pathlib import Path

from workbench_common import ROOT
from workbench_contracts import Diagnostic, NeutralEvidence, NeutralHoldReference

from .claw_current import reading_is_healthy


class NeutralReference:
    def __init__(self, config):
        self.reference = None
        self.error = "Neutral reference not configured"
        path = config["arm"].get("neutral_reference")
        if not path:
            return
        try:
            ref = NeutralHoldReference.model_validate(json.loads((ROOT / Path(path)).read_text()))
            arm = config["arm"]
            if (
                ref.domain != ("simulated" if config["mode"] == "mock" else "physical")
                or ref.i2c_bus != arm.get("i2c_bus", 1)
                or ref.pwm_address != arm.get("i2c_address", 0x40)
            ):
                raise ValueError(
                    "Neutral reference does not match the selected sensor setup/domain"
                )
            self.reference, self.error = ref, ""
        except (OSError, ValueError) as exc:
            self.error = f"Neutral reference unavailable: {exc}"

    def assess(self, sample, *, commanded_neutral=False):
        ref = self.reference
        result = NeutralEvidence(reference=ref, commanded_neutral=commanded_neutral)
        if not ref:
            result.detail = self.error
        elif not reading_is_healthy(sample):
            result.state, result.detail = (
                "fault",
                "No healthy current sample; neutral comparison unavailable",
            )
        elif sample.power_state == "off":
            result.state, result.detail = (
                "off",
                "Supply off; recorded powered-neutral reference retained",
            )
        elif not commanded_neutral:
            result.state, result.detail = (
                "not_neutral",
                "Neutral targets not currently established; recorded ranges are advisory",
            )
        else:

            def within(value, low, high):
                return value is not None and (
                    low <= value <= high
                    or math.isclose(value, low, rel_tol=0, abs_tol=1e-12)
                    or math.isclose(value, high, rel_tol=0, abs_tol=1e-12)
                )

            magnitude = abs(sample.shunt_mv) if sample.shunt_mv is not None else None
            ends = sorted((abs(ref.shunt_mv.min), abs(ref.shunt_mv.max)))
            # If a future reference crosses zero, zero magnitude is inside its range.
            if ref.shunt_mv.min <= 0 <= ref.shunt_mv.max:
                ends[0] = 0
            matched = within(sample.bus_v, ref.bus_v.min, ref.bus_v.max) and within(
                magnitude, *ends
            )
            result.state = "within_reference" if matched else "outside_reference"
            result.detail = (
                "Within recorded neutral electrical range"
                if matched
                else "Outside recorded neutral range; inspect load/supply. Advisory, not an overcurrent cutoff"
            )
        return result

    def diagnostics(self, sample, *, commanded_neutral=False):
        evidence = self.assess(sample, commanded_neutral=commanded_neutral)
        ref = self.reference
        recorded = Diagnostic(
            component="neutral_reference",
            status=(
                "unavailable"
                if not ref
                else "warning"
                if evidence.state in ("fault", "outside_reference")
                else "ok"
            ),
            summary=evidence.detail,
            measurements={
                "trial_id": ref.trial_id if ref else None,
                "sample_count": ref.sample_count if ref else None,
                "hold_seconds": ref.duration_seconds if ref else None,
                "bus_min_v": ref.bus_v.min if ref else None,
                "bus_max_v": ref.bus_v.max if ref else None,
                "shunt_min_mv": ref.shunt_mv.min if ref else None,
                "shunt_max_mv": ref.shunt_mv.max if ref else None,
                "all_channels_neutral_us": 1500 if ref else None,
                "commanded_neutral": commanded_neutral,
                "motion_limits_commissioned": False,
                "measured_circuit": "Claw/channel 0 only",
            },
        )
        return [recorded]
