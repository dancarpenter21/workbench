# Position testing in Pulse test

Choose a joint, enter a **Position (μs)**, and press **Move**. There are no Start or
Prepare screens. The first Move in each new session initializes all six servos to the
operator-established neutral request of **1500 μs**, then applies the requested small
move. Merely opening the tab or restarting the app never moves the arm.

Observe the arm and press **OK** or **Not OK**. OK records the position and allows
another move. Not OK records the result, attempts PWM shutdown and ends motion;
cut physical servo power and support the arm. PWM shutdown can release loads.
**Stop** retains the last target. Support the arm, disconnect servo power and confirm
**Finish** before another session. Reopening an interrupted session requires finish;
it does not reset to neutral or replay movement automatically.

Each explicit move remains limited to 10 μs, at least 250 ms apart, inside the active
trial range (initially 1450–1550 μs). A large request is rejected rather than split
into automatic steps. All six servos hold while the selected joint is tested.
The six-neutral initialization is an explicit user-commanded startup action, not an
assertion that measured joint positions have settled. The neutral request uses the
operator-established safe neutral procedure for this bench.

**Setup & history** contains optional names, saved records, range edits, current-scale
verification, recorded OK/Not OK results and export. Records are created automatically
without requiring labels. Current-scale verification's assembly identity is reused
when configured. Simple feedback records direction as unknown rather than inferring
it from pulse changes. It does not qualify travel limits, combined paths, rates,
physical position or automatic retrieval. Existing detailed records/APIs remain
readable; automatic retrieval stays disabled.

The claw's INA219 at 0x41 is read before any output initialization and throughout all
six neutral writes, movement and powered holding, including stopped sessions without
a browser. Missing, stale, invalid or failed readings abort testing and attempt PWM
shutdown. Cut physical servo power. With a verified scale, abs(current) >= 2.5 A trips
the monitor; otherwise amperes and the cutoff remain explicitly unverified. Polling
and PWM shutdown are not a physical current limiter.

Neutral initialization never clears a sensor fault. After an inspected fault, finish
any interrupted session, disconnect servo power, and use **Reset sensor fault** under
Setup & history. This requires an explicit power-off/support acknowledgment and a fresh
healthy reading with claw supply at most 0.5 V. No movement is retried automatically.

Physical use still requires the workbench profile, hardware mode, PCA9685 backend and
its commissioning gate. Channel mapping is base 5, joints 2–5 on 4–1, and claw 0.
Simulated readings and results remain labelled and are not physical acceptance.
