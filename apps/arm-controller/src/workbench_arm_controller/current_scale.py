"""Operator-attested scale records; never derives resistance from servo readings."""

from uuid import uuid4

from fastapi import HTTPException
from workbench_contracts import CurrentScaleState, CurrentScaleVerification, now


class CurrentScaleRecords:
    def __init__(self, monitor):
        self.monitor = monitor
        self.store = None

    def identity(self):
        m = self.monitor
        return ("simulated" if m.simulated else "physical", m.bus_number, 65, m.pwm_address)

    def matches(self, record):
        return (
            record.domain,
            record.i2c_bus,
            record.sensor_address,
            record.pwm_address,
        ) == self.identity()

    def attach(self, store):
        self.store = store
        active = store.get("current_scale:active")
        if active:
            try:
                record = CurrentScaleVerification.model_validate(
                    store.get("current_scale:" + active)
                )
                if not self.matches(record):
                    raise ValueError("Active current scale does not match sensor setup/domain")
                self.install(record)
            except Exception as exc:
                self.monitor.config_error = True
                self.monitor.verified = False
                self.monitor.shunt = None
                self.monitor.trip(f"Invalid active current scale: {exc}")

    def status(self):
        return CurrentScaleState(
            records=[
                CurrentScaleVerification.model_validate(self.store.get("current_scale:" + key))
                for key in self.store.get("current_scale:records", [])
            ],
            active_id=self.store.get("current_scale:active"),
            source="web" if self.store.get("current_scale:active") else "configuration",
        )

    def save(self, record):
        if not self.matches(record):
            raise HTTPException(
                422, "Verification must match this sensor setup and evidence domain"
            )
        for name in ("assembly_id", "observer", "method", "instrumentation", "evidence"):
            if not getattr(record, name).strip():
                raise HTTPException(422, f"{name} must not be blank")
        if record.observed_at.utcoffset() is None:
            raise HTTPException(422, "Verification timestamp must include timezone")
        saved = record.model_copy(update={"id": str(uuid4())})
        self.store.put_many(
            {
                "current_scale:" + saved.id: saved.model_dump(mode="json"),
                "current_scale:records": [*self.store.get("current_scale:records", []), saved.id],
            }
        )
        return saved

    def install(self, record):
        self.monitor.shunt = record.shunt_ohms
        self.monitor.evidence = f"Web verification {record.id}: {record.evidence}"
        self.monitor.verified = True
        self.monitor.config_error = False
        self.monitor.active_assembly_id = record.assembly_id
        # Preserve any existing fault/recovery requirement.
        self.monitor.last = None
        self.monitor.sample = {}
        self.monitor.sampled_at = None

    def apply(self, request, disable_outputs):
        data = self.store.get("current_scale:" + request.verification_id)
        if not data:
            raise HTTPException(404, "Verification not found")
        record = CurrentScaleVerification.model_validate(data)
        if not self.matches(record):
            raise HTTPException(409, "Verification does not match current sensor setup/domain")
        monitor = self.monitor
        if not monitor.poll(force=True):
            raise HTTPException(503, monitor.fault)
        if monitor.last is None or monitor.clock() - monitor.last >= 0.3:
            raise HTTPException(503, "Fresh sensor reading required")
        if not monitor.simulated and monitor.sample["bus_v"] > 0.5:
            raise HTTPException(409, "Disconnect ALL servo power before applying a scale")
        disable_outputs()
        self.store.put_many(
            {
                "current_scale:active": record.id,
                "current_scale:activation:" + str(uuid4()): {
                    "verification_id": record.id,
                    "power_off_and_supported": True,
                    "at": now().isoformat(),
                },
            }
        )
        self.install(record)
        if not monitor.poll(force=True):
            raise RuntimeError(monitor.fault)
        return self.status()
