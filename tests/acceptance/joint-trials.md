# Pulse test acceptance

Run physical checks only with an operator at the bench and continuous claw INA219
monitoring. Software tests use fake devices or mock mode and do not satisfy physical
acceptance. Preserve actual setup, observations and failures in the exported record
and associated bench notes. Read [Pulse test](../../docs/joint-trials.md) first.

1. Secure/support the assembly, clear the established neutral path, arrange immediate
   physical power cutoff and verify fresh raw INA219 readings. Verify current scale
   and cutoff are explicitly unverified unless externally verified for this setup.
2. Open Pulse test. Verify opening the tab sends no motion. Select a joint and enter
   a small position request. Verify there are no Start/Prepare forms or required names.
3. Press Move. Verify all six outputs are initialized at the established 1500 μs
   neutral request, with current checks before and throughout output writes, followed
   by the selected joint's requested change. This is not measured position feedback.
4. Verify only OK / Not OK feedback is required before another move. OK saves the
   requested pulse, timestamp and result; no direction or physical position is inferred.
   Verify other joints retain their targets when selecting and moving another joint.
5. In separate supported trials, verify Not OK attempts output shutdown and instructs
   physical cutoff; Stop retains targets and blocks further movement. Do not deliberately
   bind or stall a servo. Disconnect power/support links and finish before another session.
6. Verify a new explicitly commanded session initializes all six at neutral again.
   Browser blur/disconnect and service restart must not automatically resume or initialize.
   Reconnect requires power-off finish for interrupted sessions.
7. Exercise sensor loss/staleness, both overcurrent polarities, partial writes and
   storage failures with injected software fixtures, not deliberate physical overcurrent.
   Verify shutdown attempts, latched faults and no automatic movement retry. Explicit
   sensor reset requires healthy readings, disconnected power and acknowledgment.
8. Inspect saved/reloaded history and export: OK/Not OK results persist, simulated and
   physical evidence remain distinct, current logs remain available and manual/automatic
   retrieval flags remain disabled. Simple position acceptance does not qualify paths,
   limits, rates, grasping or autonomous retrieval.
