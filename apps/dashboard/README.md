# Dashboard

TypeScript/Vite operator interface with a typed OpenAPI HTTP client. It displays camera
preview, tool shortcuts, request progress and service readiness, and provides local
microphone capture, stop controls and acknowledged recovery.

From the repository root, install with `npm ci`, then run:

```sh
npm run dev --workspace @workbench/dashboard
```

Start the Python applications separately with `uv run python scripts/dev.py --backend-only`.
The dashboard defaults to port 5173. Set `WORKBENCH_COORDINATOR_URL` to change its backend
proxy target. Use `-- --port 5174` to change the Vite port. The root launcher starts both
the dashboard and all backends with `npm run dev`.

`npm run build` at the repository root runs TypeScript validation and creates `dist/`.
Production static hosting needs a reverse proxy for `/api/coordinator/*`; the development
Vite proxy is not bundled into the production output.

Microphone access requires localhost or HTTPS and browser permission. Audio is converted
to mono PCM WAV locally and uploaded for local transcription. Transcripts are shown for
review; recording does not automatically trigger movement. Mock voice returns a fixed
phrase. No remote fonts, telemetry, or hosted inference is used.

The shared generated types come from `@workbench/contracts`; regenerate them with
`npm run contracts` when backend contracts change.
