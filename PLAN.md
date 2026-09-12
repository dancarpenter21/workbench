# Robotic Workbench — plan continuation

## Objective and current state

Extend the six-application monorepo from fixed, recorded pickup sequences to
camera-guided tool retrieval, then introduce wireless arm control.

The existing implementation provides independently runnable vision, voice, assistant,
coordinator, arm-controller and dashboard applications; shared API contracts; durable
operation tracking; mock devices; and local hardware/model adapters. The simulated
workflow, browser workflow and 30 simulated deliveries have passed testing.

Current vision checks occur before and after a recorded movement sequence. Continuous
visual corrections, physical calibration, real-model accuracy and physical acceptance
trials remain outstanding. Switching the transport alone will not add visual guidance.

## Proposed transport decision

Use USB serial to establish the physical control baseline, then add ordinary Wi-Fi as
the first wireless transport. Keep the webcam connected to the vision computer over USB
initially. If the camera later needs wireless connectivity, carry its video over a
separate Wi-Fi connection to the computer.

The arm link carries small movement targets and joint/status feedback. It does not need
to carry webcam frames. For illustration, 100-byte targets at 20 updates per second use
2 KB/s per arm before protocol overhead; feedback adds traffic in the other direction.
Frame age, delay variation and lost updates matter more than this modest payload rate.

| Transport | Proposed role | Reason |
| --- | --- | --- |
| USB serial | Baseline and diagnostic fallback | Already implemented; isolates control and vision issues from wireless behavior. |
| Wi-Fi | First wireless arm transport | Direct computer integration and an existing RoArm-M2-S HTTP command/feedback interface. |
| ESP-NOW | Alternative if access-point-free operation becomes a requirement | Suitable for compact messages, but requires a computer-side gateway and a verified bidirectional implementation. |
| Zigbee | Defer | Low-power mesh networking offers little benefit for the initial powered workbench; unsuitable for the planned webcam video stream. |

Waveshare documents that its supplied RoArm-M2-S ESP-NOW interface does not return arm
feedback. This is a limitation of that implementation, not of ESP-NOW itself. An ESP-NOW
version would need firmware changes or an arm-side bridge, plus a USB-connected ESP32
gateway at the computer. Verify the installed firmware before relying on any interface.

