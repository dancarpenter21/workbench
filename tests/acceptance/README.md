# Acceptance protocol

Automated simulated acceptance: start mock applications, then run
`uv run python scripts/smoke.py`. This checks 30 deliveries, identity and duplicate IDs.
It is not a physical acceptance result.

For physical acceptance, log 10 trials for each of the three tools. Record tool, spoken
request, transcript, operation ID, start/end time, source/tray observations, successful
delivery, wrong-tool delivery, stop/recovery event, and failure explanation. Require at
least 27 successes out of 30, and zero wrong-tool deliveries. Keep raw results alongside
local audio and camera recordings, excluding private recordings from git.

Also test missing/swapped/displaced tools, uncertain speech, an unknown tool, two tools in
one request, stale/disconnected cameras, an occupied tray, serial disconnection, stop during
movement, power interruption, service restart during movement and explicit recovery.
Disconnect internet after model setup and repeat a complete request. Measure recognition,
inference, motion and total latency separately. Confirm webcam selection with a second
USB camera.

No recorded real camera/audio fixtures or hardware trial results are included yet. The
integration suite generates a synthetic silent WAV to check transport/format handling;
mock voice returns a fixed phrase. Collect real recordings during calibration to evaluate
the actual models and vision thresholds.
