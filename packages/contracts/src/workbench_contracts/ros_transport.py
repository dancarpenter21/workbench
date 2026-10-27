"""Lossless ROS representations of the existing commissioning contracts.

Motion planning uses standard trajectory interfaces. These generated messages
preserve the operator commissioning API, including explicit optional values.
ROS is imported only when encoding or decoding an actual message.
"""

import calendar
import importlib
import inspect
import json
import math
import types
import typing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from uuid import UUID

from pydantic import BaseModel, TypeAdapter


class RemoteControlError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FieldType:
    kind: str
    ros_type: str
    annotation: object
    optional: bool = False
    child: object = None


@lru_cache(maxsize=None)
def field_type(annotation):
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        values = typing.get_args(annotation)
        other = [value for value in values if value is not type(None)]
        if len(other) == 1 and len(other) != len(values):
            base = field_type(other[0])
            return FieldType(base.kind, base.ros_type, base.annotation, True, base.child)
    if origin is typing.Literal:
        values = typing.get_args(annotation)
        if len({type(value) for value in values}) == 1:
            return field_type(type(values[0]))
    if annotation is bool:
        return FieldType("bool", "bool", annotation)
    if annotation is int:
        return FieldType("int", "int64", annotation)
    if annotation is float:
        return FieldType("float", "float64", annotation)
    if annotation in (str, UUID):
        return FieldType("str", "string", annotation)
    if annotation is bytes:
        return FieldType("bytes", "uint8[]", annotation)
    if annotation is datetime:
        return FieldType("datetime", "builtin_interfaces/Time", annotation)
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return FieldType("model", f"workbench_interfaces/Api{annotation.__name__}", annotation)
    if origin in (list, tuple):
        arguments = typing.get_args(annotation)
        if arguments and (len(arguments) == 1 or all(a == arguments[0] for a in arguments)):
            child = field_type(arguments[0])
            if not child.optional and not child.ros_type.endswith("[]") and child.kind != "json":
                return FieldType("list", child.ros_type + "[]", annotation, child=child)
    # Flexible draft records and metadata remain JSON, validated by their original types.
    return FieldType("json", "string", annotation)


@lru_cache(maxsize=None)
def adapter(annotation):
    return TypeAdapter(annotation)


def ros_field(name, spec):
    return name + "_json" if spec.kind == "json" else name


def message_fields(model):
    for name, value in model.model_fields.items():
        yield name, field_type(value.annotation)


def create_message(name):
    package, name = name.split("/", 1) if "/" in name else ("workbench_interfaces", name)
    return getattr(importlib.import_module(package + ".msg"), name)()


def require_finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    if isinstance(value, dict):
        for key, entry in value.items():
            require_finite_json(key)
            require_finite_json(entry)
    elif isinstance(value, (list, tuple)):
        for entry in value:
            require_finite_json(entry)


def encode(value, spec, factory=create_message):
    if spec.kind == "model":
        return to_ros(value, factory=factory)
    if spec.kind == "list":
        return [encode(entry, spec.child, factory) for entry in value]
    if spec.kind == "json":
        require_finite_json(value)
        return json.dumps(adapter(spec.annotation).dump_python(value, mode="json"), allow_nan=False)
    if spec.kind == "datetime":
        if value.tzinfo is None:
            raise ValueError("ROS timestamps require an explicit timezone")
        message = factory("builtin_interfaces/Time")
        message.sec, message.nanosec = (
            calendar.timegm(value.utctimetuple()),
            value.microsecond * 1000,
        )
        return message
    if spec.kind == "bytes":
        return list(value)
    if spec.kind == "int":
        if not -(2**63) <= value < 2**63:
            raise ValueError("Value is outside the ROS int64 range")
        return int(value)
    if spec.kind == "float":
        return float(value)
    if spec.kind == "bool":
        return bool(value)
    return str(value)


def decode(value, spec):
    if spec.kind == "model":
        return from_ros(value, spec.annotation)
    if spec.kind == "list":
        return [decode(entry, spec.child) for entry in value]
    if spec.kind == "json":

        def invalid_constant(value):
            raise ValueError(f"Non-finite JSON constant: {value}")

        require_finite_json(json.loads(value, parse_constant=invalid_constant))
        return adapter(spec.annotation).validate_json(value)
    if spec.kind == "datetime":
        return datetime.fromtimestamp(value.sec, timezone.utc) + timedelta(
            microseconds=value.nanosec // 1000
        )
    if spec.kind == "bytes":
        return bytes(value)
    return value


def to_ros(value, *, factory=create_message):
    message = factory("Api" + type(value).__name__)
    for name, spec in message_fields(type(value)):
        field = getattr(value, name)
        if spec.optional:
            setattr(message, "has_" + name, field is not None)
            if field is None:
                continue
        setattr(message, ros_field(name, spec), encode(field, spec, factory))
    return message


def from_ros(message, model):
    values = {}
    for name, spec in message_fields(model):
        values[name] = (
            None
            if spec.optional and not getattr(message, "has_" + name)
            else decode(getattr(message, ros_field(name, spec)), spec)
        )
    return model.model_validate(values)


def controller_bindings(app):
    """Use the existing guarded handlers directly; no internal HTTP server is started."""
    from fastapi.routing import APIRoute

    bindings = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path == "/execute":
            continue  # Retrieval exclusively uses the planned action, never legacy recipes.
        if route.response_model is None:
            continue
        parameters = inspect.signature(route.endpoint).parameters
        annotations = typing.get_type_hints(route.endpoint)
        bindings.append(
            {
                "name": "Control"
                + "".join(word.capitalize() for word in route.endpoint.__name__.split("_")),
                "method": next(iter(route.methods)),
                "path": route.path,
                "endpoint": route.endpoint,
                "parameters": {
                    name: field_type(annotations.get(name, param.annotation))
                    for name, param in parameters.items()
                },
                "result": field_type(route.response_model),
            }
        )
    return bindings
