import asyncio
import json
import sys
from contextlib import asynccontextmanager

from fastapi import HTTPException
from workbench_common import Store, app_base, settings
from workbench_common.diagnostics import command
from workbench_contracts import (
    ArmCommand,
    ArmStatus,
    CommissionAcquire,
    CommissionAttemptSelect,
    CommissionCommand,
    CommissionCreate,
    CommissionDraft,
    CommissionEdit,
    CommissionExport,
    CommissionFeedback,
    CommissionFinish,
    CommissionMark,
    CommissionPowerOffReset,
    CommissionPrepare,
    CommissionReady,
    CommissionSelect,
    CommissionSession,
    CommissionStatus,
    CommissionStep,
    CommissionStop,
    CommissionTarget,
    CommissionWindow,
    CurrentScaleApply,
    CurrentScaleState,
    CurrentScaleVerification,
    Diagnostic,
    Health,
    ManualAcquire,
    ManualInput,
    ManualPrepare,
    ManualRelease,
    ManualSession,
    ManualStatus,
    NeutralEvidence,
    Recovery,
)

from workbench_arm_controller.claw_current import ClawCurrentMonitor
from workbench_arm_controller.commissioning_live import LiveCommissioning
from workbench_arm_controller.current_scale import CurrentScaleRecords
from workbench_arm_controller.driver import SerialArm
from workbench_arm_controller.hat_presence import HatPresence
from workbench_arm_controller.manual import ManualControl
from workbench_arm_controller.neutral_reference import NeutralReference
from workbench_arm_controller.pca9685 import AUTOMATION_UNAVAILABLE


