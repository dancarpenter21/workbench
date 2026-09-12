"""Canonical API types; FastAPI exports these as OpenAPI for TypeScript clients."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

WAV_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Error(Contract):
    code: str
    message: str


class Health(Contract):
    service: str
    mode: str
    ready: bool
    detail: str = ""


class Tool(Contract):
    id: str
    name: str
    aliases: list[str]


class TextRequest(Contract):
    text: str = Field(min_length=1, max_length=1000)
    operation_id: UUID = Field(default_factory=uuid4)


class Intent(Contract):
    action: Literal["retrieve_tool", "get_status", "clarify"]
    tool_id: str | None = None
    message: str = ""


class Transcript(Contract):
    text: str


class Observation(Contract):
    tool_id: str
    location: Literal["source", "tray", "missing", "uncertain"]
    confidence: float = Field(ge=0, le=1)


class Observations(Contract):
    captured_at: datetime
    tools: list[Observation]
    tray_clear: bool
    camera_ok: bool


class MockScene(Contract):
    tools: list[Observation]
    tray_clear: bool = True
    camera_ok: bool = True
    age_seconds: float = Field(default=0, ge=0, le=3600)


class ArmCommand(Contract):
    operation_id: UUID
    tool_id: str


class ArmStatus(Contract):
    state: Literal["idle", "moving", "completed", "fault", "stopped"]
    operation_id: UUID | None = None
    tool_id: str | None = None
    message: str = ""
    recovery_required: bool = False


OperationState = Literal[
    "interpreting",
    "checking",
    "moving",
    "verifying",
    "completed",
    "clarification",
    "rejected",
    "failed",
    "stopped",
    "status",
]
TERMINAL = {"completed", "clarification", "rejected", "failed", "stopped", "status"}


class Operation(Contract):
    id: UUID
    text: str
    state: OperationState = "interpreting"
    tool_id: str | None = None
    message: str = "Interpreting request"
    updated_at: datetime = Field(default_factory=now)


class WorkbenchStatus(Contract):
    mode: str
    operation: Operation | None
    recovery_required: bool
    message: str = ""


class Recovery(Contract):
    acknowledged: Literal[True]
