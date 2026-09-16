# Raspberry Pi workbench shopping list

Updated 2026-09-15 for the selected Raspberry Pi 5 / DFRobot ROB0036 V2 build.
This replaces the old RoArm shopping budget for purchasing purposes. Quantities are
build requirements; ownership and order status have not been recorded. Check existing
parts and the final kit contents before ordering duplicates. Prefer ready-made leads
and connections where they meet the required ratings.

## Core components

| Qty | Item / source | Selection and purchase notes |
| ---: | --- | --- |
| 1 | [DFRobot ROB0036 V2 arm](https://www.dfrobot.com/product-192.html) | Selected. Includes six servos and installed gripper; controller and supply are separate. |
| 1 | [Raspberry Pi 5 via DFRobot KIT-003](https://www.dfrobot.com/kit-003.html) | Selected kit family. Confirm 4 GB versus 8 GB, regional power adapter, cooling/case, display and accessories in the configured order. RAM needs for the local models remain unmeasured. |
| 1 | [Waveshare Servo Driver HAT, SKU 15275](https://www.waveshare.com/servo-driver-hat.htm) | Selected regular, straight-header PCA9685 board. Confirm Pi 5 cooler/case clearance and mounting. |
| 1 | [Raspberry Pi Camera Module 3](https://www.raspberrypi.com/products/camera-module-3/) | Selected camera family. Standard versus wide remains pending bench framing; use the visible-light version for the planned color-reference workflow. |
| 1 | [Official Standard-Mini camera cable, 300 mm](https://www.raspberrypi.com/products/camera-cable/) | 15-pin camera end to 22-pin Pi 5 end. Confirm routing length; the camera's standard supplied ribbon does not provide this Pi 5 connection. |
| 1 | [ServoCity PDB, 3108-2827-0801](https://www.servocity.com/8-channel-servo-power-node/) | Selected eight-channel distribution board. XT30 power connection; protection and mating lead still need selection. |
| 1 | [Mean Well LRS-100-5](https://www.meanwell.com/Upload/PDF/LRS-100/LRS-100-SPEC.PDF) | Selected 5 V / 18 A / 90 W servo supply. Final power assembly remains pending the items below. |
| 1 | 7-inch display | Selected size; exact model remains open. KIT-003 links to [this HDMI touchscreen](https://www.dfrobot.com/product-1655.html), a candidate to verify before ordering. Count it once if included in the kit. |

The arm's [manufacturer packing information](https://wiki.dfrobot.com/rob0036/)
includes six short 20 cm servo extensions and assembly accessories. Check those first
for the local PDB-to-arm connections; they do not cover the 4-6-foot overhead run.
Factory assembly does not supply workbench calibration or qualified tool paths.

## Accessories to include or confirm

| Qty | Item | Purchase condition |
| ---: | --- | --- |
| 1 | Pi 5 power adapter | Confirm the kit's Pi 5-rated adapter and regional plug. Keep Pi power separate from servo power. |
| 1 set | Pi cooling, accessible case/mount and HAT standoffs | Count kit parts first. Confirm the combined cooler/HAT/camera/display fit before selecting extra spacers or a GPIO extension. |
| 1 | microSD boot card | Check kit inclusion. Size for Raspberry Pi OS, local models, references and logs; capacity remains to be selected. |
| 1, if needed | microSD reader | Reuse an existing reader for OS installation. See [Pi setup requirements](https://www.raspberrypi.com/documentation/computers/getting-started.html). |
| 1 set | Display video, touch and power connections | Match the chosen display and Pi 5. For the linked HDMI candidate, verify micro-HDMI video, USB touch connection and power budget; do not assume bundled rigid adapters fit. |
| 6 | Approximately 4-6-foot PWM/ground harnesses | Verify both ends' contacts and polarity before purchase. Carry signal and ground only between HAT and PDB; leave the positive conductor disconnected. |
| Up to 6 | Short PDB-to-servo extensions | Buy only any shortfall after checking the arm's included leads and actual table routing. |
| 1 set | Rigid overhead Pi/display/camera mounting | Include camera bracket, fasteners and cable strain relief. Keep CSI local to the Pi and the camera fixed. |
| 1 set | Arm base fastening and supported-test fixture | Match the measured base and bench; provide support for unloaded commissioning and power-loss tests. |
| 1 set | Consistent bench lighting | Reuse suitable lighting where possible; assess shadows and reflections in the camera view. |
| 3 / 1 / 1 set | Tool holders / delivery tray / gripper padding | Size after checking tool geometry, reach and grip. Start with three lightweight tools already available where possible. |
| 1, if needed | USB microphone or existing compatible audio input | Needed at the computer running browser voice input. Check any existing webcam/headset microphone before buying. |
| 1 set | Cable clips, labels and protective sleeving | Count included accessories first; keep signal routing and moving joints clear. |

## Servo-power assembly: select before ordering

These are required parts of the proposed supply installation, with exact SKUs and
ratings still pending the physical design described in the
[hardware guide](hardware.md#wiring-and-power-boundaries).

| Qty | Item | Detail to resolve |
| ---: | --- | --- |
| 1 set | Supply-to-PDB power lead | Correct mating XT30 connection, polarity, conductor rating and suitable supply-terminal termination. Favor a properly rated preterminated lead. |
| 1 set | Overcurrent protection and holder(s) | Coordinate with the supply, PDB, each wiring path and measured load. Exact device, rating and placement remain unset. |
| 1 assembly | Accessible physical servo-power cutoff | Select contacts and wiring for the actual switched circuit and load; validate restart behavior and supported-arm response. |
| 1 assembly | Supply enclosure and mounting | Include covered mains terminals, protective-earth connection, cord/inlet, strain relief, appropriate terminations and ventilation. The proposed printed box alone is not a completed mains assembly. |

The [PDB is rated for 15 A continuous](https://www.servocity.com/8-channel-servo-power-node/),
while the [supply is rated for 18 A](https://www.meanwell.com/Upload/PDF/LRS-100/LRS-100-SPEC.PDF).
The supply's overload protection does not establish protection for the PDB or wiring.
All PDB signal-sharing switches must remain OFF for six independent joints. See the
hardware guide for the current budget and power-separation details before motor testing.

Have a multimeter and access to suitable PWM waveform measurement equipment for
commissioning; borrow existing equipment where possible. Long-harness signal quality
must be checked under motor load before relying on it.

## Price snapshot

USD manufacturer listings checked on 2026-09-15. Shipping, tax, regional charges and
configuration changes are excluded. Recheck at checkout; these are not reserved prices.

| Item | Listed price | Basis |
| --- | ---: | --- |
| [ROB0036 V2 arm](https://www.dfrobot.com/product-192.html) | $259.00 | Product listing |
| [Waveshare 15275 HAT](https://www.waveshare.com/servo-driver-hat.htm) | $14.99 | Indexed manufacturer listing; direct page access was blocked during this check |
| [ServoCity PDB](https://www.servocity.com/8-channel-servo-power-node/) | $17.99 | Product listing |
| Subtotal: arm + HAT + PDB only | **$291.98** | Excludes all other components and accessories |
| [DFRobot KIT-003](https://www.dfrobot.com/kit-003.html) | $203.40 displayed | Configurable page; selected options and final bundle price are not established |
| [Camera Module 3](https://www.raspberrypi.com/products/camera-module-3/) | From $25.00 | Manufacturer starting price; lens variant and reseller quote pending |
| Camera cable, servo supply and remaining accessories | TBD | Obtain quotes after the choices above |

The complete build total remains open. Add the configured kit, chosen camera, cable,
servo supply, any accessories not already owned/included, and shipping/tax to the
$291.98 subtotal. The previous $500 allowance applied to a different RoArm/GPU-PC
build and is not the budget for this assembly.

## Purchases waiting on design decisions

- Extra feedback sensors, a second camera, and watchdog/controller hardware depend on
  the [completion and stop design](arm-control.md). No specific part is selected yet.
- Full-size or heavy tools depend on measured payload, reach and grip; the servo's
  advertised torque is not a tool payload rating.
- The old RoArm arm, its supply and the old USB-camera allowance are historical
  alternatives, not additional requirements for this build.

Software development since the hardware selection adds no mandatory controller or
sensor SKU. It makes the existing commissioning needs explicit. Automated PCA9685
retrieval remains disabled pending physical validation.
