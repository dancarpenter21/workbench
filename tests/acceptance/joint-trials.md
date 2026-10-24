# Initialization acceptance

Run physical checks only with an operator at the bench and continuous claw INA219
monitoring. Software tests use fake devices or mock mode and do not satisfy physical
acceptance. Preserve actual setup, observations and failures in internal API records
and associated bench notes. Read [Initialization](../../docs/joint-trials.md) first.

1. Secure/support the assembly, clear the established neutral path, arrange immediate
   physical power cutoff and verify fresh raw INA219 readings. Verify current scale
   and cutoff are explicitly unverified unless externally verified for this setup.
2. Open Initialization. Verify opening the tab sends no motion. Select a joint and enter
   a small pulse request. Verify no operator, history or export forms are present.
   Current-scale verification must remain accessible through the checklist’s Diagnostics link.
   Verify the Initialization checklist tracks saved bounds and verified scale automatically,
   retains physical-review checkboxes after reload, and blocks review edits during a trial.
   Ticking reviews must never enable motion or count simulated progress as physical evidence.
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
8. After a safe OK response, capture minimum/maximum without movement. Verify ordering,
   explicit replacement, all six summaries and persistence through Finish/reload/new
   sessions. A new move blocks capture until OK. Captures from a different joint or
   stale command must be rejected by injected API tests.
9. Near a range edge, confirm clearance and extend by 50 μs. Verify no movement or
   saved-bound change occurs. New sessions reset to 1450–1550 μs. Verify larger-step
   behavior (up to 40 μs), 250 ms spacing and the absolute 500–2500 μs envelope using
   mock/injected tests before supervised physical use; do not explore unknown physical
   travel merely to reach the software envelope.
10. Inspect internal API records: OK/Not OK evidence and current logs remain available;
    simulated and physical results stay separate, and Jog/automatic retrieval remain
    gated. Captured bounds do not qualify paths, rates, grasping or autonomous retrieval.
