/** Display-only measurement help. Nothing in this module commands hardware. */
export type Illustration = "pose" | "pivot" | "box" | "claw";
const descriptions: Record<Illustration, string> = {
  pose: "Schematic side view of the arm on a table, with its elbow bent and claw open. This is not a verified 1500 microsecond pose.",
  pivot: "A link between circular bearing centres: measure from the centre of one rotation axis to the centre of the next, not from screw edges.",
  box: "A translucent collision box encloses a link, its servo and brackets. Measure the full extent along each box axis.",
  claw: "Open fingers and ghosted closed fingers inside an enclosing box: include the whole swept mechanism, not just the empty gripping gap.",
};
export function illustration(kind: Illustration) {
  return `<figure class="measurement-figure"><div class="measurement-art measurement-art-${kind}"><img src="/images/measurement-guide.png" alt="${descriptions[kind]}" loading="lazy" /></div>
    <figcaption>${descriptions[kind]} <a href="/images/measurement-guide.png" target="_blank" rel="noreferrer">Enlarge illustration sheet</a></figcaption></figure>`;
}

const diagramDescriptions = {
  "origin": "Side view: measure signed X and Z offsets from the parent origin to the next pivot centre; measure Y sideways separately. The diagonal is not the X offset.",
  "box": "Collision box: sizes span the entire box along its own rotated X, Y and Z axes. Its centre offset starts at the link origin, which need not be at the box centre.",
  "oriented-box": "Side view: a narrow box follows a component tilted 30 degrees upward. Its box X axis is rotated relative to link X; box centre remains expressed in link coordinates. Pitch is negative for upward tilt from X toward Z.",
  "capsule": "Capsule A and B are the centres of its rounded ends; radius extends outward from their centre line, including beyond both ends.",
  "rotation": "Right handed axes: X along reach, Y left, Z up. Positive roll turns Y toward Z; positive pitch turns Z toward X; positive yaw turns X toward Y."
} as const;
export function measurementDiagram(kind: keyof typeof diagramDescriptions) {
  return `<figure class="measurement-figure"><a href="/images/measurement-${kind}.svg" target="_blank" rel="noreferrer" aria-label="Enlarge diagram: ${diagramDescriptions[kind]}"><img class="measurement-diagram" src="/images/measurement-${kind}.svg" alt="${diagramDescriptions[kind]}" width="560" height="270" loading="lazy" /></a><figcaption>${diagramDescriptions[kind]} Select diagram to enlarge.</figcaption></figure>`;
}

export function poseGuide() {
  return `<section id="measurement-pose" class="measurement-step" tabindex="-1">
    <h3>1. Position the arm and mark your reference</h3>
    <p>Have a ruler, square, removable tape and an angle gauge or protractor ready. Fix the base to the table and remove any held tool. Mark the rotation centres on the brackets; a link’s edge is not its pivot.</p>
    ${illustration("pose")}
    <ol>
      <li><strong>Establish the zero pose:</strong> use the monitored pulse controls in step 4 to set channels 1–5 to 1500 µs. For a new ROS model, use 1500 µs for the claw’s zero too. Record how your assembled arm actually sits; do not bend it to match the drawing.</li>
      <li><strong>Mark the table frame:</strong> (0, 0, 0) is where the base’s vertical rotation axis meets the tabletop. Mark +X along the arm’s reach projected on the table at base neutral. Looking outward along +X, +Y is left; +Z is up.</li>
      <li><strong>Mark local origins:</strong> for a new ROS model, each child link begins at the joint pivot that attaches it to its parent. One simple new-model convention is to keep joint/link axes parallel to the table axes at zero, then rotate each collision box to follow its component in step 2. Link frames and their boxes move together with the joints later. Use the existing frame convention when editing a saved model.</li>
      <li><strong>Measure the claw envelope separately:</strong> use your observed full-open pulse, then check the whole opening/closing sweep. 1500 µs is only a midpoint command, not “fully open.” Keep the other joints at their reference. Return the claw to zero for joint-origin measurements.</li>
    </ol>
    <p class="measurement-note">A new motion session first commands <strong>all six servos to 1500 µs</strong>. Check clearance and support before using Move. Powered holding requires continuous INA219 readings at 0x41, including while measuring. On missing, stale, invalid or failed readings, stop, attempt PWM shutdown and cut physical servo power; do not retry motion automatically. Use |current| &lt; 2.5 A only with a verified scale; otherwise raw shunt voltage remains mandatory and amperes/cutoff are unverified. Polling is not a physical current limiter. Mock readings are simulated.</p>
    <p>Use a square to project pivots onto the table and a ruler for heights. Measure offsets along each axis separately. Keep fingers and measuring tools clear of moving joints. Saving or browsing this guide never positions the arm.</p>
  </section>`;
}

