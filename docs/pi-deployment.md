# Raspberry Pi deployment preparation

The deployment helper prepares a **mock-only**, manually started backend service.
It does not install services, open cameras/I2C, start processes, acknowledge recovery
or issue motion commands. DFRobot/PCA9685 automated retrieval remains disabled.
The generated files can be reviewed and tested offline before a Pi exists.

## 1. Prepare a repeatable configuration bundle

From the checkout, choose the eventual absolute Linux checkout path and the existing
non-root Pi account. These example values are deployment paths, not hardware settings:

```sh
python scripts/pi.py prepare --pi-root /home/operator/workbench --user operator \
  --output .runtime/pi-deployment
python scripts/pi.py check --bundle .runtime/pi-deployment
```

Use `--config PATH` and `--bench PATH` to snapshot reviewed local JSON files. The defaults
are `config/development/pi.json` and `config/workbench/default.json`. The runtime must
retain mock mode, Picamera2 and PCA9685 selection; the helper refuses hardware mode.
Camera and arm calibration values are copied unchanged. No unknown physical value is filled in.

The new directory contains `runtime.json`, `bench.json`, `workbench-mock.service` and a
manifest of file hashes. Existing output directories are refused. `check` detects changed
files and validates the service template and unique loopback URLs; the unit also runs this
check before startup. It does not qualify
calibration, installed packages, devices, systemd, user permissions or physical readiness.
Hashes detect accidental changes; they are not signed provenance. To change configuration,
generate and review a new bundle, then replace the installed bundle while stopped.

The bundle's target directory is always `<pi-root>/.runtime/pi-deployment`. When preparing
on another computer, copy the bundle there on the Pi. Use paths without spaces or shell/
systemd expansion characters. Resolve host-specific absolute reference-image paths again
on the Pi. Keep reviewed source configuration and commissioning evidence backed up;
`.runtime` is ignored by Git.

## 2. Prepare the Pi environment (requires the Pi)

Use the repository's Python 3.11+ requirement and a Raspberry Pi OS/system Python pair
compatible with its camera packages. Record the OS release, architecture, Python version,
repository commit and installed package versions with the commissioning record. First
install the locked base environment in the checkout:

```sh
uv sync --locked
.venv/bin/python scripts/pi.py check --bundle .runtime/pi-deployment
```