def create_app(config=None, store=None):
    cfg = config or settings()
    state = {"store": store, "driver": None, "task": None, "error": ""}
    lock = asyncio.Lock()
    monitor = ClawCurrentMonitor(cfg)
    hat_presence = HatPresence(cfg, monitor)
    scales = CurrentScaleRecords(monitor)
    manual = ManualControl(cfg, monitor=monitor)
    commissioning = LiveCommissioning(cfg, monitor=monitor)
    neutral_reference = NeutralReference(cfg)

    def busy():
        return bool(state["task"] and not state["task"].done())

    def commanded_neutral():
        targets = manual.targets if manual.prepared else {}
        if commissioning.trial and commissioning.trial.get("stage") in (
            "prepared",
            "ready",
            "awaiting_feedback",
            "awaiting_power_off",
        ):
            targets = commissioning.trial.get("targets", {})
        return len(targets) == 6 and all(abs(value - 1500) < 1e-9 for value in targets.values())

    def neutral_snapshot():
        return neutral_reference.assess(monitor.status(), commanded_neutral=commanded_neutral())

    def manual_snapshot():
        snapshot = manual.status(
            busy=busy() or commissioning.reserved, recovery=current().recovery_required
        )
        snapshot.neutral_evidence = neutral_snapshot()
        snapshot.servo_hat = hat_presence.status()
        return snapshot

    async def manual_io(function, *args):
        # Cancellation must not release the motion lock while an I2C thread still writes.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except HTTPException:
            raise
        except Exception as exc:
            monitor.trip(str(exc))
            try:
                await asyncio.to_thread(monitor_tick)
            except Exception:
                pass  # The original fault remains latched; physical power cutoff is required.
            raise HTTPException(503, monitor.fault) from exc

    def monitor_tick():
        monitor.stream = (
            commissioning.prefix + "samples:" + commissioning.trial["draft_id"]
            if commissioning.trial
            else "claw:service"
        )
        if (
            not monitor.simulated
            and monitor.last is not None
            and monitor.clock() - monitor.last >= 0.3
        ):
            monitor.trip("Stale INA219 readings")
        monitor.poll()
        if not monitor.shutdown_required:
            return
        monitor.shutdown_required = False
        failures = []
        for control in (manual, commissioning):
            try:
                control.emergency_disable()
            except Exception as exc:
                failures.append(str(exc))
        # Also cover a stale output left by an earlier service, without stealing another owner's lock.
        if not monitor.simulated and not manual.output and not commissioning.output:
            try:
                from workbench_arm_controller.device_lock import DeviceLock
                from workbench_arm_controller.neutral_servo_test import disable_outputs

                with DeviceLock(monitor.bus_number, cfg["arm"].get("i2c_address", 0x40)):
                    if monitor.bus is None:
                        from smbus2 import SMBus

                        monitor.bus = SMBus(monitor.bus_number)
                    disable_outputs(monitor.bus, cfg["arm"].get("i2c_address", 0x40))
            except Exception as exc:
                failures.append(str(exc))
        reason = monitor.fault + (
            " PWM shutdown failed: " + "; ".join(failures) if failures else ""
        )
        manual.fault(reason)
        monitor.record_fault()
        if commissioning.trial:
            try:
                commissioning.fault(RuntimeError(reason))
            except HTTPException:
                pass
            except Exception as exc:
                reason += f" Fault record unavailable: {exc}"
        monitor.fault = reason

    async def manual_worker():
        while True:
            await asyncio.sleep(0.02)
            async with lock:
                try:
                    await manual_io(monitor_tick)
                    await manual_io(hat_presence.poll)
                    await manual_io(manual.tick)
                    await manual_io(commissioning.tick)
                except Exception as exc:
                    monitor.trip(str(exc))
                    await manual_io(monitor_tick)
                if manual.recovery_required and not current().recovery_required:
                    status = current()
                    status.state, status.recovery_required = "stopped", True
                    status.message = manual.message
                    try:
                        await save(status)
                        await state["store"].aput("manual_active", False)
                    except Exception as exc:
                        monitor.trip(str(exc))
                        await manual_io(monitor_tick)

    def current():
        return ArmStatus.model_validate(state["store"].get("status", {"state": "idle"}))

    async def save(status):
        await state["store"].aput("status", status.model_dump(mode="json"))
        return status

    async def stop_driver():
        if commissioning.reserved:
            await manual_io(commissioning.stop)
            return "Commissioning stopped; PWM retained. Disconnect servo power and finish trial."
        if manual.token or (cfg["mode"] == "hardware" and cfg["arm"].get("backend") == "pca9685"):
            manual.hold(
                "Software hold requested; last PWM targets retained. Verify the arm is stationary.",
                recovery=True,
            )
            await state["store"].aput("manual_active", False)
            return manual.message
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
        worker = None
        try:
            commissioning.attach(state["store"])
            monitor.attach(state["store"])
            scales.attach(state["store"])
            await manual_io(monitor_tick)
            await manual_io(hat_presence.poll)
            status = current()
            if status.state == "moving" or state["store"].get("manual_active", False):
                status.state, status.recovery_required = "fault", True
                status.message = "Controller restarted during motion; inspect hardware and recover"
                await save(status)
            if cfg["mode"] == "hardware" and cfg["arm"].get("backend") == "pca9685":
                status = current()
                state["error"] = manual.error
                status.state, status.recovery_required = (
                    "fault" if manual.error else "stopped",
                    True,
                )
                status.message = (
                    manual.error or "Manual preparation required. " + AUTOMATION_UNAVAILABLE
                )
                await save(status)
            elif cfg["mode"] == "hardware":
                try:
                    if cfg.get("profile") == "diagnostics":
                        raise ValueError("Motion disabled in diagnostics profile")
                    backend = cfg["arm"].get("backend", "roarm")
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
            worker = asyncio.create_task(manual_worker())
            yield
        finally:
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            async with lock:
                try:
                    await manual_io(manual.close)
                finally:
                    try:
                        await manual_io(commissioning.close)
                    finally:
                        await manual_io(monitor.close)
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

    async def hat_diagnostics():
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

    @app.get("/diagnostics", response_model=list[Diagnostic])
    async def diagnostics():
        hat = await hat_diagnostics()
        async with lock:
            return [
                *hat,
                *neutral_reference.diagnostics(
                    monitor.status(), commanded_neutral=commanded_neutral()
                ),
            ]

    @app.get("/commissioning/evidence", response_model=NeutralEvidence)
    async def commissioning_evidence():
        async with lock:
            return neutral_snapshot()

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

    @app.get("/commissioning/current-scale", response_model=CurrentScaleState)
    async def current_scale_status():
        async with lock:
            return scales.status()

    @app.post("/commissioning/current-scale", response_model=CurrentScaleVerification)
    async def current_scale_save(value: CurrentScaleVerification):
        async with lock:
            return await manual_io(scales.save, value)

    @app.post("/commissioning/current-scale/apply", response_model=CurrentScaleState)
    async def current_scale_apply(value: CurrentScaleApply):
        async with lock:
            if cfg.get("profile", "workbench") != "workbench" or not monitor.enabled:
                raise HTTPException(
                    409, "Current scale activation requires the workbench PCA9685 backend"
                )
            if busy() or manual.token or commissioning.reserved:
                raise HTTPException(409, "Finish all arm sessions before applying a scale")

            def disable():
                if commissioning.simulated:
                    return
                if manual.output:
                    manual.output.disable()
                    manual.output.close_preserving_pwm()
                    manual.output = None
                output = commissioning.open_output()
                try:
                    output.disable()
                finally:
                    output.close()
                manual.prepared = False
                manual.commanded = {}

            return await manual_io(scales.apply, value, disable)

    @app.post("/commissioning/feedback", response_model=CommissionStatus)
    async def commissioning_feedback(value: CommissionFeedback):
        async with lock:
            return await manual_io(commissioning.feedback, value)

    @app.post("/commissioning/drafts/{draft_id}/select-attempt", response_model=CommissionDraft)
    async def commissioning_select_attempt(draft_id: str, value: CommissionAttemptSelect):
        async with lock:
            return await manual_io(commissioning.select_attempt, draft_id, value)

    @app.get("/commissioning/status", response_model=CommissionStatus)
    async def commissioning_status():
        async with lock:
            return commissioning.status()

    @app.get("/commissioning/drafts", response_model=list[CommissionDraft])
    async def commissioning_drafts():
        async with lock:
            return commissioning.drafts()

    @app.post("/commissioning/drafts", response_model=CommissionDraft)
    async def commissioning_create(value: CommissionCreate):
        async with lock:
            return await manual_io(commissioning.create, value)

    @app.get("/commissioning/drafts/{draft_id}", response_model=CommissionDraft)
    async def commissioning_read(draft_id: str):
        async with lock:
            return commissioning.read(draft_id)

    @app.post("/commissioning/drafts/{draft_id}", response_model=CommissionDraft)
    async def commissioning_edit(draft_id: str, value: CommissionEdit):
        async with lock:
            return await manual_io(commissioning.edit, draft_id, value)

    @app.get("/commissioning/drafts/{draft_id}/export", response_model=CommissionExport)
    async def commissioning_export(draft_id: str):
        async with lock:
            return commissioning.export(draft_id)

    @app.post("/commissioning/initialize", response_model=CommissionSession)
    async def commissioning_initialize(value: CommissionAcquire):
        async with lock:
            if busy() or manual.token or commissioning.reserved:
                raise HTTPException(409, "Arm is busy; finish the current session first")
            if not commissioning.available:
                raise HTTPException(409, "Commissioning unavailable")
            if not commissioning.simulated and manual.output:
                await manual_io(manual.output.close_preserving_pwm)
                manual.output = None
                manual.prepared = False
                manual.commanded = {}
            return await manual_io(commissioning.initialize, value)

    @app.post("/commissioning/reset-current", response_model=CommissionStatus)
    async def commissioning_reset_current(value: CommissionPowerOffReset):
        async with lock:
            if (
                not value.power_off_and_supported
                or busy()
                or manual.token
                or commissioning.reserved
            ):
                raise HTTPException(409, "Finish sessions and disconnect servo power before reset")
            await manual_io(monitor.prepare)
            return commissioning.status()

    @app.post("/commissioning/acquire", response_model=CommissionSession)
    async def commissioning_acquire(value: CommissionAcquire):
        async with lock:
            if busy() or manual.token:
                raise HTTPException(409, "Arm is busy")
            return await manual_io(commissioning.acquire, value)

    @app.post("/commissioning/prepare", response_model=CommissionStatus)
    async def commissioning_prepare(value: CommissionPrepare):
        async with lock:
            # Validate before handing off the process lock. The acknowledgement applies here.
            commissioning.tick()
            if not commissioning.token or value.token != commissioning.token:
                raise HTTPException(409, "Wrong commissioning session")
            if not commissioning.simulated and manual.output:
                await manual_io(manual.output.close_preserving_pwm)
                manual.output = None
            if not commissioning.simulated:
                manual.prepared = False
                manual.commanded = {}
            return await manual_io(commissioning.prepare, value)

    @app.post("/commissioning/ready", response_model=CommissionStatus)
    async def commissioning_ready(value: CommissionReady):
        async with lock:
            result = await manual_io(commissioning.ready, value)
            return result

    @app.post("/commissioning/heartbeat", response_model=CommissionStatus)
    async def commissioning_heartbeat(value: CommissionCommand):
        async with lock:
            result = await manual_io(commissioning.heartbeat, value)
            return result

    @app.post("/commissioning/step", response_model=CommissionStatus)
    async def commissioning_step(value: CommissionStep):
        async with lock:
            result = await manual_io(commissioning.step, value)
            return result

    @app.post("/commissioning/target", response_model=CommissionStatus)
    async def commissioning_target(value: CommissionTarget):
        async with lock:
            return await manual_io(commissioning.target, value)

    @app.post("/commissioning/select", response_model=CommissionStatus)
    async def commissioning_select(value: CommissionSelect):
        async with lock:
            return await manual_io(commissioning.select, value)

    @app.post("/commissioning/window", response_model=CommissionStatus)
    async def commissioning_window(value: CommissionWindow):
        async with lock:
            return await manual_io(commissioning.window, value)

    @app.post("/commissioning/mark", response_model=CommissionStatus)
    async def commissioning_mark(value: CommissionMark):
        async with lock:
            result = await manual_io(commissioning.mark, value)
            return result

    @app.post("/commissioning/stop", response_model=CommissionStatus)
    async def commissioning_stop(value: CommissionStop):
        async with lock:
            result = await manual_io(commissioning.stop_trial, value)
            return result

    @app.post("/commissioning/finish", response_model=CommissionStatus)
    async def commissioning_finish(value: CommissionFinish):
        async with lock:
            result = await manual_io(commissioning.finish, value)
            await save(
                ArmStatus(state="idle", message="Trial finished; individual observations only")
            )
            return result

    @app.get("/manual/status", response_model=ManualStatus)
    async def manual_status():
        async with lock:
            return manual_snapshot()

    @app.post("/manual/prepare", response_model=ManualStatus)
    async def manual_prepare(request: ManualPrepare):
        async with lock:
            if busy() or manual.token or commissioning.reserved:
                raise HTTPException(409, "Arm is busy")
            # Preparation is allowed during recovery only with power disconnected.
            await manual_io(manual.prepare)
            await save(ArmStatus(state="idle", message=manual.message))
            await state["store"].aput("manual_active", False)
            return manual_snapshot()

    @app.post("/manual/session", response_model=ManualSession)
    async def manual_acquire(request: ManualAcquire):
        async with lock:
            if busy() or commissioning.reserved or current().recovery_required:
                raise HTTPException(409, "Arm is busy or requires recovery")
            if manual.token or not manual.status().available or not manual.prepared:
                raise HTTPException(
                    409, "Manual control is unavailable, unprepared, or already owned"
                )
            await state["store"].aput("manual_active", True)
            return manual.acquire()

    @app.post("/manual/input", response_model=ManualStatus)
    async def manual_input(request: ManualInput):
        async with lock:
            await manual_io(manual.input, request)
            return manual_snapshot()

    @app.post("/manual/release", response_model=ManualStatus)
    async def manual_release(request: ManualRelease):
        async with lock:
            manual.authenticate(request.token)
            manual.hold()
            await state["store"].aput("manual_active", False)
            return manual_snapshot()

    @app.post("/execute", response_model=ArmStatus, status_code=202)
    async def execute(command: ArmCommand):
        if cfg.get("profile") == "diagnostics":
            raise HTTPException(409, "Motion disabled in diagnostics profile")
        async with lock:
            if commissioning.reserved:
                raise HTTPException(409, "Finish commissioning with power disconnected first")
            if manual.token:
                raise HTTPException(409, "Manual control owns the arm")
            if state["error"]:
                raise HTTPException(503, state["error"])
            if cfg["mode"] == "hardware" and cfg["arm"].get("backend") == "pca9685":
                raise HTTPException(409, AUTOMATION_UNAVAILABLE)
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
            if commissioning.reserved:
                raise HTTPException(409, "Finish commissioning with power disconnected first")
            if manual.token:
                raise HTTPException(409, "End manual control before recovery")
            if cfg["mode"] == "hardware" and cfg["arm"].get("backend") == "pca9685":
                manual.recover()
            elif manual.recovery_required:
                manual.recover()
            if state["task"] and not state["task"].done():
                raise HTTPException(409, "Wait for the motion worker to stop")
            if state["driver"]:
                try:
                    await asyncio.to_thread(state["driver"].recover)
                except Exception as exc:
                    raise HTTPException(503, "Arm feedback unavailable; recovery refused") from exc
            await state["store"].aput("manual_active", False)
            return await save(ArmStatus(state="idle", message="Ready for a new request"))

    return app
