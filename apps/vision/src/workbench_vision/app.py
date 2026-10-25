import asyncio
import html
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import HTTPException, Response
from workbench_common import ROOT, app_base, settings
from workbench_contracts import (
    Diagnostic,
    Health,
    MockScene,
    Observation,
    Observations,
    VideoAnswer,
    VideoOffer,
    now,
)

from .capture import open_capture
from .stream import VideoSessions

RECONNECT_INTERVAL_SECONDS = 2


class Camera:
    def __init__(self, cfg):
        import cv2

        self.cv = cv2
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.frame = None
        self.observations = Observations(
            captured_at=now(), tools=[], tray_clear=False, camera_ok=False
        )
        self.error = "Camera starting"
        self.references = {}
        self.frame_at = None
        self.frame_id = None
        self.timestamp_source = "capture"
        self.frame_size = None
        self.frame_rate = 0.0
        self.previous_frame_time = None
        self.recognition_error = ""
        try:
            self.load_references()
        except (ValueError, OSError) as exc:
            self.recognition_error = str(exc)
        self.thread = threading.Thread(target=self.run_forever, daemon=True)
        self.thread.start()
        self.recognition_thread = threading.Thread(target=self.run_recognition, daemon=True)
        self.recognition_thread.start()

    def load_references(self):
        vision = self.cfg["workbench"]["vision"]
        labels = {"empty", *(t["id"] for t in self.cfg["workbench"]["tools"])}
        for region in vision["regions"]:
            paths = vision["references"].get(region, {})
            if set(paths) != labels:
                raise ValueError(f"Region {region} needs reference images for {sorted(labels)}")
            self.references[region] = {}
            for label, path in paths.items():
                image = self.cv.imread(str(ROOT / path))
                if image is None:
                    raise ValueError(f"Cannot read reference: {path}")
                self.references[region][label] = self.cv.resize(image, (64, 64)).astype("float32")

    def classify(self, crop, region):
        import numpy as np

        vision = self.cfg["workbench"]["vision"]
        image = self.cv.resize(crop, (64, 64)).astype("float32")
        scores = sorted(
            (
                (1 - float(np.abs(image - reference).mean()) / 255, label)
                for label, reference in self.references[region].items()
            ),
            reverse=True,
        )
        score, label = scores[0]
        if score < vision["match_threshold"] or score - scores[1][0] < vision["match_margin"]:
            return "uncertain", score
        return label, score

    def record_error(self, exc):
        with self.lock:
            self.error = str(exc) or type(exc).__name__
            self.frame = None
            self.observations.camera_ok = False

    def recognize(self, frame):
        labels = {}
        for region, (x, y, w, h) in self.cfg["workbench"]["vision"]["regions"].items():
            if min(x, y) < 0 or min(w, h) <= 0 or y + h > frame.shape[0] or x + w > frame.shape[1]:
                raise ValueError(f"Region {region} lies outside camera frame")
            labels[region] = self.classify(frame[y : y + h, x : x + w], region)
        observations = []
        for tool in self.cfg["workbench"]["tools"]:
            tid = tool["id"]
            source, score = labels[tid]
            tray, tray_score = labels["tray"]
            if tray == tid and source == "empty":
                location, confidence = "tray", min(score, tray_score)
            elif source == tid and tray != tid:
                location, confidence = "source", score
            elif source == "empty" and tray != "uncertain":
                location, confidence = "missing", score
            else:
                location, confidence = "uncertain", 0
            observations.append(Observation(tool_id=tid, location=location, confidence=confidence))
        return Observations(
            captured_at=now(),
            tools=observations,
            tray_clear=labels["tray"][0] == "empty",
            camera_ok=True,
        )

    def run_forever(self):
        while not self.stop.is_set():
            self.run()
            self.stop.wait(RECONNECT_INTERVAL_SECONDS)

    def run(self):
        camera = self.cfg["camera"]
        cap = None
        try:
            cap = open_capture(camera, self.cv)
            failures = 0
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    frame = cap.read()
                    captured_at = getattr(cap, "captured_at", None)
                    if not isinstance(captured_at, datetime):
                        captured_at = now()
                    failures = 0
                    with self.lock:
                        self.frame = frame
                        self.frame_at = captured_at
                        self.frame_id = captured_at.isoformat()
                        self.timestamp_source = (
                            "sensor"
                            if getattr(cap, "timestamp_source", "") == "sensor"
                            else "capture"
                        )
                        self.frame_size = (frame.shape[1], frame.shape[0])
                        tick = time.monotonic()
                        previous = getattr(self, "previous_frame_time", None)
                        self.frame_rate = 1 / (tick - previous) if previous else 0.0
                        self.previous_frame_time = tick
                        self.error = ""
                except Exception as exc:
                    self.record_error(exc)
                    failures += 1
                    if failures >= 3:
                        break
                # Capture counts toward the interval, rather than adding
                # another full delay after each frame. Never queue catch-up frames.
                self.stop.wait(max(0, camera["interval_seconds"] - (time.monotonic() - started)))
        except Exception as exc:
            self.record_error(exc)
        finally:
            if cap is not None:
                try:
                    cap.close()
                except Exception as exc:
                    self.record_error(exc)

    def latest(self):
        """Return one immutable frame reference; consumers never queue camera frames."""
        with self.lock:
            if (
                self.frame is None
                or self.error
                or self.frame_at is None
                or (now() - self.frame_at).total_seconds() > self.cfg["observation_max_age_seconds"]
            ):
                return None
            return self.frame_id, self.frame, self.frame_at, self.timestamp_source

    def snapshot(self):
        sample = self.latest()
        if sample is None:
            return None
        frame_id, frame, captured_at, source = sample
        ok, jpeg = self.cv.imencode(".jpg", frame)
        if not ok:
            raise ValueError("Camera encoding failed")
        return frame_id, jpeg.tobytes(), captured_at, source

    def run_recognition(self):
        previous = None
        while not self.stop.is_set():
            sample = self.latest()
            if sample is None or sample[0] == previous or self.recognition_error:
                self.stop.wait(0.01)
                continue
            previous, frame, captured_at, _ = sample
            try:
                observation = self.recognize(frame)
                observation.captured_at = captured_at
                with self.lock:
                    # A read failure during inference must not restore camera health.
                    if self.frame is not None and not self.error:
                        self.observations = observation
            except Exception as exc:
                with self.lock:
                    self.recognition_error = str(exc) or type(exc).__name__
                    self.observations.camera_ok = False

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        if hasattr(self, "recognition_thread"):
            self.recognition_thread.join(timeout=2)
        if self.thread.is_alive():
            self.record_error("Camera capture did not stop within two seconds")