This mock service needs no optional hardware/model extras and runs directly from the
installed virtual environment; service startup never downloads packages or models.
For Camera Module 3 testing, follow the separate
[system-package/Picamera2 environment instructions](hardware.md#camera-module-3-smoke-check-on-the-pi).
Check that `/usr/bin/python3` meets the project's Python requirement before selecting it.
Camera and local model compatibility, memory use and latency remain untested on the Pi.

Ensure the selected user owns the checkout and can write `.runtime/pi-mock`. Keep this
persistent state directory distinct from `.runtime/hardware` and every other running stack.
Do not launch another backend stack or uvicorn worker against the same ports or state files.
The unit fixes `WEB_CONCURRENCY=1` and uses the existing launcher, with one process per
application and loopback APIs. There is no dashboard service in the bundle.

## 3. Review, install and start manually (requires Linux/systemd)

Inspect all generated files before installing. The following commands are for the Pi;
they have not been exercised on the target hardware:

```sh
systemd-analyze verify .runtime/pi-deployment/workbench-mock.service
sudo install -m 0644 .runtime/pi-deployment/workbench-mock.service /etc/systemd/system/workbench-mock.service
sudo systemctl daemon-reload
sudo systemctl start workbench-mock.service
systemctl status workbench-mock.service
.venv/bin/python scripts/pi.py health --config .runtime/pi-deployment/runtime.json
```

The unit has `Restart=no` and no `[Install]` section. It configures neither automatic
restart nor boot startup. Start it only when wanted; do not add an enable target or a
restart policy during commissioning. `Type=simple` means systemd's active state does
not prove application initialization or readiness. See the upstream
[service semantics](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)
and [installation semantics](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml).

For the dashboard on the Pi's local display, install the frontend dependencies with
`npm ci` and run only the frontend in a separate terminal:

```sh
WORKBENCH_COORDINATOR_URL=http://127.0.0.1:8100 \
  npm run dev --workspace @workbench/dashboard -- --host 127.0.0.1 --port 5173
```

Adjust the coordinator port if changed in the bundle. Open `http://127.0.0.1:5173` on
the Pi. Do not run top-level `npm run dev` alongside the service: it starts backends too.
Remote access, authentication and a production frontend server remain outside this local
commissioning profile.

## 4. Interpret health without inferring physical readiness

`scripts/pi.py health` only makes seven GET requests: five `/health` checks and arm/
coordinator `/status`. It uses canonical response contracts, checks identities and mode,
ignores HTTP proxy settings and refuses redirects. Each request has a bounded timeout
(`--timeout`, default two seconds). There are no automatic HTTP retries or state changes.
Malformed or truncated HTTP responses are reported against the affected service/status
check, and inspection continues through the remaining endpoints. A failed status read
leaves recovery and idle state unknown rather than assuming readiness.

| Signal | What it establishes |
| --- | --- |
| systemd active | Launcher process is running; initialization may still be pending |
| `services.*.responding` | Correctly identified health response in the expected mode |
| `services_ready` | All services report ready; a service can still require recovery |
| `recovery_required`, `workflow_idle` | Current arm/coordinator status permits an idle software state, or needs attention |
| `checks_ok` / exit 0 | All above software checks passed; this is not physical readiness or permission to move |

Exit 1 means a service/status check failed, reported unready, was busy or required recovery.
Exit 2 means invalid input or configuration. Unavailable status produces `null` for unknown
recovery/idle fields. The check does not test recognition, clear paths, current tool position,
arm stationarity, measured motion completion or a functioning physical cutoff.

When manually inspecting a separately started hardware stack, use `--expected-mode hardware`.
The PCA9685 arm service is expected to remain unready and the check to fail. Do not alter
the generated mock service to run hardware. Use the dedicated camera-only instructions
with motor power disconnected for the currently supported Pi hardware check.

## 5. Shutdown, logs and durable state

```sh
sudo systemctl stop workbench-mock.service
journalctl -u workbench-mock.service -b --no-pager
```

The existing launcher shuts down the coordinator first, then arm, assistant, voice and
vision. `KillMode=mixed` sends the initial termination signal to the launcher so it can
perform that sequence. Remaining processes are killed if the launcher exits or the unit's
100-second stop timeout expires; the launcher independently allows 15 seconds per child.
These are process-cleanup limits, not motion or physical stopping limits.
[Upstream kill semantics](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml).

**Stopping applications does not establish a physical stop.** The service never controls
servo power. Existing PCA9685 output may persist if its controlling process fails; loss
of PWM or power may release the arm. Bench evidence must establish the actual behavior
and physical cutoff procedure before hardware operation. No automatic restart, recovery
acknowledgement or motion replay is part of this deployment flow.

Launcher messages go to the systemd journal. Each backend appends logs to
`.runtime/pi-mock/<service>.log`; operation IDs correlate API requests. Check both places
for startup failures. Log files currently have no automatic rotation. Inspect disk use and
archive/truncate app logs only while stopped; record the time span and repository revision.
Journal retention is controlled by the Pi's OS configuration.

Arm/coordinator SQLite databases and their recovery/operation records live in the same
state directory. Preserve them across restarts and upgrades. Back up the entire state
directory while stopped; do not delete databases to clear recovery, share them between
modes, or restore an old copy to replay an operation. After interruption, inspect status
and the bench before using the application's explicit recovery flow. The DFRobot service
continues to refuse recovery until its motion implementation is commissioned.

## Still required on the Pi and bench

- Validate OS/Python/package compatibility, ownership, boot disk durability/free space,
  systemd unit verification, and journal/log retention on the installed Pi.
- Test manual start/stop, port conflicts, a child failure, process interruption, absent
  services and a manual restart in mock mode. Confirm no orphan process, duplicate worker,
  automatic restart, lost operation history or automatic replay.
- Inspect camera-only captures, references and focus, then measure camera/model memory
  use, temperature and latency on the actual Pi. Verify browser microphone access locally.
- Establish electrical protection, signal integrity, actual channel/limit calibration,
  startup/readiness/completion evidence and physical stop/recovery behavior with the arm
  supported and supervised. This bundle supplies none of that physical evidence.
