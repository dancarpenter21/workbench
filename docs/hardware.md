# Hardware setup and calibration

## Initial shopping budget

Target an existing GPU computer plus at most $500 in new hardware:

| Item | Allowance |
| --- | ---: |
| Waveshare RoArm-M2-S, suitable power supply and mounting | $300 |
| Logitech C270 USB camera with microphone | $30 |
| Fixed camera mount, lighting, tool holders, tray, gripper padding and accessible cutoff | $70 |
| Shipping, tax, cables and contingency | $100 |

These are budget allowances, not a validated checkout quote. Confirm kit contents,
dimensions, supply requirements, payload at actual reach, and gripper opening before
ordering. Start with three lightweight tools. A full-size wrench has not been qualified.
Sources: [arm options](https://www.waveshare.com/product/roarm-m2-s.htm),
[camera](https://www.logitech.com/en-us/shop/p/c270-hd-webcam).

## Local dependencies and models

Install only the optional adapters you need, or all four for the complete physical setup:

```sh
uv sync --extra vision --extra arm --extra voice --extra assistant
```

`llama-cpp-python` may require a native toolchain and a GPU-specific build; the default
installation can use CPU inference. Place a compatible chat/instruction GGUF file at
`models/assistant.gguf` and a faster-whisper/CTranslate2 model directory at
`models/whisper/`. Models are not bundled or downloaded by the applications. Obtain
models with appropriate licenses before offline use. Configure `llm.gpu_layers`,
`llm.context_size`, `speech.device`, and `speech.compute_type` for the actual machine.
The shipped settings use CPU inference so they do not assume a GPU backend or VRAM size.
Measure combined model memory use and latency before enabling GPU offload.

The workspace constrains NumPy below 2.4 for the development host's older Phenom II CPU.
NumPy 2.4 raises its [CPU baseline to x86-64-v2](https://numpy.org/doc/2.4/reference/simd/build-options.html),
which this host does not support. Keep this constraint when using the same machine.

Copy `config/workbench/default.json` to the ignored `config/workbench/local.json`, and
create a local runtime settings file if changing camera, serial or model options. Set
`WORKBENCH_CALIBRATION` and `WORKBENCH_CONFIG` to absolute paths. No physical setup is
enabled by default. The arm refuses motion while `arm.calibrated` is false.

## Vision

1. Fix the camera and lighting. Set the camera device index or device path, resolution,
   and four `[x, y, width, height]` regions: one for each source and one for the tray.
2. Collect cropped color reference images for **every label in every region**: `empty`,
   `adjustable_wrench`, `screwdriver`, and `pliers`. This allows wrong-tool rejection.
   Set `vision.references[region][label]` to each image path, relative to the repository
   or absolute. Use fixed orientations and backgrounds; tool holders should constrain
   position. The arm gripper must be clear of regions during verification.
3. Run vision alone in hardware mode; inspect `/preview` and `/observations`. Tune
   `match_threshold` and `match_margin` using separate validation images. Uncertain matches
   block pickup. Similarity scores are not statistically calibrated probabilities.
4. Test absent tools, swapped tools, displaced tools, shadows, camera disconnection and
   an occupied tray. Confirm a tool is recognized at the tray only when its source is empty.
5. Repeat camera selection with a second USB webcam before claiming multi-camera support.

The initial classifier compares resized color crops with reference images. It is suitable
only for a controlled bench. It is not a trained general-purpose tool detector. A camera
move, lighting change, new background or changed tool requires recalibration.

## Arm

Use the manufacturer's manual controls with supervision to establish a start/home pose
and pickup, lift, delivery, release and return poses for each tool. Do not copy example
angles from another bench. Record each pose as an object with `base`, `shoulder`, `elbow`,
and `hand` in radians in `arm.sequences[tool_id]`. First and last poses must be identical.
Every path must have at least three poses; include all needed approach/clearance poses.
Set `joint_limits` to measured bounds for this layout and leave speed low initially.
Each recipe must begin at its recorded home pose; software does not automatically home
from an unknown starting position.

The adapter sends JSON `T:102` with nonzero bounded speed and acceleration over USB,
requests `T:105` feedback, and checks `T:1051` joint angles. It requires three consecutive
in-tolerance readings before advancing. It does not detect arbitrary obstacles, certify
payload, or infer grasp success from servo position. Calibrated path review must establish
clearance through the entire motion, not just at endpoints.

Software Stop stops issuing waypoints and requests a hold at fresh feedback angles. If
serial feedback/control is lost, software cannot guarantee a stop: use the physical cutoff.
Do not rely on the browser as an emergency stop. Test the physical cutoff with the arm
supported and no load, since power loss can release the tool or allow the arm to fall.

The inspected upstream firmware's `T:0` command disables torque for ten seconds and then
reenables it; this adapter intentionally does not send it. Firmware revisions may differ.
Verify feedback fields and hold behavior on the installed firmware before enabling paths.
References: [joint command documentation](https://www.waveshare.com/wiki/RoArm-M2-S_Robotic_Arm_Control),
[firmware implementation](https://github.com/waveshareteam/roarm_m2/blob/main/RoArm-M2_example/RoArm-M2_module.h).

Only after reviewing and testing the paths set `arm.calibrated` to true. Start with:

```sh
WORKBENCH_CALIBRATION="$PWD/config/workbench/local.json" uv run --no-sync python scripts/dev.py --mode hardware
```

Keep the work area clear, inspect the arm, acknowledge recovery, and test a single tool.
Stopping or an uncertain outcome requires manual inspection and recovery; nothing replays.
Physical acceptance remains **unperformed** until the hardware is available.
