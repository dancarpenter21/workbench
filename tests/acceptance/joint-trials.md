# DF Robot v2 driver acceptance

Software checks use explicit mock mode or injected devices. They do not establish
physical acceptance. No calibration notebook or generic arm-model editor is required.

1. Open Config. Confirm fixed channels (base 5 through claw 0), independent bounds,
   and the established 0.1 Ω shunt are displayed. Confirm saved harness limits survive
   reload and partial edits preserve other endpoints. Clear/undo and validation must
   work. Saving must not start a session, enable outputs, or move the arm.
2. Confirm model endpoint/name, prompt, tools, and claw target inputs are in Config.
   Diagnostics must show readings, device checks, and explicit recovery without
   persistent numeric-setting or calibration-record forms.
3. With an operator present, support the arm, disconnect servo power for preparation,
   clear the neutral path, and arrange immediate physical power cutoff. Verify fresh
   raw INA219 readings before output. Verify the effective scale in current details;
   configuration display alone does not prove the active scale.
4. Use Manual's existing preparation, direct pulse moves, keyboard jogging, and Stop.
   Preserve fixed assembly bounds. Use small supervised moves with known clearance;
   independently bounded combinations can still collide. Pulse commands are not
   measured joint position.
5. Verify INA219 monitoring continues during movement and powered holding. Verified
   abs(current) >= 2.5 A and missing/stale/invalid/failed samples must stop motion and
   attempt PWM shutdown; cut physical servo power. Exercise faults with injected
   software sensors, not deliberate physical stalls or overcurrent. Mock readings
   stay labeled simulated; unverified scales stay labeled unverified.
6. Verify exclusive ownership, browser disconnect/watchdog stopping, explicit fault
   recovery, and no automatic replay or motion retry. Hardware restarts require
   disconnected servo power and support. Saving settings cannot clear a fault.
7. Exercise the LLM harness through the ROS gateway with an explicitly enabled
   supervised session and saved limits. Confirm one-joint pulse commands and explicit
   stop/end behavior. Completion reports commanded targets, not arrival or grip.
   Automatic retrieval and physical Cartesian planning remain separately gated.

Record actual observations and failures directly in the issue or associated bench
notes. Keep physical results separate from mock software results.
