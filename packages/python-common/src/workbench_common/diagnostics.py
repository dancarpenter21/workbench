"""Read-only diagnostics and bounded subprocesses; no device output ownership."""

import asyncio
import importlib.util
import os
import platform
import shutil
from pathlib import Path

from workbench_contracts import Diagnostic

from . import ROOT


async def command(*args, timeout=3):
    """Fixed-argument callers only. Reap children on timeout, cancellation, and shutdown."""
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise RuntimeError(
                stderr.decode(errors="replace").strip()
                or stdout.decode(errors="replace").strip()
                or "Command failed"
            )
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def model_check(component, cfg, module, path, loaded):
    if cfg["mode"] == "mock":
        return Diagnostic(
            component=component,
            status="ok",
            summary="Simulated model",
            measurements={"simulated": True},
        )
    present = (ROOT / path).is_dir() if component == "voice" else (ROOT / path).is_file()
    available = importlib.util.find_spec(module) is not None
    return Diagnostic(
        component=component,
        status="ok" if loaded else "warning",
        summary="Model loaded"
        if loaded
        else (
            "Model loading skipped in diagnostics profile"
            if cfg.get("profile") == "diagnostics"
            else "Model not loaded"
        ),
        measurements={
            "dependency_available": available,
            "model_path": path,
            "model_present": present,
            "loaded": loaded,
        },
    )


async def host_check(mock=False):
    if mock:
        return Diagnostic(
            component="pi",
            status="ok",
            summary="Simulated Pi",
            measurements={"simulated": True, "temperature_c": 45.0},
        )
    values = {"os": platform.platform()}
    errors = []
    reads = {
        "model": ("/proc/device-tree/model", lambda s: s.rstrip("\0")),
        "uptime_seconds": ("/proc/uptime", lambda s: float(s.split()[0])),
        "temperature_c": ("/sys/class/thermal/thermal_zone0/temp", lambda s: int(s) / 1000),
    }
    for name, (path, parse) in reads.items():
        try:
            values[name] = parse(Path(path).read_text().strip())
        except (OSError, ValueError):
            values[name] = None
            errors.append(name)
    try:
        values["load_1m"] = os.getloadavg()[0]
        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        values["memory_total_mb"] = int(memory["MemTotal"].split()[0]) / 1024
        values["memory_available_mb"] = int(memory["MemAvailable"].split()[0]) / 1024
        disk = shutil.disk_usage(ROOT)
        values["disk_free_gb"] = disk.free / 1024**3
        values["disk_total_gb"] = disk.total / 1024**3
    except (OSError, KeyError, ValueError):
        errors.append("resource metrics")
    try:
        values["throttling"] = (await command("vcgencmd", "get_throttled")).decode().strip()
    except (OSError, RuntimeError, TimeoutError):
        values["throttling"] = None
    return Diagnostic(
        component="pi",
        status="warning" if errors else "ok",
        summary="Unavailable: " + ", ".join(errors) if errors else "Host metrics available",
        measurements=values,
    )
