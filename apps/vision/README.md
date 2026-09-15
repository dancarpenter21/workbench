# vision

Owns USB/OpenCV and Raspberry Pi CSI camera capture and reference-image classification. `GET /observations` returns timestamped tool locations, confidence and tray clearance; `GET /preview` returns a JPEG in hardware mode or an SVG scene in mock mode.

## Run independently

From the repository root:

```sh
uv run uvicorn workbench_vision.app:create_app --factory --host 127.0.0.1 --port 8101
```

Default mode is mock. Settings and environment overrides are documented in the root
README. Use one worker only. Health: `http://127.0.0.1:8101/health`; interactive API
documentation: `http://127.0.0.1:8101/docs`.

Hardware mode needs the `vision` optional extra, configured camera regions and per-region reference images. See the hardware guide. Mock-only routes: `PUT /mock/scene`, `POST /mock/deliver/{tool_id}`, and `POST /mock/reset`. The scene endpoint supports stale timestamps and missing/uncertain tools for integration tests.

Dependencies are declared in this application's `pyproject.toml`; common HTTP and contract
packages are workspace dependencies. Install from the root with `uv sync`. Run relevant
integration tests with `uv run pytest tests/integration -q`.

See [architecture](../../docs/architecture.md) and [hardware setup](../../docs/hardware.md).

## Camera backends

In hardware mode, `camera.backend` selects `opencv` (the default when omitted) or
`picamera2` for Camera Module 3 connected to the Pi CSI socket. Both use `device`,
`width`, `height`, and `interval_seconds`. For Picamera2, `device` is the zero-based
camera index reported by `rpicam-hello --list-cameras`.

```json
{
  "camera": {
    "backend": "picamera2",
    "device": 0,
    "width": 640,
    "height": 480,
    "interval_seconds": 0.25
  }
}
```

Use Raspberry Pi OS's `python3-picamera2` package and an environment that can import
its system-installed Picamera2/libcamera bindings. Installing the repository's
`vision` extra alone does not provide those bindings. Picamera2 is imported only
when its hardware backend starts; mock mode never opens a camera. See the
[Raspberry Pi installation guidance](https://github.com/raspberrypi/picamera2#installation).

Capture uses Picamera2's `RGB888` format, whose array pixels are ordered B, G, R,
matching OpenCV reference images and JPEG encoding. The
[official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
explains this format naming. The camera runs without an on-screen preview; the API
serves JPEG previews. Region coordinates and references must be calibrated on the
installed camera at the configured resolution and stable focus/lighting.

Startup and capture failures mark camera health unavailable and hide the preview;
the last observation timestamp is retained until another frame succeeds. CSI
capture waits are bounded to one second so a missing frame can be reported and a
pending capture cancelled during shutdown. Startup failures require restarting
vision after correcting the configuration/device. Tests use fake devices; physical
Camera Module 3 operation, focus and frame latency still need validation on the Pi.

## Camera setup without the service

The standalone setup commands open the selected camera and prepare references before
starting vision. They do not start the arm service or import its runtime. Install the
`vision` extra first; Picamera2 also needs the Pi system packages described above.

```sh
uv run --no-sync python -m workbench_vision.setup init \
  --output config/workbench/local.json
uv run --no-sync python -m workbench_vision.setup capture \
  --config config/development/pi.json --output .runtime/camera-setup/frame.png
uv run --no-sync python -m workbench_vision.setup overlay \
  --bench config/workbench/local.json --image .runtime/camera-setup/frame.png \
  --output .runtime/camera-setup/regions.png
```

`capture` opens the **real camera**, even when the selected settings profile says
`mode: mock`. It needs no reference images or arm calibration. Inspect `frame.png` for
color, framing and focus, and `regions.png` for region placement. The default five
warm-up frames can be changed with `--discard-frames` (0–60); this does not establish
stable focus or lighting. Keep raw captures for reference collection; overlays are
for inspection only.

| Command | Purpose |
| --- | --- |
| `init --output PATH [--source PATH]` | Copy a bench configuration once; source defaults to `config/workbench/default.json`. |
| `capture --config PATH --output PATH` | Save a raw PNG from the configured camera backend and resolution. |
| `overlay --bench PATH --image PATH --output PATH` | Draw named region boxes on a separate inspection PNG. |
| `region --bench PATH --image PATH --name ID --box X Y W H` | Set a region within the supplied image; changing its box clears its stale reference entries. |
| `reference --bench PATH --image PATH --region ID --label LABEL --output PATH` | Crop a region from a raw frame to a PNG and record its reference path. |
| `check --bench PATH --config PATH` | Check region names, bounds, complete reference labels, readable images and image dimensions; return nonzero on errors. |

New configuration and image outputs refuse to overwrite existing files. Use fresh
image names for repeated captures and inspection. `region` and `reference` update
the specified bench configuration. Region IDs are the configured tool IDs plus
`tray`; labels are `empty` plus all tool IDs. The shipped three-tool bench needs
**16 references**, including each tool placed in each of the four regions.

Follow the [reference collection workflow](../../docs/hardware.md#vision-reference-calibration)
for the complete procedure and the final vision-only service command. These tools
prepare and validate files; physical camera operation and recognition still need
validation on the installed bench.