Sources: [Waveshare arm interfaces](https://www.waveshare.com/product/roarm-m2-s.htm),
[HTTP control and feedback](https://www.waveshare.com/wiki/RoArm-M2-S_Python_HTTP_Request_Communication),
[ESP-NOW documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html),
[Zigbee fundamentals](https://docs.silabs.com/zigbee/8.2.0/zigbee-fundamentals/01-overview).

## Application changes

### Vision

- Add camera calibration and mapping from image coordinates into a named workbench
  coordinate frame. Start with a fixed overhead camera, a planar bench, known tool
  heights and constrained orientations.
- Extend observations with frame identity, capture time, target position/orientation,
  confidence and calibration identity. Preserve missing/uncertain classifications.
- Detect the tool's grasp location and, where feasible, a visible gripper marker so the
  controller can measure approach error. Use joint feedback when the gripper occludes
  the target; halt visual approach if the required observations become unreliable.
- Process the newest available frame rather than accumulating old frames. Record
  capture-to-observation latency and validate detection with held-out bench images.

### Arm controller

- Separate transport from motion policy so USB and Wi-Fi share validation, limits,
  feedback handling, operation identity and recovery behavior.
- Retain recorded safe approach/retreat paths while adding bounded incremental target
  updates during the visual approach phase. Validate inverse kinematics, reachability,
  joint limits and the permitted approach region before dispatch.
- Keep servo regulation and motion limits local to the arm. Host vision supplies slower
  target corrections; the LLM does not participate in the repeating control loop.
- Include operation/session identity, sequence number and validity interval in target
  updates. Define freshness enforcement without assuming unsynchronized clocks agree.
  Reject stale, duplicate and out-of-order updates; replace superseded targets rather
  than replaying a backlog.
- Require application-level command acknowledgments and measured joint feedback.
  A successful network send does not establish that a movement executed.
- Implement an arm-side watchdog that enters a tested safe state when commands expire
  or communication disappears. Determine whether the stock firmware can enforce these
  rules; if not, implement them in firmware or an arm-side controller before wireless
  physical trials. A watchdog on the host alone is insufficient.

### Coordinator and contracts

- Extend retrieval phases to distinguish locating, approaching, aligning, grasping,
  lifting, delivering and verifying. Persist the high-level operation, not every visual
  correction as a separate retrieval.
- Require compatible coordinate frames, calibration identities and fresh observations
  before alignment. Bound the time and number of corrections, and fail into recovery
  when convergence or required feedback is lost.
- Keep one retrieval active across the workbench initially. Multi-arm addressing may
  be added to contracts, but simultaneous motion requires a separate shared-workspace
  collision policy and is deferred.
- Update shared Pydantic models, exported OpenAPI and generated TypeScript types together.

### Dashboard, voice and assistant

- Display the selected target, approach error, observation age, transport status and
  retrieval phase so the operator can inspect visual guidance.
- Preserve explicit stop/recovery controls and transcript review. Voice and assistant
  continue producing high-level requests; neither gains direct joint-control access.

## Implementation milestones

1. **Qualify the physical bench over USB.** Select three lightweight tools, mount the arm
   and camera, measure payload/reach/grip suitability, and validate recorded pickup and
   delivery paths. Establish physical cutoff, stop and recovery behavior with supervision.
2. **Calibrate and locate tools.** Collect camera references and validation images,
   calibrate image-to-bench coordinates, and report target poses without moving the arm.
   Measure localization error against physical measurements.
3. **Close the visual loop over USB.** Add bounded approach corrections at low speed.
   Target 10–20 visual updates per second initially, subject to measured hardware capacity.
   Verify convergence, occlusion handling and feedback-loss behavior before grasping.
4. **Introduce Wi-Fi.** Add a transport adapter using the documented arm interface for
   baseline measurements. Complete the required arm-side freshness/watchdog behavior,
   then compare the same physical tasks over USB and Wi-Fi. Do not assume stock HTTP
   control meets the desired update rate or failure semantics.
5. **Run acceptance trials.** Measure accuracy, latency and failures, then document the
   qualified tool set, supported workspace and operating conditions. Revisit ESP-NOW
   only if a concrete deployment requirement or measured Wi-Fi limitation warrants it.

## Validation and acceptance

- Record camera capture, vision completion, command dispatch/acknowledgment, joint
  feedback and final placement times. Report median, 95th-percentile and worst observed
  end-to-end delay, plus update loss and age; average latency alone is insufficient.
- Set permitted motion speed, observation age, approach tolerance and watchdog deadline
  from measured stopping behavior and localization uncertainty before physical closed-loop
  operation. The 10–20 Hz target is a development goal, not a real-time guarantee.
- Test packet delay/loss/reordering, duplicate commands, broken connections, service and
  controller restarts, wrong tools, stale images, camera movement, occlusion, occupied trays,
  unreachable targets and failure to converge. Expired targets must never resume after
  reconnection. Recovery must require inspection and a new valid operation.
- Verify local watchdog behavior by removing the wireless link while the computer cannot
  send a stop. Test the physical cutoff with the arm supported and unloaded before loaded
  trials; loss of power or torque may allow the arm or tool to fall.
- Require at least 27 successful physical deliveries across 30 trials, with ten trials
  per tool and zero wrong-tool deliveries. Keep simulated and physical results separate.
- Repeat a full request offline after model installation and validate camera selection
  with a second USB webcam before claiming multi-camera compatibility.

## Assumptions and decision gates

- Retain the initial $500 hardware budget, excluding the existing computer. Reassess it
  if measured payload requirements or an arm-side controller require different hardware.
- Start with one arm, one camera, a fixed bench, a small known tool catalog, supervised
  operation and delivery into a tray. Cluttered picking, direct handoffs and simultaneous
  multi-arm movement remain out of scope for this phase.
- The Wi-Fi recommendation is a proposed next step, not an implemented transport choice.
  Confirm the purchased arm and its installed firmware before implementing its adapter.
- Existing application APIs are unauthenticated and bound to loopback. Keep them local
  while adding the device link. If deploying applications across computers, add explicit
  authentication, authorization and secure transport rather than exposing the current APIs.
- If ESP-NOW is selected later, budget for gateway/firmware development, implement
  bidirectional telemetry and message authentication, and plan radio channels explicitly.
  ESP-NOW sharing a radio with infrastructure Wi-Fi must use that access point's channel.
  See [Espressif's coexistence guidance](https://docs.espressif.com/projects/esp-faq/en/latest/application-solution/esp-now.html).
