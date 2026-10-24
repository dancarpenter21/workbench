# Guided joint trial acceptance

Run only with an operator at the bench after reading
[the joint trial procedure](../../docs/joint-trials.md) and hardware guidance.
Software tests are not physical acceptance. Record assembly ID, operator,
wiring check, supply arrangement, date, and observed outcomes.

1. Verify the factory assembly is unchanged, base is secured, all links can be
   supported, and channels run base 5 through claw 0. Establish individual servo
   power isolation and immediate physical power disconnection.
2. Leave servo power disconnected. Enable only the physical commissioning gate.
   Create a physical record. Confirm the panel labels physical observations and
   normal uncommissioned manual control remains unavailable.
3. Select base/5. Prepare with all acknowledgements. Independently verify only
   channel 5 has PWM and the other outputs are disabled. Where available,
   measure the actual pulse width and period; note differences from nominal
   register encoding in the record.
4. Apply only the selected servo's power with supported links and clearance.
   Record the initial movement. If unsuitable, disconnect power, stop, and
   finish as needs-review. Do not expand the restricted trial window.
5. If suitable, acknowledge settled/clear, issue one 10 μs step, observe direction,
   and record requested/encoded pulse and the physical observation. Verify each
   click produces only one step and the 1450–1550 μs window is enforced.
6. Exercise Stop, browser focus loss, and browser disconnection in separate
   supported trials. Verify no further step is accepted, PWM is retained, and
   normal manual control/retrieval stays blocked until power-off finish.
7. With servo power disconnected and links supported, finish and verify all
   outputs disabled. Start the next joint only through a fresh preparation.
8. With the arm supported and power disconnected, restart the service during an
   unfinished trial. Verify no target is restored, an interrupted trial requires
   finish, and its record is needs-review.
9. Complete suitable observations on each joint or explicitly leave unsuitable
   joints for review. Export the bundle. Verify its domain, mapping, observation
   values, history, disabled configuration flags, and unset rates.

Do not accept a combined starting pose, jogging rate, or autonomous retrieval
based on these single-joint results. Record failures and unresolved mechanical
questions before further commissioning.


## All-servos-holding pulse tests and current interlock

This is a separate supervised acceptance run, not an instruction to test currently
powered hardware. Support all links and disconnect all servo power first.

1. Confirm fresh raw INA219 readings at `0x41` on the configured bus before
   preparing outputs. With the unverified scale, verify that amperes are unavailable
   and the 2.5 A cutoff is explicitly unverified. Do not deliberately stall the claw.
2. Select **All servos holding**. Prepare with disconnected power and confirm all
   six requested targets are 1500 µs, then apply power under support and observe.
3. For each channel 5 through 0, apply one typed change of at most 10 µs. Verify
   only that servo's output changes, the other five retain their pulses, and raw
   claw readings continue even while another servo is selected or all are holding.
4. Reject a target more than 10 µs away and a target outside the active window.
   Review clearance before widening a selected trial window. Verify the edit and
   subsequent intent/result pulses appear in the exported record.
5. Stop the session and disconnect the browser while the supported assembly holds.
   Verify raw sensor samples continue in the service log. Reconnect and finish
   with power disconnected; do not expect automatic resumption.
6. With servo power disconnected and outputs under observation, use a supervised
   sensor-fault injection to verify motion ownership is invalidated and PWM
   full-off is attempted. Verify clear physical-cutoff instructions and fresh
   power-off preparation after repair. Test conversion/staleness/current thresholds
   with injected software fixtures, not a deliberate physical overcurrent.
7. Verify orderly shutdown disables PWM before monitoring stops. Restart with
   power disconnected; no previous targets or ownership may be restored.
8. Export records and verify timestamped raw samples, simulated/physical labels,
   requested/encoded pulses, window edits, marks, fault history and disabled limits.
   Record actual observations and unresolved issues before enabling wider tests.
