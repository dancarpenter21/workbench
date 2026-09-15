"""Hardware capture backends. Frames use OpenCV's BGR channel order."""


class OpenCVCapture:
    def __init__(self, camera, cv):
        self.device = cv.VideoCapture(camera["device"])
        try:
            if not self.device.isOpened():
                raise RuntimeError("OpenCV camera disconnected or unavailable")
            self.device.set(cv.CAP_PROP_FRAME_WIDTH, camera["width"])
            self.device.set(cv.CAP_PROP_FRAME_HEIGHT, camera["height"])
        except Exception:
            self.close()
            raise

    def read(self):
        ok, frame = self.device.read()
        if not ok or frame is None:
            raise RuntimeError("OpenCV camera disconnected or unavailable")
        return frame

    def close(self):
        if self.device is not None:
            device, self.device = self.device, None
            device.release()


class Picamera2Capture:
    def __init__(self, camera):
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError(
                "Picamera2 backend unavailable: install Raspberry Pi OS python3-picamera2 "
                "and make its system packages visible to the vision Python environment"
            ) from exc

        self.device = Picamera2(camera_num=camera["device"])
        self.pending = None
        try:
            config = self.device.create_video_configuration(
                # Picamera2 RGB888 stores [B, G, R]; BGR888 would swap red and blue.
                main={"size": (camera["width"], camera["height"]), "format": "RGB888"},
                queue=False,
            )
            self.device.configure(config)
            self.device.start()
        except Exception:
            self.close()
            raise

    def read(self):
        # Keep one outstanding job after a timeout, rather than queueing more captures.
        # The bounded wait lets Camera.close() stop a disconnected CSI camera.
        if self.pending is None:
            self.pending = self.device.capture_array("main", wait=False)
        try:
            frame = self.device.wait(self.pending, timeout=1.0)
        except TimeoutError as exc:
            raise TimeoutError("Picamera2 frame capture timed out") from exc
        except Exception:
            # A completed job that failed cannot produce another frame on retry.
            self.pending = None
            raise
        self.pending = None
        if frame is None:
            raise RuntimeError("Picamera2 returned no camera frame")
        return frame

    def close(self):
        if self.device is not None:
            device, self.device = self.device, None
            try:
                if self.pending is not None:
                    device.cancel_all_and_flush()
                    self.pending = None
            finally:
                # Picamera2.close() stops capture and releases the camera and preview.
                device.close()


def open_capture(camera, cv):
    backend = camera.get("backend", "opencv")
    if backend == "opencv":
        return OpenCVCapture(camera, cv)
    if backend == "picamera2":
        return Picamera2Capture(camera)
    raise ValueError(f"Unknown camera backend: {backend!r}; use opencv or picamera2")