export function orientationGuide() {
  return `<div class="measurement-help">
    <p><strong>Joint origin:</strong> start at the parent link’s marked origin; end at this joint’s pivot centre. Enter signed X, Y and Z distances along the <em>parent’s</em> axes in inches. A diagonal pivot-to-pivot length alone is not three coordinates. For the claw’s gripping reference point, measure to the chosen point between the fingers instead of a pivot.</p>
    ${illustration("pivot")}${measurementDiagram("origin")}
    <p><strong>Mounting orientation:</strong> roll, pitch and yaw orient the joint frame at zero. They are not the current servo angle. With the new-model convention in step 1, aligned frames have roll = pitch = yaw = 0°, even if a metal link slopes. Enter zeros only when you have chosen and checked that alignment. Existing models may use different frames.</p>
    ${measurementDiagram("rotation")}
    <p><strong>Axis direction:</strong> a unit vector along the joint’s rotation shaft, expressed in its joint frame. (1, 0, 0) means +X; (0, 1, 0) means +Y; (0, 0, 1) means +Z. Check the actual shaft and positive direction, especially the claw mechanism. Do not derive an axis from the direction a link points.</p>
    <details><summary>Rotation mathematics and worked examples</summary>
      <p>Use column vectors. Fixed-axis roll about parent X, then pitch about parent Y, then yaw about parent Z gives <code>R = Rz(yaw) Ry(pitch) Rx(roll)</code>. The rightmost matrix acts first. At joint zero, <code>p_parent = origin + R × p_joint</code>; × here means matrix multiplication. Joint movement adds a separate rotation about the joint’s axis.</p>
      <pre>Rx(r) = [1, 0, 0; 0, cos r, −sin r; 0, sin r, cos r]
Ry(p) = [cos p, 0, sin p; 0, 1, 0; −sin p, 0, cos p]
Rz(y) = [cos y, −sin y, 0; sin y, cos y, 0; 0, 0, 1]</pre>
      <p>Example only: origin (2, 0, 3) inches and yaw +90° maps joint point (1, 0, 0) to parent point (2, 1, 3) inches. Roll +90° turns +Y toward +Z. Pitch +90° turns +X toward −Z. For a compound rotation, apply the matrices in the stated order; adding the three angles does not work.</p>
      <p>To check orientation physically, mark the parent and joint axes with tape at zero. Aligned arrows mean 0/0/0. A frame turned a quarter turn counterclockwise viewed from +Z looking toward the origin has yaw +90°. Use a square/protractor to check each axis; compound orientations need the full frame transform, not three independent link tilts.</p>
      <p>Internal units: radians = degrees × π / 180; metres = inches × 0.0254. For example, 90° = π/2 radians. Exports use metres and radians.</p>
    </details>
  </div>`;
}

export function nominalAngle(channel: number, pulse: number): { shaft: number; displacement: number } | null {
  if (!Number.isFinite(pulse) || pulse < 500 || pulse > 2500) return null;
  const slope = channel === 5 ? 0.135 : 0.09;
  return { shaft: (pulse - 500) * slope, displacement: (pulse - 1500) * slope };
}
export function nominalText(channel: number, pulse: number) {
  const value = nominalAngle(channel, pulse);
  return value ? `Nominal shaft ${Number(value.shaft.toFixed(2))}° · from midpoint ${value.displacement > 0 ? "+" : ""}${Number(value.displacement.toFixed(2))}° (unverified)`
    : "Enter a pulse from 500–2500 µs to see the nominal angle.";
}
export function calibrationGuide() {
  return `<section id="measurement-calibration" class="measurement-step" tabindex="-1"><h3>4. Compare pulses and measured angles</h3>
    <p><a href="https://wiki.dfrobot.com/rob0036/" target="_blank" rel="noreferrer">DFRobot ROB0036 V2 specifications</a> give the following nominal shaft rotation. These are reference estimates, not measured joint position or safe assembled-arm limits.</p>
    <table class="geometry-axis-table"><thead><tr><th>Servo</th><th>500 µs</th><th>1500 µs</th><th>2500 µs</th></tr></thead><tbody>
      <tr><td>Base · CH 5</td><td>0°</td><td>135°</td><td>270°</td></tr><tr><td>Other servos · CH 0–4</td><td>0°</td><td>90°</td><td>180°</td></tr></tbody></table>
    <p>Nominal displacement from midpoint: <code>(pulse − 1500) × 0.135°</code> for the base; <code>(pulse − 1500) × 0.09°</code> for the others. Nominal shaft angle uses <code>pulse − 500</code> instead. Installed joint direction may reverse the sign; the claw linkage does not turn shaft degrees directly into jaw opening.</p>
    <p>For the existing capsule guard, record measured pulse/angle pairs for channels 1–4 in its advanced section below. Base and claw references remain informational. These samples do not calibrate ROS execution.</p>
    <ol><li>At 1500 µs, align or zero your gauge between the parent and moving link. This is 0° relative joint angle, even if the link is tilted relative to the table.</li>
      <li>Change only the joint being measured using Move, within previously verified clearance and limits. Keep all other commanded pulses at reference; do not sweep to manufacturer endpoints.</li>
      <li>Read relative rotation using the gauge/protractor centred on the shaft. Sign it using the axis’s right-hand rule. Record the pulse and observed angle; repeat at safe points on both sides of neutral.</li>
      <li>Use at least three samples including (1500, 0). Order pulses increasingly; measured angles must strictly increase or strictly decrease. Include observed repeatability/backlash in the angle-error allowance. Unknown measurements stay blank.</li></ol>
  </section>`;
}
