import asyncio
import html
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import HTTPException, Response
from workbench_common import ROOT, app_base, settings
from workbench_contracts import Health, MockScene, Observation, Observations, now


class Camera:
    def __init__(self, cfg):
        import cv2

        self.cv = cv2
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.jpeg = None
        self.observations = Observations(
            captured_at=now(), tools=[], tray_clear=False, camera_ok=False
        )
        self.error = "Camera starting"
        self.references = {}
        vision = cfg["workbench"]["vision"]
        labels = {"empty", *(t["id"] for t in cfg["workbench"]["tools"])}
        for region in vision["regions"]:
            paths = vision["references"].get(region, {})
            if set(paths) != labels:
                raise ValueError(f"Region {region} needs reference images for {sorted(labels)}")
            self.references[region] = {}
            for label, path in paths.items():
                image = cv2.imread(str(ROOT / path))
                if image is None:
                    raise ValueError(f"Cannot read reference: {path}")
                self.references[region][label] = cv2.resize(image, (64, 64)).astype("float32")
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

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

    def run(self):
        camera = self.cfg["camera"]
        cap = self.cv.VideoCapture(camera["device"])
        cap.set(self.cv.CAP_PROP_FRAME_WIDTH, camera["width"])
        cap.set(self.cv.CAP_PROP_FRAME_HEIGHT, camera["height"])
        try:
            while not self.stop.is_set():
                try:
                    ok, frame = cap.read()
                    if not ok:
                        raise ValueError("Camera disconnected or unavailable")
                    labels = {}
                    for region, (x, y, w, h) in self.cfg["workbench"]["vision"]["regions"].items():
                        if (
                            min(x, y) < 0
                            or min(w, h) <= 0
                            or y + h > frame.shape[0]
                            or x + w > frame.shape[1]
                        ):
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
                        observations.append(
                            Observation(tool_id=tid, location=location, confidence=confidence)
                        )
                    ok, jpeg = self.cv.imencode(".jpg", frame)
                    if not ok:
                        raise ValueError("Camera encoding failed")
                    with self.lock:
                        self.jpeg = jpeg.tobytes()
                        self.observations = Observations(
                            captured_at=now(),
                            tools=observations,
                            tray_clear=labels["tray"][0] == "empty",
                            camera_ok=True,
                        )
                        self.error = ""
                except Exception as exc:
                    with self.lock:
                        self.error = str(exc)
                        self.observations.camera_ok = False
                self.stop.wait(camera["interval_seconds"])
        finally:
            cap.release()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def create_app(config=None):
    cfg = config or settings()
    initial = [
        Observation(tool_id=t["id"], location="source", confidence=1)
        for t in cfg["workbench"]["tools"]
    ]
    state = {"scene": MockScene(tools=initial), "camera": None, "error": ""}

    @asynccontextmanager
    async def lifespan(app):
        if cfg["mode"] == "hardware":
            try:
                state["camera"] = await asyncio.to_thread(Camera, cfg)
            except Exception as exc:
                state["error"] = str(exc)
        yield
        if state["camera"]:
            await asyncio.to_thread(state["camera"].close)

    app = app_base("vision", lifespan)

    @app.get("/health", response_model=Health)
    async def health():
        error = state["error"] or (state["camera"].error if state["camera"] else "")
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
    async def preview():
        if cfg["mode"] == "mock":
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
            return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
        camera = state["camera"]
        if camera:
            with camera.lock:
                if camera.jpeg and camera.observations.camera_ok:
                    return Response(
                        camera.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
                    )
        raise HTTPException(503, "Camera preview unavailable")

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
