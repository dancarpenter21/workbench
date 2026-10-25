"""Canonical API types; FastAPI exports these as OpenAPI for TypeScript clients."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class OffCurrentBounds(Contract):
    baseline_id: str = Field(min_length=1, max_length=200)
    domain: Literal["physical", "simulated"]
    i2c_bus: int = Field(ge=0)
    sensor_address: Literal[65] = 65
    pwm_address: int = Field(ge=64, le=119)
    max_bus_v: float = Field(gt=0, le=0.5)
    max_abs_shunt_mv: float = Field(gt=0, le=0.5)
    evidence: str = Field(min_length=1)


class ClawCurrentStatus(Contract):
    reading_state: Literal["ok", "error", "stale", "unavailable"] | None = None
    reset_required: bool = False
    reading_healthy: bool | None = None
    monitor_session_id: str | None = None
    sample_sequence: int = 0
    i2c_bus: int | None = None
    sensor_address: int = 65
    pwm_address: int | None = None
    power_state: Literal["off", "energized", "unknown"] = "unknown"
    off_bounds: OffCurrentBounds | None = None
    off_baseline_error: str = ""
    simulated: bool = True
    healthy: bool = False
    fault: str = ""
    sampled_at: datetime | None = None
    age_seconds: float | None = None
    bus_v: float | None = None
    shunt_mv: float | None = None
    current_a: float | None = None
    scale_verified: bool = False
    shunt_ohms: float | None = None
    verification_evidence: str = ""
    cutoff_a: float = 2.5


class ReferenceStatistics(Contract):
    min: float
    max: float
    mean: float
    median: float
    stddev: float = Field(ge=0)
    p95: float
    p99: float

    @model_validator(mode="after")
    def ordered(self):
        if not self.min <= self.mean <= self.max:
            raise ValueError("Reference statistics must be ordered")
        return self


class NeutralHoldReference(Contract):
    schema_version: Literal[1] = 1
    trial_id: str = Field(min_length=1)
    domain: Literal["physical", "simulated"]
    i2c_bus: int = Field(ge=0)
    sensor_address: Literal[65] = 65
    pwm_address: int = Field(ge=64, le=119)
    targets_us: dict[str, Literal[1500]]
    nominal_encoded_us: float
    scale_verified: Literal[False] = False
    pwm_full_off_verified: Literal[True]
    measured_circuit: str
    duration_seconds: float = Field(gt=0)
    sample_count: int = Field(gt=0)
    bus_v: ReferenceStatistics
    shunt_mv: ReferenceStatistics
    report_path: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def six_neutral_channels(self):
        if set(self.targets_us) != {str(n) for n in range(6)}:
            raise ValueError("Neutral evidence requires all six channels")
        return self


class NeutralEvidence(Contract):
    reference: NeutralHoldReference | None = None
    state: Literal[
        "unavailable", "fault", "off", "not_neutral", "within_reference", "outside_reference"
    ] = "unavailable"
    detail: str = "Neutral reference not configured"
    commanded_neutral: bool = False


class ManualJoint(Contract):
    id: str
    label: str
    channel: int = Field(ge=0, le=5)
    min_us: float
    max_us: float
    max_rate_us_per_second: float = Field(gt=0)
    direction: str
    commanded_us: float | None = None


class PeripheralStatus(Contract):
    state: Literal["detected", "error", "unknown"] = "unknown"
    simulated: bool = False
    i2c_bus: int | None = None
    address: int
    checked_at: datetime | None = None
    age_seconds: float | None = None
    detail: str = ""


class Diagnostic(Contract):
    component: str
    status: Literal["ok", "warning", "error", "unavailable"]
    checked_at: datetime = Field(default_factory=now)
    summary: str
    measurements: dict[str, str | float | bool | None] = Field(default_factory=dict)


class ArmHardwareStatus(Contract):
    mode: str
    checked_at: datetime = Field(default_factory=now)
    claw_current: ClawCurrentStatus
    servo_hat: PeripheralStatus
    neutral_evidence: NeutralEvidence
    checks: list[Diagnostic] = Field(default_factory=list)


class ManualStatus(Contract):
    servo_hat: PeripheralStatus | None = None
    neutral_evidence: NeutralEvidence | None = None
    claw_current: ClawCurrentStatus | None = None
    simulated: bool
    available: bool = False
    prepared: bool = False
    active: bool = False
    recovery_required: bool = False
    message: str = ""
    joints: list[ManualJoint] = Field(default_factory=list)


class ManualPrepare(Contract):
    power_disconnected_and_arm_supported: Literal[True]


class ManualAcquire(Contract):
    ready_acknowledged: Literal[True]


class ManualSession(Contract):
    token: UUID
    status: ManualStatus


class ManualRelease(Contract):
    token: UUID


class ManualInput(ManualRelease):
    sequence: int = Field(ge=0, strict=True)
    joint_id: str
    speed: float = Field(ge=-1, le=1, strict=True)


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


class CommissionObservation(Contract):
    requested_us: float
    encoded_us: float
    observed_at: datetime
    notes: str = ""


class CommissionFeedbackRecord(Contract):
    issued_at: datetime | None = None
    written_at: datetime | None = None
    command_id: str
    joint_id: str
    requested_us: float
    encoded_us: float
    previous_us: float
    outcome: Literal["settled_clear", "uncertain", "unsafe"]
    observed_direction: Literal["positive", "negative", "none", "unknown"]
    physical_frame: str = Field(min_length=1, max_length=500)
    notes: str = Field(default="", max_length=2000)
    observed_at: datetime = Field(default_factory=now)


class CommissionAttempt(Contract):
    current_scale: dict = Field(default_factory=dict)
    telemetry_reference: str | None = None
    id: str
    trial_id: str
    joint_id: str
    observer: str
    conditions: str
    started_at: datetime = Field(default_factory=now)
    ended_at: datetime | None = None
    state: Literal["in_progress", "observed", "needs_review"] = "in_progress"
    observations: dict[str, CommissionObservation] = Field(default_factory=dict)
    feedback: list[CommissionFeedbackRecord] = Field(default_factory=list)
    direction: str = ""
    notes: str = ""


class CommissionJoint(Contract):
    id: str
    channel: int
    state: Literal["untested", "in_progress", "observed", "needs_review"] = "untested"
    observations: dict[str, CommissionObservation] = Field(default_factory=dict)
    direction: str = ""
    notes: str = ""
    attempts: list[CommissionAttempt] = Field(default_factory=list)
    selected_attempt_id: str | None = None


class CommissionEvent(Contract):
    at: datetime
    trial_id: str | None = None
    joint_id: str | None = None
    action: str
    detail: str


class CommissionSetup(Contract):
    assembly_condition: Literal["factory_assembled_unchanged"] = "factory_assembled_unchanged"
    backend: Literal["mock", "pca9685"]
    i2c_bus: int | None = None
    i2c_address: int | None = None
    initial_us: Literal[1500] = 1500
    trial_min_us: Literal[1450] = 1450
    trial_max_us: Literal[1550] = 1550
    step_us: Literal[10] = 10
    nominal_reference_clock_hz: Literal[25000000] = 25000000
    prescale: Literal[121] = 121
    position_feedback: Literal[False] = False


class CommissionPulseBound(CommissionObservation):
    source: Literal["observed", "manual"] = "observed"
    trial_id: str
    command_id: str


class CommissionJointBounds(Contract):
    minimum: CommissionPulseBound | None = None
    maximum: CommissionPulseBound | None = None


class CommissionBounds(Contract):
    domain: Literal["physical", "simulated"]
    assembly_id: str
    setup: CommissionSetup
    joints: dict[str, CommissionJointBounds]


class CommissionLimitValues(Contract):
    minimum_us: float | None = Field(default=None, ge=500, le=1500)
    maximum_us: float | None = Field(default=None, ge=1500, le=2500)

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum_us is None and self.maximum_us is None:
            raise ValueError("Provide at least one limit")
        if self.minimum_us is not None and self.maximum_us is not None:
            if self.minimum_us >= self.maximum_us:
                raise ValueError("Minimum must be below maximum")
        return self


class CommissionBoundsUpdate(Contract):
    assembly_id: str
    setup: CommissionSetup
    joints: dict[str, CommissionLimitValues]


CommissionChecklistStep = Literal[
    "hardware", "startup", "directions", "speeds", "recovery", "configuration"
]


class CommissionChecklist(Contract):
    context_key: str
    completed: dict[CommissionChecklistStep, bool] = Field(default_factory=dict)
    updated_at: datetime | None = None


class CommissionReadiness(Contract):
    ready: bool


class GeometryMeasurements(Contract):
    context_key: str
    domain: Literal["physical", "simulated"]
    revision: int = 0
    geometry: dict
    valid: bool = False
    validation_error: str = ""
    active_revision: int | None = None


class GeometryMeasurementsUpdate(Contract):
    context_key: str
    revision: int = Field(ge=0)
    geometry: dict
    activate: bool = False


class CommissionChecklistUpdate(Contract):
    context_key: str
    step: CommissionChecklistStep
    completed: bool


class CommissionDraft(Contract):
    schema_version: Literal[1] = 1
    id: str
    revision: int = 0
    domain: Literal["physical", "simulated"]
    assembly_id: str
    observer: str
    setup: CommissionSetup | None = None
    joints: list[CommissionJoint]
    history: list[CommissionEvent] = Field(default_factory=list)


class CommissionPendingCommand(Contract):
    command_id: str
    joint_id: str
    previous_us: float
    requested_us: float
    encoded_us: float | None = None
    issued_at: datetime
    written_at: datetime | None = None


class CommissionStatus(Contract):
    capture_command_id: str | None = None
    can_resume_powered: bool = False
    max_move_us: int = 10
    capture_ready: bool = False
    trial_type: Literal["isolated", "all_holding"] = "isolated"
    targets: dict[str, float] = Field(default_factory=dict)
    encoded_targets: dict[str, float] = Field(default_factory=dict)
    windows: dict[str, list[float]] = Field(default_factory=dict)
    claw_current: ClawCurrentStatus | None = None
    available: bool = False
    simulated: bool = True
    reserved: bool = False
    pending_command: CommissionPendingCommand | None = None
    stage: Literal[
        "idle", "acquired", "prepared", "ready", "awaiting_feedback", "awaiting_power_off"
    ] = "idle"
    trial_id: str | None = None
    draft_id: str | None = None
    joint_id: str | None = None
    requested_us: float | None = None
    encoded_us: float | None = None
    message: str = ""


class CommissionCreate(Contract):
    assembly_id: str = Field(min_length=1, max_length=200)
    observer: str = Field(min_length=1, max_length=200)


class CommissionEdit(Contract):
    revision: int = Field(ge=0)
    joint_id: str
    direction: str = Field(max_length=500)
    notes: str = Field(max_length=4000)


class CommissionAcquire(Contract):
    observer: str | None = Field(default=None, min_length=1, max_length=200)
    conditions: str = Field(default="Supported bench trial", min_length=1, max_length=2000)
    trial_type: Literal["isolated", "all_holding"] = "isolated"
    draft_id: str
    joint_id: str


class CommissionSession(Contract):
    token: str
    status: CommissionStatus


class CommissionCommand(Contract):
    token: str
    sequence: int = Field(ge=0, strict=True)


class CommissionPowerOffReset(Contract):
    # Accept older clients while allowing explicit reset without cycling servo power.
    power_off_and_supported: Literal[True] | None = None
    fault_reviewed: Literal[True] | None = None

    @model_validator(mode="after")
    def require_reset_acknowledgment(self):
        if not (self.fault_reviewed or self.power_off_and_supported):
            raise ValueError("Explicit operator fault acknowledgment required")
        return self


class CommissionPrepare(CommissionCommand):
    power_off_supported_isolated: Literal[True] | None = None
    power_off_and_supported: Literal[True] | None = None

    @model_validator(mode="after")
    def require_power_off(self):
        if not (self.power_off_supported_isolated or self.power_off_and_supported):
            raise ValueError("Power-off and support acknowledgment required")
        return self


class CommissionReady(CommissionCommand):
    settled_and_clear: Literal[True]


class CommissionStep(CommissionCommand):
    direction: int = Field(ge=-1, le=1, strict=True)


class CommissionTarget(CommissionCommand):
    joint_id: str
    pulse_us: float = Field(ge=500, le=2500, allow_inf_nan=False)
    assume_ok: bool = False


class CommissionSelect(CommissionCommand):
    joint_id: str


class CommissionWindow(CommissionSelect):
    min_us: float = Field(ge=500, le=2500, allow_inf_nan=False)
    max_us: float = Field(ge=500, le=2500, allow_inf_nan=False)
    reviewed: Literal[True]


class CommissionMark(CommissionCommand):
    joint_id: str | None = None
    command_id: str | None = None
    kind: Literal["initial", "minimum", "maximum"]
    expected_requested_us: float
    settled_and_clear: Literal[True]
    notes: str = Field(default="", max_length=2000)


class CommissionStop(Contract):
    trial_id: str


class CommissionFeedback(CommissionCommand):
    command_id: str
    joint_id: str
    expected_requested_us: float
    outcome: Literal["settled_clear", "uncertain", "unsafe"]
    observed_direction: Literal["positive", "negative", "none", "unknown"]
    physical_frame: str = Field(min_length=1, max_length=500)
    notes: str = Field(default="", max_length=2000)


class CommissionAttemptSelect(Contract):
    revision: int = Field(ge=0)
    joint_id: str
    attempt_id: str


class CurrentScaleVerification(Contract):
    id: str = ""
    domain: Literal["physical", "simulated"]
    assembly_id: str = Field(min_length=1, max_length=200)
    observer: str = Field(min_length=1, max_length=200)
    method: str = Field(min_length=1, max_length=2000)
    instrumentation: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)
    observed_at: datetime
    i2c_bus: int = Field(ge=0)
    sensor_address: Literal[65] = 65
    pwm_address: int = Field(ge=0, le=127)
    shunt_ohms: float = Field(gt=0, allow_inf_nan=False)
    externally_verified: Literal[True]


class CurrentScaleApply(Contract):
    verification_id: str
    power_off_and_supported: Literal[True]


class CurrentScaleState(Contract):
    records: list[CurrentScaleVerification] = Field(default_factory=list)
    active_id: str | None = None
    source: Literal["configuration", "web"] = "configuration"


class CommissionFinish(CommissionStop):
    reviewed_joint_ids: list[str] = Field(default_factory=list)
    power_off_and_supported: Literal[True]
    observations_reviewed: bool = False


class CommissionExport(Contract):
    schema_version: Literal[2] = 2
    telemetry: list[dict] = Field(default_factory=list)
    record: CommissionDraft


class WorkbenchStatus(Contract):
    hardware: ArmHardwareStatus | None = None
    servo_hat: PeripheralStatus | None = None
    neutral_evidence: NeutralEvidence | None = None
    claw_current: ClawCurrentStatus | None = None
    mode: str
    commissioning_ready: bool | None = None
    retrieval_enabled: bool = True
    operation: Operation | None
    recovery_required: bool
    message: str = ""
    manual: ManualStatus | None = None
    commissioning: CommissionStatus | None = None


class Recovery(Contract):
    acknowledged: Literal[True]


class DiagnosticsReport(Contract):
    hardware: ArmHardwareStatus | None = None
    neutral_evidence: NeutralEvidence | None = None
    checked_at: datetime = Field(default_factory=now)
    mode: str
    retrieval_enabled: bool
    checks: list[Diagnostic]


class MicrophoneDevice(Contract):
    id: str
    name: str


class MicrophoneTest(Contract):
    device_id: str = Field(min_length=1, max_length=100)


class MicrophoneResult(Contract):
    device_id: str
    duration_seconds: float
    sample_rate: int
    peak: float
    rms: float
    clipped: bool
    wav_base64: str
    simulated: bool = False


class VideoOffer(Contract):
    sdp: str = Field(min_length=1, max_length=65536)
    type: Literal["offer"] = "offer"


class VideoAnswer(Contract):
    sdp: str
    type: Literal["answer"] = "answer"
    session_id: str
    simulated: bool
    target_fps: float
