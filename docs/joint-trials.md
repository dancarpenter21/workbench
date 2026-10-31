# DF Robot v2 positioning and configuration

Use **Manual** for preparation, keyboard jogging, and direct pulse positioning.
Preparation requests 1500 µs on all six channels. Later moves retain other targets.
Faults require inspection and explicit recovery through Diagnostics.

Use **Config** for saved harness pulse limits, claw open/close targets, and LLM
endpoint, model, prompt, and tools. Saving limits never opens an output device or
moves the arm. Existing saved limits remain in their existing store. Manual uses
fixed independent assembly bounds; the harness additionally requires saved bounds.

Wiring is fixed: base 5, shoulder 4, elbow 3, wrist bend 2, claw rotation 1, claw 0.
The physical Pi configuration records the operator-verified 0.1 Ω INA219 shunt.
Persisted active scale records override launch settings; inspect live current details
for the effective scale. The app does not silently overwrite those records.

**Diagnostics** shows live readings, device checks, reports, and explicit recovery.
It has no scale-entry form. ROS-model and capsule editors are removed from the normal
UI; their backend validation and planned-execution gates remain intact.

Every physical claw output and powered hold requires INA219 readings at 0x41 before
output and continuously thereafter. Missing, stale, invalid, or failed readings stop
motion and attempt PWM shutdown; cut physical servo power. Verified current at
abs(current) >= 2.5 A trips software shutdown. Raw shunt readings remain available;
unverified and simulated readings remain labeled. Sampling and shutdown have latency.

Pulses are commands, not measured position. Independent bounds do not prevent joint
combinations from colliding. No calibration notebook is required for the pulse driver
or supervised LLM harness. Physical planned execution and automatic retrieval remain
gated separately. No hardware services are automatically restarted by UI changes.
