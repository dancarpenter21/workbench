# Physical manual-control acceptance

Run only after the hardware and commissioning procedures and an initial review of
`docs/manual-controls.md`. Mock/browser tests establish software behavior only.
Record physical observations, assembly/configuration version, observer, timestamps,
load, and pass/fail evidence in the commissioning records. Keep runtime evidence private.

1. **Disconnected initialization:** support the arm, disconnect servo power, verify
   independent channels and cutoff. Start the workbench with saved commissioning limits.
   Opening services and the dashboard must not program motion. Verify preparation
   programs the reviewed initial signals on all six channels with motor power off.
2. **Power application:** follow the reviewed arrangement and power sequence. Observe
   initial movement and stationary behavior before confirming ready. Stop the trial
   if the physical response differs from the reviewed procedure.
3. **Mapping and directions:** use slow, short jogs for each selection. Confirm Base
   controls 5, then 4/3/2/1 along the arm, and Claw controls 0. Verify observed increasing
   direction matches each label and that other joints retain their targets.
4. **Bounds and speed:** verify measured usable limits with clearance and support;
   validate proportional and precision input, pulse quantization, and physical travel.
   Record limits and maximum pulse rates for the actual assembly/load. Software bounds
   do not establish collision avoidance between joints or with the bench.
5. **Keyboard:** test selection, held enable, release, opposing keys, neutral after
   selection, Shift precision, editable-field focus, and Escape Stop.
6. **Browser focus:** change app tabs and window focus while jogging. Verify held
   keys clear, targets stop changing, and the session stays enabled on return.
7. **Ownership:** try a second browser and a retrieval request during manual control;
   both must be refused. During retrieval, manual acquisition must be refused.
8. **Loss and hold:** release direction; end manual control; change tabs; move focus
   outside the panel; hide/close the page; interrupt network access. Measure target
   update cessation and observe actual actuator settling. Confirm network loss during active jogging triggers
   the server watchdog and requires recovery when release cannot arrive. Idle sessions
   retain ownership during background timer throttling.
9. **Faults and restart:** with appropriate support and power procedures, verify output
   failure handling without automatic retries, unknown-state readouts, re-preparation,
   and restart without session restoration. Do not induce powered wiring faults.
10. **Shutdown and cutoff:** verify last PWM retention on service shutdown, document
    host-failure behavior and actuator settling, and validate the physical support and
    cutoff procedure. Confirm removing pulses/power cannot drop an unsupported load.

Do not enable manual hardware configuration until initialization, hold, fault,
recovery and cutoff behavior have been physically reviewed. Passing these trials
does not enable PCA9685 automated retrieval.

## Independent Jog preparation and recovery

Jog requires no commissioning records or measured geometry. Its nominal 500–2500 µs
range is not a verified mechanical range. Speed remains limited to 100 µs/s.
Disconnect ALL servo power and support the arm, acknowledge this in Jog, and select
**Prepare for Jog**. This verifies the claw supply is off, archives an inactive
unfinished commissioning trial without accepting it, clears recoverable interlocks,
and programs 1500 µs on all six channels. An active owner must end its session first.
Apply power using the bench procedure, check readiness, then explicitly enable manual
control. A sensor fault never automatically resumes motion. Cut physical servo power
and support the arm before repeating preparation.

INA219 raw readings remain mandatory before enabling output and throughout movement
and powered holding. Verified current must stay below 2.5 A in absolute value;
unverified amperes and cutoff must be labeled unverified. Mock readings are simulated.
Monitoring and PWM shutdown do not constitute a physical current limiter.

Current status includes read time, sample gap, worker delay, motion-lock wait,
persistence duration, and queued telemetry count. Sample logging runs outside the arm
I/O lock with a bounded queue; logging failure or queue exhaustion latches a fault.
Use these diagnostics to investigate stale samples without extending freshness limits.
