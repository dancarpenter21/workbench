"""Export all application APIs without starting devices, loading models, or opening state."""

import json
from importlib import import_module

from workbench_common import ROOT, settings

MODULES = {
    "vision": "workbench_vision",
    "voice": "workbench_voice",
    "assistant": "workbench_assistant",
    "arm-controller": "workbench_arm_controller",
    "coordinator": "workbench_coordinator",
}


def main():
    config = settings()
    config["mode"] = "mock"
    combined = {
        "openapi": "3.1.0",
        "info": {"title": "Robotic Workbench", "version": "0.1.0"},
        "paths": {},
        "components": {"schemas": {}},
    }
    for name, module in MODULES.items():
        spec = import_module(f"{module}.app").create_app(config=config).openapi()
        for path, operations in spec["paths"].items():
            for operation in operations.values():
                operation["operationId"] = name.replace("-", "_") + "_" + operation["operationId"]
            combined["paths"][f"/{name}{path}"] = operations
        for name, schema in spec["components"]["schemas"].items():
            existing = combined["components"]["schemas"].get(name)
            if existing is not None and existing != schema:
                raise ValueError(f"Conflicting shared schema: {name}")
            combined["components"]["schemas"][name] = schema
    (ROOT / "packages/contracts/openapi.json").write_text(json.dumps(combined, indent=2) + "\n")


if __name__ == "__main__":
    main()
