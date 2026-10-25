"""Persist capsule-measurement drafts separately from pulse bounds; never move hardware."""

import json
import math

from fastapi import HTTPException
from workbench_common import ROOT
from workbench_contracts import GeometryMeasurements

from .movement import ArmGeometry, MovementSession
from .servo_trial import encode_pulse


class GeometrySetup:
    def __init__(self, config, store, context, context_key):
        self.config, self.store = config, store
        self.context, self.context_key = context, context_key

    def _identity(self):
        context = self.context()
        return self.context_key(context).replace("bounds:", "geometry:"), context.domain

    def _validate(self, geometry, domain, revision):
        candidate = {**geometry, "calibrated": True, "revision": f"measurements-{revision}"}
        try:
            if candidate.get("domain") != domain:
                raise ValueError("Geometry domain does not match this workbench")
            model = ArmGeometry.from_dict(candidate)
            result = MovementSession(model).check(
                dict.fromkeys(range(6), 1500),
                dict.fromkeys(range(6), encode_pulse(1500)[1]),
            )
            if not result.accepted:
                raise ValueError("Reference pose: " + result.reason)
        except (ValueError, TypeError, KeyError) as exc:
            return None, str(exc)
        return candidate, ""

    def read(self):
        key, domain = self._identity()
        saved = self.store.get(key)
        if saved:
            geometry, revision = saved["geometry"], saved["revision"]
        else:
            geometry = json.loads(
                (ROOT / "config/workbench/movement-geometry.example.json").read_text()
            )
            geometry["domain"] = domain
            revision = 0
        _, error = self._validate(geometry, domain, revision)
        return GeometryMeasurements(
            context_key=key,
            domain=domain,
            revision=revision,
            geometry=geometry,
            valid=not error,
            validation_error=error,
            active_revision=saved.get("active_revision") if saved else None,
        )

    def save(self, request):
        current = self.read()
        if request.context_key != current.context_key or request.revision != current.revision:
            raise HTTPException(409, "Measurements or arm setup changed; reload before saving")
        try:
            # Drafts may be incomplete but must remain portable JSON.
            geometry = json.loads(json.dumps(request.geometry, allow_nan=False))
            geometry.setdefault("max_boxes", 4096)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Measurements must contain finite JSON values") from exc
        self._check_draft_shape(geometry, current.domain)
        candidate, error = self._validate(geometry, current.domain, current.revision + 1)
        if request.activate and error:
            raise HTTPException(422, error)
        previous = self.store.get(current.context_key, {})
        record = {
            "revision": current.revision + 1,
            "geometry": candidate if request.activate else geometry,
            "active_revision": current.revision + 1
            if request.activate
            else previous.get("active_revision"),
            "active_geometry": candidate if request.activate else previous.get("active_geometry"),
        }
        # Commit first; a failed save never changes the active configuration.
        self.store.put(current.context_key, record)
        self.apply_active()
        return self.read()

    @staticmethod
    def _check_draft_shape(data, domain):
        def scalar(value):
            return value is None or (type(value) in (int, float) and math.isfinite(value))

        def vector(value):
            return value is None or (
                isinstance(value, list) and len(value) == 3 and all(scalar(v) for v in value)
            )

        joints, capsules = data.get("joints"), data.get("capsules")
        valid = (
            data.get("schema_version") == 1
            and data.get("domain") == domain
            and data.get("reference_pulse_us") == 1500
            and data.get("base_axis_perpendicular_to_table") is True
            and data.get("claw_envelope") == "full_opening_range"
            and isinstance(data.get("revision"), str)
            and type(data.get("calibrated")) is bool
            and type(data.get("max_boxes")) is int
            and scalar(data.get("clearance_mm"))
            and scalar(data.get("geometry_error_mm"))
            and isinstance(joints, list)
            and len(joints) == 4
            and isinstance(capsules, list)
        )
        if valid:
            for channel, joint in zip((4, 3, 2, 1), joints, strict=True):
                if not isinstance(joint, dict):
                    valid = False
                    break
                samples = joint.get("pulse_angle_deg")
                valid &= (
                    joint.get("channel") == channel
                    and vector(joint.get("origin_mm"))
                    and vector(joint.get("axis"))
                    and scalar(joint.get("error_deg"))
                    and isinstance(samples, list)
                    and all(
                        isinstance(pair, list) and len(pair) == 2 and all(scalar(v) for v in pair)
                        for pair in samples
                    )
                )
            valid &= all(
                isinstance(c, dict)
                and isinstance(c.get("name"), str)
                and type(c.get("parent_channel")) is int
                and c["parent_channel"] in (4, 3, 2, 1)
                and vector(c.get("a_mm"))
                and vector(c.get("b_mm"))
                and scalar(c.get("radius_mm"))
                for c in capsules
            )
        if not valid:
            raise HTTPException(422, "Malformed measurement draft; reload the measurement form")

    def apply_active(self):
        key, domain = self._identity()
        saved = self.store.get(key, {})
        active = saved.get("active_geometry")
        self.config["arm"].pop("movement_geometry_data", None)
        if active:
            candidate, error = self._validate(active, domain, saved["active_revision"])
            if error:
                # Do not silently fall back to another model for a corrupt active record.
                self.config["arm"]["movement_geometry_data"] = {}
            else:
                self.config["arm"]["movement_geometry_data"] = candidate
