# Physical manual-control acceptance

Run only after the hardware and commissioning procedures and an initial review of
`docs/manual-controls.md`. Mock/browser tests establish software behavior only.
Record physical observations, assembly/configuration version, observer, timestamps,
load, and pass/fail evidence in the commissioning records. Keep runtime evidence private.

1. **Disconnected initialization:** support the arm, disconnect servo power, verify
   independent channels and cutoff. Start the workbench profile with manual configuration.
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
6. **Xbox/browser:** on the actual browser and OS, test USB and the intended Bluetooth
   connection, controller discovery/selection, neutral stick drift, held bumper,
   proportional input, precision, joint changes, B Stop, disconnect and reconnect.
   Verify HTTPS/localhost access and unsupported-mapping guidance where applicable.
7. **Ownership:** try a second browser and a retrieval request during manual control;
   both must be refused. During retrieval, manual acquisition must be refused.
8. **Loss and hold:** release direction; end manual control; change tabs; move focus
   outside the panel; hide/close the page; interrupt network access. Measure target
   update cessation and observe actual actuator settling. Confirm network loss triggers
   the server watchdog and requires recovery when release cannot arrive.
9. **Faults and restart:** with appropriate support and power procedures, verify output
   failure handling without automatic retries, unknown-state readouts, re-preparation,
   and restart without session restoration. Do not induce powered wiring faults.
10. **Shutdown and cutoff:** verify last PWM retention on service shutdown, document
    host-failure behavior and actuator settling, and validate the physical support and
    cutoff procedure. Confirm removing pulses/power cannot drop an unsupported load.

Do not enable manual hardware configuration until initialization, hold, fault,
recovery and cutoff behavior have been physically reviewed. Passing these trials
does not enable PCA9685 automated retrieval.
