"""Read-only, operator-declared arm-off baseline capture from the running arm service.

This module never opens I2C and never sends a motion or PWM request. Raw evidence
and proposed display bounds stay separate from current-scale calibration.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from workbench_common import ROOT
from workbench_contracts import ClawCurrentStatus

from .claw_current import check_current

BUS_QUANTUM_V = 0.004
SHUNT_QUANTUM_MV = 0.01
MAX_OFF_BUS_V = 0.5
MAX_OFF_SHUNT_MV = 0.5


def distribution(values):
    ordered = sorted(values)
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "stddev": statistics.pstdev(values),
        "p95": ordered[math.ceil(len(values) * 0.95) - 1],
        "p99": ordered[math.ceil(len(values) * 0.99) - 1],
    }


def upper_bound(values, quantum):
    """Cover observed extremes plus at least three register counts of margin."""
    margin = max(3 * quantum, 3 * statistics.pstdev(values), max(values) - min(values))
    high = max(values) + margin
    return round(math.ceil(high / quantum - 1e-9) * quantum, 6), margin


def summarize(samples):
    if len(samples) < 2:
        raise ValueError("At least two distinct valid readings required")
    bus = [s["bus_v"] for s in samples]
    shunt = [s["shunt_mv"] for s in samples]
    max_bus, bus_margin = upper_bound(bus, BUS_QUANTUM_V)
    max_shunt, shunt_margin = upper_bound([abs(v) for v in shunt], SHUNT_QUANTUM_MV)
    if max_bus > MAX_OFF_BUS_V or max_shunt > MAX_OFF_SHUNT_MV:
        raise ValueError("Proposed bounds too wide for an arm-off baseline; inspect the supply")
    verified = all(s["scale_verified"] for s in samples)
    resistances = {s.get("shunt_ohms") for s in samples}
    if verified and (len(resistances) != 1 or None in resistances):
        raise ValueError("Current scale changed during capture")
    return {
        "sample_count": len(samples),
        "bus_v": distribution(bus),
        "shunt_mv": distribution(shunt),
        "current_a": distribution([s["current_a"] for s in samples]) if verified else None,
        "proposed_bounds": {
            "max_bus_v": max_bus,
            "max_abs_shunt_mv": max_shunt,
            "max_abs_current_a": max_shunt / 1000 / next(iter(resistances)) if verified else None,
        },
        "margins": {"bus_v": bus_margin, "abs_shunt_mv": shunt_margin},
        "method": "Observed maximum + max(3 register counts, 3 population stddev, observed span), rounded up to a register count; absolute shunt voltage",
        "qualification": "Provisional single-session display bounds; not proof all servo power is off or a current safety limit",
    }


def validate_sample(value, *, allow_simulated=False):
    sample = ClawCurrentStatus.model_validate(value).model_dump(mode="json")
    if sample["simulated"] and not allow_simulated:
        raise ValueError("Simulated readings cannot establish physical off bounds")
    if not sample["healthy"] or sample["fault"]:
        raise ValueError("INA219 monitoring fault: " + (sample["fault"] or "unhealthy reading"))
    if sample["age_seconds"] is None or not 0 <= sample["age_seconds"] < 0.3:
        raise ValueError("Missing or stale INA219 sample")
    if not sample["sampled_at"]:
        raise ValueError("Timestamp required for distinct samples")
    check_current(sample)
    if not 0 <= sample["bus_v"] <= MAX_OFF_BUS_V or abs(sample["shunt_mv"]) > MAX_OFF_SHUNT_MV:
        raise ValueError("Readings do not resemble an off supply; stop and check physical power")
    if sample["scale_verified"] and (
        sample["shunt_ohms"] is None
        or sample["shunt_ohms"] <= 0
        or sample["current_a"] is None
        or not sample["verification_evidence"].strip()
    ):
        raise ValueError("Verified ampere scale requires resistance, evidence, and current")
    return sample


def capture(
    client,
    directory,
    *,
    duration=60.0,
    interval=0.1,
    observer,
    allow_simulated=False,
    clock=time.monotonic,
    sleep=time.sleep,
    setup=None,
    wall_clock=lambda: datetime.now(timezone.utc),
):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    run_id = directory.name
    started = wall_clock().isoformat()
    metadata = {
        "schema_version": 1,
        "baseline_id": run_id,
        "started_at": started,
        "declared_state": "arm_power_off",
        "observer": observer,
        "source": str(client.base_url),
        "requested_duration_seconds": duration,
        "poll_interval_seconds": interval,
        "setup": setup or {},
        "read_only": True,
    }
    samples = []
    identities = set()
    previous_stamp = None
    source_identity = None
    start = clock()
    report = {**metadata, "status": "in_progress", "summary": None}
    raw_path = directory / "samples.jsonl"
    with raw_path.open("x", buffering=1) as log:

        def event(kind, **values):
            log.write(
                json.dumps(
                    {
                        "record_type": kind,
                        "baseline_id": run_id,
                        "recorded_at": wall_clock().isoformat(),
                        **values,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
            log.flush()

        event("start", metadata=metadata)
        try:
            commissioning = client.get("/commissioning/status")
            commissioning.raise_for_status()
            if commissioning.json().get("reserved"):
                raise ValueError("Finish the active commissioning trial before an off baseline")
            while clock() - start < duration:
                request_start = clock()
                response = client.get("/manual/status")
                response.raise_for_status()
                status = response.json()
                if status.get("active"):
                    raise ValueError("Manual motion session active; baseline aborted")
                sample = validate_sample(
                    status.get("claw_current"), allow_simulated=allow_simulated
                )
                for field in ("i2c_bus", "sensor_address", "pwm_address"):
                    if (
                        setup
                        and field in setup
                        and sample.get(field) is not None
                        and sample[field] != setup[field]
                    ):
                        raise ValueError(
                            "Live sensor setup differs from the selected configuration"
                        )
                latency = clock() - request_start
                if sample["age_seconds"] + latency >= 0.3:
                    raise ValueError("Sample became stale during HTTP request")
                stamp = datetime.fromisoformat(sample["sampled_at"].replace("Z", "+00:00"))
                # Reject old cached timestamps even if a client reports a small age.
                if abs((wall_clock() - stamp).total_seconds()) >= 0.5:
                    raise ValueError("Source timestamp is stale or clocks are not synchronized")
                identity = (
                    sample["simulated"],
                    sample["scale_verified"],
                    sample["shunt_ohms"],
                    sample["verification_evidence"],
                    sample.get("monitor_session_id"),
                    sample.get("i2c_bus"),
                    sample.get("sensor_address"),
                )
                if source_identity is None:
                    source_identity = identity
                elif identity != source_identity:
                    raise ValueError(
                        "Sensor setup, monitor session, domain, or current scale changed"
                    )
                key = sample["sampled_at"]
                if previous_stamp is not None and stamp < previous_stamp:
                    raise ValueError("Source timestamps moved backwards")
                if key not in identities:
                    identities.add(key)
                    previous_stamp = stamp
                    samples.append(sample)
                    event(
                        "sample",
                        elapsed_seconds=clock() - start,
                        request_seconds=latency,
                        reading=sample,
                    )
                elapsed = clock() - start
                sleep(max(0, min(interval - (clock() - request_start), duration - elapsed)))
            span = (
                (
                    datetime.fromisoformat(samples[-1]["sampled_at"].replace("Z", "+00:00"))
                    - datetime.fromisoformat(samples[0]["sampled_at"].replace("Z", "+00:00"))
                ).total_seconds()
                if samples
                else 0
            )
            if (
                len(samples) < max(2, math.floor(duration / interval * 0.5))
                or span < duration * 0.9
            ):
                raise ValueError(
                    "Insufficient distinct samples or time coverage; no usable baseline"
                )
            report.update(
                status="complete",
                domain="simulated" if samples[0]["simulated"] else "physical",
                source_span_seconds=span,
                summary=summarize(samples),
            )
            event("complete", summary=report["summary"])
        except BaseException as exc:
            report.update(status="rejected", error=str(exc), sample_count=len(samples))
            event("rejected", error=str(exc), sample_count=len(samples))
            raise
        finally:
            log.flush()
            os.fsync(log.fileno())
            report["ended_at"] = wall_clock().isoformat()
            report["samples_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            report_path = directory / "report.json"
            with report_path.open("x") as output:
                json.dump(report, output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="collect read-only samples")
    parser.add_argument("--arm-off-confirmed", action="store_true")
    parser.add_argument("--observer", default="operator")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--config", type=Path, default=ROOT / "config/development/pi.json")
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / ".runtime/commissioning/off-baselines"
    )
    parser.add_argument("--allow-simulated", action="store_true")
    args = parser.parse_args(argv)
    if (
        not math.isfinite(args.duration)
        or not 10 <= args.duration <= 3600
        or not math.isfinite(args.interval)
        or not 0.05 <= args.interval <= 0.2
    ):
        parser.error("Use duration 10–3600 seconds and interval 0.05–0.2 seconds")
    if not args.capture:
        print(
            "Read-only arm-off baseline: GET status only; no I2C/PWM writes.\n"
            "Use --capture --arm-off-confirmed after disconnecting all servo power."
        )
        return 0
    if not args.arm_off_confirmed:
        parser.error("--arm-off-confirmed required; no powered testing in this procedure")
    cfg = json.loads(args.config.read_text())
    arm = cfg["arm"]
    setup = {
        "i2c_bus": arm.get("i2c_bus", 1),
        "sensor_address": 0x41,
        "pwm_address": arm.get("i2c_address", 0x40),
        "config": str(args.config.resolve()),
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = args.output_root / run_id
    print(
        f"Capturing {args.duration:g} seconds from the running monitor. Evidence: {directory}",
        flush=True,
    )
    try:
        with httpx.Client(base_url=cfg["services"]["arm-controller"], timeout=0.5) as client:
            report = capture(
                client,
                directory,
                duration=args.duration,
                interval=args.interval,
                observer=args.observer,
                allow_simulated=args.allow_simulated,
                setup=setup,
            )
        print(json.dumps(report["summary"], indent=2))
        print(f"Report: {directory / 'report.json'}")
    except (OSError, ValueError, httpx.HTTPError, KeyboardInterrupt) as exc:
        print(f"Baseline rejected: {exc}. Evidence retained at {directory}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
