# Initialization: direct joint values

Initialization contains six rows: Base, Joints 2–5, and Claw. Each row has editable
**Pulse**, **Min**, and **Max** fields in microseconds and a **Move** button.
**Save limits** stores any entered limits; unfinished joints can remain blank. There are no step buttons,
range-extension controls, feedback prompts, checklist, or recovery panels on this tab.
Errors appear only when loading or an action fails. Live ammeter readings remain in
the shared header, and simulated data is labelled SIMULATED.

Click **Move** on a row to command that joint's entered pulse. The first move of a
new session initializes all six joints to the established 1500 μs neutral request,
then applies the entered pulse. Confirm that starting arrangement is safe. Later
moves affect only the requested joint; other outputs retain their targets. Direct
entry accepts 500–2500 μs independently of saved Jog limits; Min/Max can remain blank. It does not use the old
40 μs step limit, split moves into steps, or retry failed commands. Repeated commands
remain at least 250 ms apart. Pulses are commands, not measured joint positions.

Edit any minimum or maximum and click **Save limits**. Blank fields preserve existing
values; you can save one joint or one endpoint at a time. Each completed range must
include 1500 μs, have minimum below maximum, fit inside 500–2500 μs, and contain a
representable PWM pulse. Saving never moves the arm or opens an output device. Values persist for
the current physical/simulated domain, assembly and hardware setup. Manually entered
limits are marked `source: manual`; they are not fabricated trial observations.
Jog uses saved limits after its output preparation. Automatic retrieval remains disabled.

Leaving the page, losing focus, disconnecting, or pressing the shared Stop ends motion
ownership and retains the last PWM target. A later explicit Move can resume a normally
stopped session from its retained targets. It does not return to neutral in that case.
When a stopped session cannot resume, an explicit Move starts a fresh monitored neutral
initialization. The previous session remains in history with unverified observations.
Latched sensor faults still require explicit recovery before movement.
Use **Diagnostics → Motion interlock**: inspect the interruption, support the arm,
disconnect servo power, acknowledge that preparation, then clear the stopped session
and reset monitoring. A reset requires a fresh valid sensor reading and never moves
the arm. Faults are never cleared automatically by Move or Save limits.

Every physical session reads the claw INA219 at **0x41** before enabling output and
continuously during movement and powered holding. Missing, stale, invalid or failed
readings stop testing and attempt PWM shutdown; cut physical servo power. With a
verified shunt resistance, **abs(current) >= 2.5 A** aborts testing. Otherwise raw
shunt voltage remains visible and amperes and the cutoff are labelled unverified.
Polling and PWM shutdown are not a physical current limiter.

The channel mapping is Base 5, Joints 2–5 on 4–1, and Claw 0. Physical tests use hardware
mode with the PCA9685 backend. Simulated tests do not establish physical acceptance.
The commissioning APIs retain trial histories, feedback, and bounded step testing for
commissioning tools; the Initialization page uses direct value entry.
