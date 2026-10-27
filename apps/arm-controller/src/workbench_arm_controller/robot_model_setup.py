"""Assembly-scoped ROS model drafts; validation never authorizes physical output."""

import json
import math

from fastapi import HTTPException
from workbench_common import ROOT
from workbench_common.robot_model import validate_model
from workbench_contracts import RobotModelMeasurements


class RobotModelSetup:
    def __init__(self, config, store, context, context_key):
        self.config, self.store = config, store
        self.context, self.context_key = context, context_key

    def template(self):
        name = "simulated.json" if self.config["mode"] == "mock" else "physical.example.json"
        return json.loads((ROOT / "ros/src/workbench_description/config" / name).read_text())

    def read(self):
        context = self.context()
        key = self.context_key(context).replace("bounds:", "robot-model:")
        record = self.store.get(key, {})
        model = record.get("model", self.template())
        error = ""
        try:
            validate_model(model, mode=self.config["mode"])
        except ValueError as exc:
            error = str(exc)
        return RobotModelMeasurements(
            context_key=key,
            domain=context.domain,
            revision=record.get("revision", 0),
            model=model,
            valid=not error,
            validation_error=error,
        )

    def save(self, request):
        current = self.read()
        if request.context_key != current.context_key or request.revision != current.revision:
            raise HTTPException(409, "Robot model or arm setup changed; reload before saving")
        model = request.model
        template = self.template()

        def scalar(value):
            return value is None or (type(value) in (int, float) and math.isfinite(value))

        def vector(value):
            return value is None or (
                isinstance(value, list) and len(value) == 3 and all(scalar(v) for v in value)
            )

        valid = (
            type(model.get("schema_version")) is int
            and model.get("schema_version") == 1
            and model.get("domain") == current.domain
            and isinstance(model.get("complete"), bool)
            and isinstance(model.get("revision"), str)
        )
        for kind in ("links", "joints"):
            entries, expected = model.get(kind), template[kind]
            if not isinstance(entries, list) or len(entries) != len(expected):
                valid = False
                continue
            for entry, reference in zip(entries, expected, strict=True):
                if not isinstance(entry, dict) or entry.get("name") != reference["name"]:
                    valid = False
                    continue
                for key in ("size", "xyz", "rpy", "axis"):
                    if (key in reference or key in entry) and not vector(entry.get(key)):
                        valid = False
                for key in ("lower", "upper", "effort", "velocity"):
                    if key in reference and not scalar(entry.get(key)):
                        valid = False
                for key in ("parent", "child", "channel", "type"):
                    if key in reference and entry.get(key) != reference[key]:
                        valid = False
        if not valid:
            raise HTTPException(422, "Robot model draft has invalid structure or evidence domain")
        try:
            portable = json.loads(json.dumps(model, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Robot model must contain finite JSON values") from exc
        self.store.put(current.context_key, {"revision": current.revision + 1, "model": portable})
        return self.read()
