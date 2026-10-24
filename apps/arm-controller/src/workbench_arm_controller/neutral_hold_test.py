"""Supervised fixed-neutral hold with continuous INA219 monitoring and durable logs.

Requires disconnected power for preparation. No jogging, sweep, automatic retry,
or return-to-neutral after a fault. Existing short interactive tests stay unchanged.
"""

import argparse
import json
import math
import os
import signal
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from workbench_common import ROOT, Store

from .base_servo_test import interrupted
from .claw_current import ClawCurrentMonitor
from .device_lock import DeviceLock
from .neutral_servo_test import disable_outputs, prepare_neutral
from .off_baseline import distribution
from .servo_trial import encode_pulse


def run(
    config,
    directory,
    *,
    duration=1800,
    await_power_seconds=120,
    store=None,
    bus_factory=None,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """Caller explicitly confirms power-off preparation, supported arm and supervision."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    monitor = ClawCurrentMonitor(config, clock=clock, bus_factory=bus_factory)
    if monitor.simulated:
        raise ValueError("Physical neutral hold requires hardware configuration")
    if store:
        monitor.attach(store)
    monitor.stream = "neutral_hold:" + directory.name
    raw_path = directory / "samples.jsonl"
    report = {
        "schema_version": 1,
        "trial_id": directory.name,
        "domain": "physical",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "requested_hold_seconds": duration,
        "i2c_bus": monitor.bus_number,
        "sensor_address": 0x41,
        "pwm_address": monitor.pwm_address,
        "targets_us": dict.fromkeys(range(6), 1500),
        "nominal_encoded_us": encode_pulse(1500)[1],
        "position_feedback": False,
        "measured_circuit": "Claw/channel 0 only; other servo currents are not measured",
        "scale_verified": monitor.verified,
        "current_a_and_2_5_a_cutoff": "verified" if monitor.verified else "UNVERIFIED",
        "shunt_ohms": monitor.shunt,
        "shunt_evidence": monitor.evidence,
    }
    held = []
    counts = Counter()
    phase = "power_off_check"
    previous = None
    last_sync = clock()
    bus = None
    with raw_path.open("x", buffering=1) as log:

        def event(kind, **values):
            log.write(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "trial_id": directory.name,
                        "phase": phase,
                        "record_type": kind,
                        **values,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
            log.flush()

        def sample():
            nonlocal previous, last_sync
            started = clock()
            if previous is not None and started - previous >= 0.3:
                raise RuntimeError("INA219 polling gap exceeded 300 ms")
            if not monitor.poll(force=True):
                raise RuntimeError(monitor.fault)
            monitor.require()
            previous = started
            value = monitor.status().model_dump(mode="json")
            event("sample", reading=value)
            counts[phase] += 1
            if phase == "neutral_hold":
                held.append(value)
            if clock() - last_sync >= 1:
                os.fsync(log.fileno())
                last_sync = clock()
            monitor.require()  # Slow disk I/O cannot silently authorize the next command.
            return value

        event("start", setup=report)
        try:
            with DeviceLock(monitor.bus_number, monitor.pwm_address):
                try:
                    # Fresh readings are required before enabling any output. Historical
                    # fault recovery is explicit and only allowed with measured power off.
                    if not monitor.poll(force=True):
                        raise RuntimeError(monitor.fault)
                    bus = monitor.bus
                    off = monitor.off_bounds
                    if not off:
                        raise RuntimeError("Recorded physical off baseline required")
                    if not (
                        0 <= monitor.sample["bus_v"] <= off.max_bus_v
                        and abs(monitor.sample["shunt_mv"]) <= off.max_abs_shunt_mv + 1e-12
                    ):
                        raise RuntimeError(
                            "Servo supply present; disconnect ALL servo power before preparation"
                        )
                    monitor.prepare()
                    first = sample()
                    if first["power_state"] != "off":
                        raise RuntimeError("Supply changed during power-off preparation")
                    phase = "prepare"
                    event("neutral_intent", targets_us=report["targets_us"])
                    monitor.require()
                    prepare_neutral(bus, monitor.pwm_address, include_claw=True)
                    sample()
                    event(
                        "neutral_result", requested_us=1500, encoded_us=report["nominal_encoded_us"]
                    )
                    phase = "await_power"
                    print(
                        "All six channels programmed at 1500 us. INA219 monitoring active.\n"
                        "Support all links, clear the path and apply servo power now.\n"
                        "The 30-minute timer starts when power is detected. Cut physical power for binding.\n"
                        "PWM will be disabled on fault, interruption, power loss or test completion.",
                        flush=True,
                    )
                    deadline = clock() + await_power_seconds
                    while True:
                        value = sample()
                        if value["bus_v"] >= 0.5:
                            break
                        if clock() >= deadline:
                            raise TimeoutError("Timed out waiting for external servo power")
                        sleep(0.1)
                    phase = "neutral_hold"
                    powered_start = clock()
                    report["powered_at"] = datetime.now(timezone.utc).isoformat()
                    event("power_detected", bus_v=value["bus_v"])
                    print(
                        f"Power detected. Logging a {duration:g}-second fixed-neutral hold; operator supervision required.",
                        flush=True,
                    )
                    while clock() - powered_start < duration:
                        value = sample()
                        if value["bus_v"] < 0.5:
                            raise RuntimeError(
                                "Servo power removed; ending hold without automatic restart"
                            )
                        sleep(min(0.1, max(0, duration - (clock() - powered_start))))
                    report["powered_elapsed_seconds"] = clock() - powered_start
                    report["status"] = "complete"
                finally:
                    # Shutdown precedes fault logging and closing the sensor handle.
                    phase = "shutdown"
                    if bus is None:
                        bus = monitor.bus
                    if bus is not None:
                        try:
                            disable_outputs(bus, monitor.pwm_address)
                            report["pwm_full_off_verified"] = True
                        except Exception as exc:
                            report["pwm_full_off_verified"] = False
                            report["shutdown_error"] = str(exc)
                            raise RuntimeError(
                                "PWM shutdown failed. CUT PHYSICAL SERVO POWER NOW"
                            ) from exc
            if report["status"] == "complete":
                event("complete", pwm_full_off_verified=report.get("pwm_full_off_verified", False))
        except BaseException as exc:
            report.update(status="aborted", error=str(exc))
            monitor.trip(str(exc))
            monitor.record_fault()
            event(
                "fault",
                error=str(exc),
                pwm_full_off_verified=report.get("pwm_full_off_verified", False),
            )
            raise
        finally:
            report["ended_at"] = datetime.now(timezone.utc).isoformat()
            report["samples_by_phase"] = dict(counts)
            report["hold_summary"] = {
                "sample_count": len(held),
                "bus_v": distribution([s["bus_v"] for s in held]) if held else None,
                "shunt_mv": distribution([s["shunt_mv"] for s in held]) if held else None,
                "current_a": distribution([s["current_a"] for s in held])
                if held and monitor.verified
                else None,
            }
            log.flush()
            os.fsync(log.fileno())
            with (directory / "report.json").open("x") as output:
                json.dump(report, output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            monitor.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--power-off-confirmed", action="store_true")
    parser.add_argument("--supervised", action="store_true")
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--config", type=Path, default=ROOT / "config/development/pi.json")
    parser.add_argument("--state-directory", type=Path, default=ROOT / ".runtime/hardware")
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / ".runtime/commissioning/neutral-holds"
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 1800:
        parser.error("Duration must be between 1 and 1800 seconds")
    if not args.live:
        print(
            "Offline plan: all six channels at 1500 us, continuous INA219 monitoring, PWM full-off on exit. No I2C access."
        )
        return 0
    if not args.power_off_confirmed or not args.supervised:
        parser.error("Physical power-off preparation and operator supervision must be confirmed")
    config = json.loads(args.config.read_text())
    if config["mode"] != "hardware":
        parser.error("Use the hardware configuration with --live")
    directory = args.output_root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    print(
        f"Evidence: {directory}\nAmperes and 2.5 A cutoff remain UNVERIFIED unless the configured shunt scale is verified.",
        flush=True,
    )
    previous = signal.signal(signal.SIGTERM, interrupted)
    store = Store("arm", args.state_directory)
    try:
        run(config, directory, duration=args.duration, store=store)
        print("Test complete; PWM full-off verified. CUT PHYSICAL SERVO POWER.", flush=True)
    except (KeyboardInterrupt, OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"Test stopped: {exc}. CUT PHYSICAL SERVO POWER. Evidence: {directory}", flush=True)
        return 1
    finally:
        store.close()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
