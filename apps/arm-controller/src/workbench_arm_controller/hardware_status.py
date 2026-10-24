"""Read-only projections of the service-owned monitors; never performs device I/O."""

from workbench_contracts import ArmHardwareStatus, Diagnostic

from .claw_current import reading_is_healthy


class HardwareStatus:
    def __init__(self, config, monitor, hat, neutral):
        self.config = config
        self.monitor = monitor
        self.hat = hat
        self.neutral = neutral

    def snapshot(self, *, commanded_neutral=False):
        sample = self.monitor.status()
        hat = self.hat.status()
        evidence = self.neutral.assess(sample, commanded_neutral=commanded_neutral)
        valid = reading_is_healthy(sample)
        reading = sample.reading_state or ("ok" if valid else "unavailable")
        power = {"off": "Powered off", "energized": "Powered", "unknown": "Power unknown"}
        current = Diagnostic(
            component="claw_current",
            status="ok" if valid else "error" if reading == "error" else "unavailable",
            summary=f"Sensor responding · {power[sample.power_state]}"
            if valid
            else {
                "error": "Sensor fault",
                "stale": "Updates delayed",
            }.get(reading, "Sensor unavailable"),
            measurements={
                "bus_v": sample.bus_v,
                "raw_shunt_mv": sample.shunt_mv,
                "current_a": sample.current_a,
                "scale_verified": sample.scale_verified,
                "ampere_scale_and_2_5_a_cutoff": "verified"
                if sample.scale_verified
                else "UNVERIFIED",
                "power_state": sample.power_state if valid else "unknown",
                "reading_state": reading,
                "reading_healthy": valid,
                "sample_age_seconds": sample.age_seconds,
                "monitor_session_id": sample.monitor_session_id,
                "sample_sequence": sample.sample_sequence,
                "reset_required": sample.reset_required,
                "fault": sample.fault,
                "simulated": sample.simulated,
            },
        )
        return ArmHardwareStatus(
            mode=self.config["mode"],
            claw_current=sample,
            servo_hat=hat,
            neutral_evidence=evidence,
            checks=[
                Diagnostic(
                    component="hat",
                    status={"detected": "ok", "error": "error", "unknown": "unavailable"}[
                        hat.state
                    ],
                    summary=hat.detail,
                    checked_at=hat.checked_at or current.checked_at,
                    measurements={
                        "bus": hat.i2c_bus,
                        "address": hat.address,
                        "age_seconds": hat.age_seconds,
                        "simulated": hat.simulated,
                        "physical_position": None,
                    },
                ),
                current,
                Diagnostic(
                    component="motion_interlock",
                    status="warning" if sample.reset_required else "ok",
                    summary="Motion inhibited — reset required"
                    if sample.reset_required
                    else "No latched current-monitor fault",
                    measurements={"reset_required": sample.reset_required, "reason": sample.fault},
                ),
                *self.neutral.diagnostics(sample, commanded_neutral=commanded_neutral),
            ],
        )
