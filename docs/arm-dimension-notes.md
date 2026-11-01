# Initial model from Arm-Dimension-Notes.jpg

Source: the operator-provided `test-results/Arm-Dimension-Notes.jpg`. The photograph
is not copied into tracked source. The operator confirmed the dimensions and all-1500 µs neutral pose, and
corrected the handwritten tip height to 13 in. This remains an incomplete model,
not physical calibration or execution qualification.

| Annotation | Confirmed reading | Intended use |
| --- | --- | --- |
| Shoulder pivot height | 3¾ in | Height above the drawn baseline; confirm baseline is tabletop |
| Shoulder to elbow | 3¼ in | Candidate pivot-centre separation |
| Elbow height | 6½ in | Observation of the drawn pose, not a link length |
| Elbow to wrist bend | 3 in | Candidate pivot-centre separation |
| Wrist bend height | 8 11/16 in | Confirmed by operator |
| Long distal section | 7¼ in | Channel 2 axle to claw tip, confirmed |
| Distal height | 13 in (corrected) | Claw tip at neutral, confirmed |
| Claw | 4 in wide, 2 in thick | Envelope dimensions; axial length and full sweep still needed |
| Claw centre | Y = −1 in | Frame/axis interpretation must be confirmed |
| Claw rotation axis | Y = −½ in | Frame/axis interpretation must be confirmed |
| Base top view | 1500 µs right, 500 µs diagonally left | Orientation observation, not calibrated angular bounds |

The draft uses neutral frames parallel to the base axes, with X/Z pivot offsets
reconstructed from the confirmed heights and lengths. Zero lateral offsets and
shoulder/base-yaw co-location are explicit provisional planar assumptions. The
wrist-roll origin and tool datum stay unset. The remaining joint-axis calibration, speed/effort, measured angular bounds, uncertainty allowances,
base/servo/bracket widths, wrist-roll pivot and tool datum remain incomplete.
Heights in a bent drawing must not be entered as successive local link lengths.
No pulse-to-angle mapping is inferred from the sketch.

Import `config/workbench/arm-dimension-notes.draft.json` through **Model → Import
model draft** in the physical evidence domain. Importing is reversible and does
not save, activate or command the arm. Complete the missing dimensions and frames
in the editor, then save a draft. A simulated workspace will reject a physical
measurement file rather than silently changing its provenance.

`arm-dimension-notes.reference.urdf` supplies fixed neutral reference frames for
RViz (enable link axes). The dashboard also shows their dashed side-view polyline.
Neither representation contains measured collision volumes.
