import asyncio
import json
import sys
from contextlib import asynccontextmanager

from fastapi import HTTPException
from workbench_common import Store, app_base, settings
from workbench_common.diagnostics import command
from workbench_contracts import ArmCommand, ArmStatus, Diagnostic, Health, Recovery

from workbench_arm_controller.driver import SerialArm
from workbench_arm_controller.pca9685 import AUTOMATION_UNAVAILABLE


def create_app(config=None, store=None):
    cfg = config or settings()
    state = {"store": store, "driver": None, "task": None, "error": ""}
    lock = asyncio.Lock()

    def current():
        return ArmStatus.model_validate(state["store"].get("status", {"state": "idle"}))

    async def save(status):
        await state["store"].aput("status", status.model_dump(mode="json"))
        return status

    async def stop_driver():
        if state["driver"]:
            try:
                await asyncio.to_thread(state["driver"].stop)
                return "Software hold requested. Verify the arm is stationary before recovery."
            except Exception:
                return "Could not confirm arm control. Use the physical power cutoff."
        if cfg["mode"] == "hardware":
            return "Hardware control unavailable. Use the physical cutoff and inspect the arm."
        return "Simulated motion stopped"

    @asynccontextmanager
    async def lifespan(app):
        state["store"] = store or Store("arm")
        status = current()
        if status.state == "moving":
            status.state, status.recovery_required = "fault", True
            status.message = "Controller restarted during motion; inspect hardware and recover"
            await save(status)
        if cfg["mode"] == "hardware":
            try:
                if cfg.get("profile") == "diagnostics":
                    raise ValueError("Motion disabled in diagnostics profile")
                backend = cfg["arm"].get("backend", "roarm")
                if backend == "pca9685":
                    raise ValueError(AUTOMATION_UNAVAILABLE)
                if backend != "roarm":
                    raise ValueError(f"Unknown arm backend: {backend}")
                state["driver"] = await asyncio.to_thread(SerialArm, cfg)
                await asyncio.to_thread(state["driver"].feedback)
                # Every hardware startup requires operator inspection before movement.
                status = current()
                status.state, status.recovery_required = "fault", True
                status.message = await stop_driver()
                await save(status)
            except Exception as exc:
                state["error"] = str(exc)
                status = current()
                status.state, status.recovery_required = "fault", True
                status.message = str(exc)
                await save(status)
        yield
        if state["task"] and not state["task"].done():
            status = current()
            status.state, status.recovery_required = "stopped", True
            await save(status)
            await stop_driver()
            await state["task"]
        if state["driver"]:
            state["driver"].close()
        if store is None:
            state["store"].close()

    app = app_base("arm-controller", lifespan)

    diagnostic_lock = asyncio.Lock()

    @app.get("/diagnostics", response_model=list[Diagnostic])
    async def diagnostics():
        if cfg["mode"] == "mock":
            return [
                Diagnostic(
                    component="hat",
                    status="ok",
                    summary="Simulated HAT",
                    measurements={"simulated": True, "physical_position": None},
                )
            ]
        if cfg["arm"].get("backend") != "pca9685":
            return [
                Diagnostic(
                    component="hat", status="unavailable", summary="PCA9685 backend not selected"
                )
            ]
        if diagnostic_lock.locked():
            return [Diagnostic(component="hat", status="unavailable", summary="Check busy")]
        async with diagnostic_lock:
            try:
                data = json.loads(
                    (
                        await command(
                            sys.executable,
                            "-m",
                            "workbench_arm_controller.hat_check",
                            "--bus",
                            str(cfg["arm"].get("i2c_bus", 1)),
                            "--address",
                            str(cfg["arm"].get("i2c_address", 0x40)),
                            timeout=2,
                        )
                    ).decode()
                )
                return [
                    Diagnostic(
                        component="hat",
                        status="ok",
                        summary="Register communication verified; physical position unknown",
                        measurements={
                            **{k: v for k, v in data.items() if k != "status"},
                            "physical_position": None,
                            "retrieval_enabled": False,
                            "calibrated": cfg["workbench"]["arm"].get("calibrated", False),
                        },
                    )
                ]
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                return [
                    Diagnostic(
                        component="hat", status="error", summary=str(exc) or "HAT check timed out"
                    )
                ]

    @app.get("/health", response_model=Health)
    async def health():
        return Health(
            service="arm-controller",
            mode=cfg["mode"],
            ready=not state["error"],
            detail=state["error"],
        )

    @app.get("/status", response_model=ArmStatus)
    async def status():
        return current()

    async def run(command):
        try:
            if cfg["mode"] == "mock":
                for _ in range(6):
                    await asyncio.sleep(cfg["arm"]["mock_step_seconds"])
                    if current().state != "moving":
                        return
            else:
                await asyncio.to_thread(state["driver"].execute, command.tool_id)
            async with lock:
                status = current()
                if status.state == "moving":
                    status.state, status.message = "completed", "Motion sequence completed"
                    await save(status)
        except Exception as exc:
            async with lock:
                status = current()
                if status.state == "moving":
                    status.state, status.recovery_required = "fault", True
                    status.message = f"{exc}. {await stop_driver()}"
                    await save(status)

    @app.post("/execute", response_model=ArmStatus, status_code=202)
    async def execute(command: ArmCommand):
        if cfg.get("profile") == "diagnostics":
            raise HTTPException(409, "Motion disabled in diagnostics profile")
        async with lock:
            if state["error"]:
                raise HTTPException(503, state["error"])
            if command.tool_id not in {t["id"] for t in cfg["workbench"]["tools"]}:
                raise HTTPException(422, "Unknown tool")
            key = f"operation:{command.operation_id}"
            if state["store"].get(key):
                raise HTTPException(
                    409, "Operation already submitted; inspect status, do not replay"
                )
            status = current()
            if status.recovery_required or (state["task"] and not state["task"].done()):
                raise HTTPException(409, "Arm is busy or requires recovery")
            # Persist intent before issuing any hardware command.
            await state["store"].aput(key, command.model_dump(mode="json"))
            status = await save(
                ArmStatus(
                    state="moving",
                    operation_id=command.operation_id,
                    tool_id=command.tool_id,
                    message="Executing reviewed path",
                )
            )
            state["task"] = asyncio.create_task(run(command))
            return status

    @app.post("/stop", response_model=ArmStatus)
    async def stop():
        async with lock:
            status = current()
            status.state, status.recovery_required = "stopped", True
            status.message = await stop_driver()
            return await save(status)

    @app.post("/recover", response_model=ArmStatus)
    async def recover(request: Recovery):
        async with lock:
            if state["error"]:
                raise HTTPException(503, state["error"])
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "Wait for the motion worker to stop")
            if state["driver"]:
                try:
                    await asyncio.to_thread(state["driver"].recover)
                except Exception as exc:
                    raise HTTPException(503, "Arm feedback unavailable; recovery refused") from exc
            return await save(ArmStatus(state="idle", message="Ready for a new request"))

    return app