def create_app(config=None):
    cfg = config or settings()
    initial = [
        Observation(tool_id=t["id"], location="source", confidence=1)
        for t in cfg["workbench"]["tools"]
    ]
    state = {"scene": MockScene(tools=initial), "camera": None, "error": ""}

    sessions = VideoSessions(cfg, lambda: state["camera"], lambda: state["scene"])

    @asynccontextmanager
    async def lifespan(app):
        if cfg["mode"] == "hardware":
            try:
                state["camera"] = await asyncio.to_thread(Camera, cfg)
            except Exception as exc:
                state["error"] = str(exc)
        yield
        await sessions.close()
        if state["camera"]:
            await asyncio.to_thread(state["camera"].close)

    app = app_base("vision", lifespan)

    @app.get("/diagnostics", response_model=list[Diagnostic])
    async def diagnostics():
        if cfg["mode"] == "mock":
            return [
                Diagnostic(
                    component="camera",
                    status="ok",
                    summary="Simulated camera",
                    measurements={"simulated": True, "frame_age_seconds": 0.0},
                )
            ]
        camera = state["camera"]
        if not camera:
            return [
                Diagnostic(
                    component="camera",
                    status="error",
                    summary=state["error"] or "Camera not initialized",
                )
            ]
        with camera.lock:
            age = (now() - camera.frame_at).total_seconds() if camera.frame_at else None
            fresh = age is not None and age <= cfg["observation_max_age_seconds"]
            return [
                Diagnostic(
                    component="camera",
                    status="ok" if fresh and not camera.error else "error",
                    summary=camera.error or ("Live frames available" if fresh else "Frames stale"),
                    measurements={
                        "backend": cfg["camera"].get("backend", "opencv"),
                        "frame_age_seconds": age,
                        "last_frame_at": camera.frame_at.isoformat() if camera.frame_at else None,
                        "width": camera.frame_size[0] if camera.frame_size else None,
                        "height": camera.frame_size[1] if camera.frame_size else None,
                        "capture_fps": camera.frame_rate,
                        "target_fps": cfg["camera"].get("fps", 30),
                        "timestamp_source": camera.timestamp_source,
                    },
                ),
                Diagnostic(
                    component="recognition",
                    status="warning" if camera.recognition_error else "ok",
                    summary=camera.recognition_error or "Reference classifier configured",
                ),
            ]

    @app.get("/health", response_model=Health)
    async def health():
        error = state["error"] or (
            (state["camera"].error or state["camera"].recognition_error) if state["camera"] else ""
        )
        return Health(service="vision", mode=cfg["mode"], ready=not error, detail=error)

    @app.get("/observations", response_model=Observations)
    async def observations():
        if cfg["mode"] == "mock":
            scene = state["scene"]
            return Observations(
                captured_at=now() - timedelta(seconds=scene.age_seconds),
                tools=scene.tools,
                tray_clear=scene.tray_clear,
                camera_ok=scene.camera_ok,
            )
        if not state["camera"]:
            raise HTTPException(503, state["error"] or "Camera not initialized")
        with state["camera"].lock:
            return state["camera"].observations.model_copy(deep=True)

    @app.get("/preview")
    async def preview(after: str | None = None):
        if cfg["mode"] == "mock":
            fps = cfg["camera"].get("fps", 30)
            tick = time.monotonic()
            frame_id = f"simulated-{int(tick * fps)}"
            if after == frame_id:
                await asyncio.sleep((int(tick * fps) + 1) / fps - tick)
                tick = time.monotonic()
                frame_id = f"simulated-{int(tick * fps)}"
            stamp = (now() - timedelta(seconds=tick % (1 / fps))).isoformat()
            lines = "".join(
                f'<text x="30" y="{100 + i * 65}" fill="#dae9e4" font-size="22">'
                f"{html.escape(t.tool_id)}: {t.location}</text>"
                for i, t in enumerate(state["scene"].tools)
            )
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
                '<rect width="640" height="360" fill="#152c28"/>'
                '<text x="30" y="45" fill="#80d5b7" font-size="18">SIMULATED WORKBENCH</text>'
                + lines
                + "</svg>"
            )
            return Response(
                svg,
                media_type="image/svg+xml",
                headers={
                    "Cache-Control": "no-store",
                    "X-Frame-Id": frame_id,
                    "X-Frame-Captured-At": stamp,
                    "X-Frame-Age-Ms": "0",
                    "X-Frame-Time-Source": "simulated",
                },
            )
        camera = state["camera"]
        if camera:
            # Long polling returns only a new, latest frame. No per-client frame queue.
            deadline = time.monotonic() + 1
            while after is not None:
                with camera.lock:
                    if camera.frame_id != after or camera.error:
                        break
                if time.monotonic() >= deadline:
                    return Response(status_code=204, headers={"Cache-Control": "no-store"})
                await asyncio.sleep(0.002)
            sample = await asyncio.to_thread(camera.snapshot)
            if sample:
                frame_id, jpeg, captured_at, source = sample
                return Response(
                    jpeg,
                    media_type="image/jpeg",
                    headers={
                        "Cache-Control": "no-store",
                        "X-Frame-Id": frame_id,
                        "X-Frame-Captured-At": captured_at.isoformat(),
                        "X-Frame-Age-Ms": str(max(0, (now() - captured_at).total_seconds() * 1000)),
                        "X-Frame-Time-Source": source,
                    },
                )
        raise HTTPException(503, "Camera preview unavailable")

    @app.post("/webrtc/offer", response_model=VideoAnswer)
    async def video_offer(value: VideoOffer):
        return await sessions.offer(value)

    @app.delete("/webrtc/sessions/{session_id}", status_code=204)
    async def close_video(session_id: str):
        await sessions.remove(session_id)

    if cfg["mode"] == "mock":

        @app.put("/mock/scene", response_model=MockScene)
        async def scene(value: MockScene):
            expected = {t["id"] for t in cfg["workbench"]["tools"]}
            if {t.tool_id for t in value.tools} != expected or len(value.tools) != len(expected):
                raise HTTPException(422, "Provide exactly one observation for each known tool")
            state["scene"] = value
            return value

        @app.post("/mock/deliver/{tool_id}", response_model=MockScene)
        async def deliver(tool_id: str):
            if tool_id not in {t.tool_id for t in state["scene"].tools}:
                raise HTTPException(404, "Unknown tool")
            state["scene"].tools = [
                t.model_copy(update={"location": "tray"}) if t.tool_id == tool_id else t
                for t in state["scene"].tools
            ]
            state["scene"].tray_clear = False
            return state["scene"]

        @app.post("/mock/reset", response_model=MockScene)
        async def reset():
            state["scene"] = MockScene(tools=[t.model_copy() for t in initial])
            return state["scene"]

    return app
