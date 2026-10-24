# Initialization and commissioning checklist

The **Initialization** tab combines pulse-bound controls with a commissioning checklist.
Open **Checklist** beside the heading to read the guidance and track progress.

The checklist automatically checks externally verified current scale and saved minimum/
maximum bounds for all six joints. Tick the remaining review items after documenting:
hardware/support/cutoff; safe starting pulses and power-on procedure; observed directions;
tested per-joint rates; stop/disconnect/recovery behavior; and the reviewed Jog configuration.
Review checkboxes are editable only after powered trials are finished. They persist across
browsers/restarts for the current domain, assembly identity and hardware setup, independently
of temporary trial observations. You can uncheck a review when it needs repeating.

Checked reviews record your confirmation, not independent measurements. The checklist does
not modify motion configuration or enable Jog or retrieval. Simulated progress is labelled
and never establishes physical acceptance. Repeat relevant physical reviews after changing
the assembly, load or procedure. Current-scale setup is linked directly to Diagnostics.
The automatic-retrieval section explains the additional motion design and acceptance work.

Choose a joint and use **Move** or a **±1, ±10, ±20, ±40 μs** button. Each step
button commands its full change immediately. The first move in each session initializes
all six servos to the established neutral request of **1500 μs**, then applies the
selected joint's requested change. Opening the tab never moves the arm. Other joints
hold their last command.

Observe the arm and press **OK** or **Not OK** after every move. After OK, use
**Set minimum** or **Set maximum** to capture the current tested pulse without moving.
Recapturing explicitly replaces that endpoint; minimum must be below maximum. The
joint selectors show the latest saved bounds, or **Not set**. These results persist
across sessions and restarts, separately for physical/simulated domains and hardware
setup/assembly identity. The active current-scale verification supplies assembly identity;
without one, the identity is “Assembly not named.” Physical setup changes select a
separate set; mock results never establish physical limits.

The initial exploration range is **1450–1550 μs**. Near an edge, check clearance,
acknowledge it, then use **Extend lower/upper range by 50 μs**. Extending causes no
movement and does not change saved bounds. Exploration stays within 500–2500 μs and
resets each session. Every move is limited to **40 μs**, at least **250 ms** apart;
requests are never split into automatic steps. Isolated trials through the existing
API retain their 10 μs limit. Pulse commands are not measured angles. PWM resolution
is approximately 4.88 μs, so a 1 μs change may produce the same output.

**Not OK** records the result, attempts PWM shutdown and ends motion; cut physical
servo power and support the arm. PWM shutdown can release loads. **Stop — hold
position** retains the last target. Support the arm, disconnect servo power and confirm
**Finish** before another session. Blur, disconnect or restart never resumes motion;
finish interrupted sessions before starting again.

There are no operator forms, record selectors, history or export controls in this tab.
Trial evidence and telemetry remain available through the existing commissioning APIs.
Current-scale verification is under **Diagnostics**. Direction remains unknown unless
recorded separately. Captured bounds do not automatically configure Jog, qualify combined
paths/rates, or enable automatic retrieval.

The claw INA219 at **0x41** is read before any output initialization and throughout
all neutral writes, movement and powered holding, including stopped sessions without
a browser. Missing, stale, invalid or failed readings abort testing and attempt PWM
shutdown. Cut physical servo power. With an externally verified shunt resistance,
**abs(current) >= 2.5 A** trips the monitor. Otherwise raw shunt readings remain visible,
and amperes and cutoff remain explicitly unverified. Polling and PWM shutdown are not
a physical current limiter.

Neutral initialization never clears a sensor fault. After inspection, finish any
interrupted session, disconnect power and use the visible **Reset sensor fault** control.
Reset requires the power-off/support acknowledgment and a fresh healthy reading with
claw supply at most 0.5 V. No movement is retried automatically.

Physical use requires the workbench profile, hardware mode, PCA9685 backend and its
commissioning gate. Channel mapping is base 5, joints 2–5 on 4–1, and claw 0.
Follow the established safe-neutral procedure and continuous supervision. Simulated
results do not satisfy physical acceptance.

The shared header shows HAT presence and live raw ammeter readings on every tab.
HAT detection establishes register communication, not servo power or position.
