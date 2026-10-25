# Pi diagnostics dashboard

Diagnostics is a tab in the normal workbench. Start the full stack with `npm start` and select Diagnostics. It reports Pi, camera, microphone, HAT, current monitor, and model status. Opening this tab does not command arm motion.

## Prepare this Pi

Use the OS Python interpreter so the Picamera2/libcamera binary packages match it.
On this Pi, Raspberry Pi OS already supplies `python3-picamera2` and ALSA `arecord`.
The project environment must expose the system packages:

```sh
uv venv --allow-existing --system-site-packages --python /usr/bin/python3 .venv
uv sync --locked --extra vision --extra pwm
uv run --no-sync python -c 'from picamera2 import Picamera2; import cv2; import smbus2'
```

This changes only the project environment; it does not install Whisper or llama.cpp.
If preparing another Pi, install its OS camera packages and `alsa-utils` first.

Load your Node installation before running npm. This computer uses nvm:

```sh
. "$HOME/.nvm/nvm.sh"
nvm use 26
npm ci
```

Node 22.12+ is supported by the project; CI uses Node 24. The installed Node 26 was
used for this Pi's initial diagnostics build.

## Start and open from the LAN

```sh
npm start
```

Open `http://<pi-address>:5173` from another computer on the same LAN and select Diagnostics. Backends remain bound to loopback; Vite proxies browser API requests. Ctrl+C stops the launcher and its children. Logs and SQLite state are under `.runtime/hardware/`.

## Use the diagnostics

- **Pi:** read temperature, load, memory, storage, uptime, and firmware throttling
  flags. An unavailable metric is unknown rather than a healthy zero.
- **Camera:** preview uses the existing camera owner and requires no tool-reference
  images. Capture and recognition have separate statuses. Missing references keep
  recognition unavailable even when the image is good. Only one application can
  own the camera: close a separate camera-stream/rpicam application before using
  this view. Failed opens retry every two seconds; repeated read failures reopen
  the camera. Stale frames are hidden.
- **Pi USB microphone:** select an ALSA capture device and click **Test microphone**.
  The Pi records five seconds at 16 kHz, mono, 16-bit PCM. Play the result in your
  browser; peak/RMS values and clipping indicate captured signal, not speech
  recognition accuracy. This uses the Pi's USB input, not your laptop microphone.
  Cancel aborts the request; capture is bounded and concurrent tests are rejected.
- **Servo HAT:** uses the arm service’s cached read-only presence check at the configured
  individual I²C address (defaults: bus 1, `0x40`). Opening or refreshing Diagnostics
  does not launch the standalone checker or construct a PWM output driver. Register communication does not measure
  servo position, power, movement, or mechanical calibration.
- **Models:** installed dependency, model path, and loading status are reported.
- **Report:** downloads the latest complete diagnostic response as JSON. Audio is
  excluded. Recordings are transient in server/browser memory, not written to disk.

Live arm hardware follows the coordinator status stream; other checks refresh every
five seconds and preview every second. A missing service leaves
other checks usable and marks retained readings stale. A service response means its
API is reachable; it does not establish hardware readiness. Mock checks and audio
are explicitly labeled simulated.

## API additions

`GET /diagnostics` on vision, voice, assistant, and arm returns typed diagnostic
checks. The coordinator's `GET /diagnostics` returns the aggregate report and host
metrics. The voice service and coordinator expose:

- `GET /diagnostics/microphone/devices`
- `POST /diagnostics/microphone/test` with an enumerated `device_id`

The test returns WAV bytes encoded as base64 plus signal measurements. All commands
use fixed arguments, validated device identifiers, and bounded execution. The
coordinator status includes `retrieval_enabled` and commissioning readiness.

Browser requests use the existing `/api/coordinator/` proxy prefix. Generated
OpenAPI and TypeScript contracts contain the additions. Existing mock retrieval
and stop remain available in the Workbench view. The shared recovery panel and its
acknowledgment/request handling have been removed. Diagnostics retains interrupted
commissioning-session cleanup and current-monitor reset; general controller recovery
uses the acknowledged `POST /coordinator/recover` API after inspection. Recovery
interlocks continue to block motion until cleared through the appropriate procedure.

## Verification

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
npm run contracts
npm run build
npx playwright install chromium
npm run test:e2e
```

Stop the diagnostics stack before the browser suite: both use backend ports
8100–8104. Browser tests start mock mode, reset its scene, exercise diagnostic
reports/audio/failure recovery, and save a dashboard screenshot in `test-results/`.
These checks do not qualify physical arm operation.


## Installed Pi verification — 2026-10-21

- 223 Python tests and four browser tests passed; Ruff, TypeScript/build, regenerated
  contracts, and diff whitespace checks passed.
- The LAN-address dashboard displayed fresh 640×480 Picamera2 frames at about
  3.7 captures/second, with preview updates once per second.
- The Pi USB microphone produced a five-second WAV with nonzero signal and no
  clipping. Browser playback advanced correctly; live cancellation promptly
  released the capture device through the proxy and coordinator.
- The HAT passed read-only register inspection at bus 1, address 0x40. No PWM writes
  or physical arm movements were performed.
- Desktop and narrow mobile layouts were visually inspected. Local screenshots
  are under `.runtime/diagnostics/dashboard-live.png` and `dashboard-mobile.png`;
  the browser suite also saves a simulated screenshot under `test-results/`.
- The separate `pi5-camera-test` stream was stopped with the owner's approval so
  the workbench could own the camera. The dashboard is running manually; this
  verification does not install boot services or establish remote-computer audio
  quality or physical arm acceptance.

## Shared arm status

Live arm hardware cards and the shared header use the same coordinator event stream.
Fresh readings with servo power off are normal; a prior motion interlock is shown
separately as **Motion inhibited — reset required**. Manual-control configuration and
neutral-reference availability do not determine whether the sensor is responding.
See [shared arm hardware status](hardware-status.md) for service ownership, data flow,
staleness and diagnostic-export behavior.
