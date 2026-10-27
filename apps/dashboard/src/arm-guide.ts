export const armParts = [
  { name: "Base", channel: 5, joint: "base", storedName: null,
    description: "The rotating platform fixed to the table.", x: 1040, y: 575, labelX: 1280, labelY: 780 },
  { name: "Upper arm", channel: 4, joint: "joint_2", storedName: "link_4",
    description: "The section between the first joint above the base and the next joint.", x: 1000, y: 315, labelX: 1260, labelY: 190 },
  { name: "Middle arm", channel: 3, joint: "joint_3", storedName: "link_3",
    description: "The long section between the upper arm and the wrist section.", x: 835, y: 365, labelX: 460, labelY: 200 },
  { name: "Wrist section", channel: 2, joint: "joint_4", storedName: "link_2",
    description: "The section between the middle arm and the rotating claw mount.", x: 770, y: 555, labelX: 330, labelY: 390 },
  { name: "Claw mount", channel: 1, joint: "joint_5", storedName: "link_1",
    description: "The rotating connector immediately behind the claw.", x: 650, y: 615, labelX: 270, labelY: 600 },
  { name: "Claw", channel: 0, joint: "claw", storedName: "claw",
    description: "The gripping fingers and their mechanism. Measure at your custom full-open pulse, with channels 1–5 at 1500 µs. Enclose the full opening and closing range.", x: 535, y: 720, labelX: 280, labelY: 910 },
] as const;

export function channelLabel(channel: number) {
  const part = armParts.find(part => part.channel === channel);
  return `${part?.name ?? "Joint"} · servo ${channel + 1} · channel ${channel}`;
}

export function knownPart(name: string, parentChannel: number) {
  return armParts.find(part => part.storedName === name
    && (part.channel === 0 ? parentChannel === 1 : part.channel === parentChannel));
}

export function mountArmGuide(root: HTMLElement, select: (name: string | null) => boolean) {
  root.innerHTML = `
    <h3>Find your arm parts</h3>
    <p class="caption">Select a labeled part to open its ROS joint measurements in step 3. Each card names its parent and child links; their boxes are in step 2.</p>
    <p class="caption">Channels use indices 0–5; servo numbers run from 1–6. Claw = servo 1 / channel 0. Base = servo 6 / channel 5.</p>
    <figure class="arm-guide-figure">
      <div class="arm-guide-photo">
        <img src="/images/dfrobot-rob0036-dark.png" width="1536" height="1024"
          alt="DFRobot ROB0036 V2: the base is on the right, the arm folds upward and back down toward the claw on the lower left." />
        <svg viewBox="0 0 1560 1040" aria-hidden="true">${armParts.map(part => `
          <line x1="${part.labelX}" y1="${part.labelY}" x2="${part.x}" y2="${part.y}" />
          <circle cx="${part.x}" cy="${part.y}" r="9" />`).join("")}</svg>
        ${armParts.map((part, index) => `<button type="button" class="arm-callout" data-part="${index}"
          style="left:${part.labelX / 15.6}%;top:${part.labelY / 10.4}%" aria-label="${part.name}, servo ${part.channel + 1}, channel ${part.channel}"><span>CH ${part.channel} · Servo ${part.channel + 1}</span><span>${part.name}</span></button>`).join("")}
      </div>
      <figcaption class="caption">Photo: <a href="https://wiki.dfrobot.com/rob0036/" target="_blank" rel="noreferrer">DFRobot ROB0036 V2</a>, background edited for dark mode.
        Reference photo only, not a verified neutral pose. Follow step 1 for the ROS zero pose; use the claw’s measured full-open pulse only when checking its envelope.</figcaption>
    </figure>
    <div class="arm-guide-legend">${armParts.map((part, index) => `<button type="button" class="secondary" data-part="${index}">
      <strong>${channelLabel(part.channel)}</strong><span>${part.description}</span></button>`).join("")}</div>
    <p id="arm-guide-selection" class="caption" role="status"></p>`;
  root.querySelectorAll<HTMLButtonElement>("[data-part]").forEach(button => {
    button.onclick = () => {
      const index = Number(button.dataset.part);
      const part = armParts[index];
      root.querySelectorAll<HTMLButtonElement>("[data-part]").forEach(item => {
        item.setAttribute("aria-pressed", String(item.dataset.part === String(index)));
      });
      const found = select(part.storedName);
      root.querySelector<HTMLElement>("#arm-guide-selection")!.textContent = found
        ? `${part.name} selected. Its ROS joint card uses local parent-frame coordinates.`
        : `The ${part.name.toLowerCase()} ROS card is not loaded. Wait for the model to load, then select again.`;
    };
  });
  return {
    showPart(name: string, reveal = false) {
      const index = armParts.findIndex(part => part.storedName === name);
      root.querySelectorAll<HTMLButtonElement>("[data-part]").forEach(button => {
        button.setAttribute("aria-pressed", String(Number(button.dataset.part) === index));
      });
      const part = armParts[index];
      root.querySelector<HTMLElement>("#arm-guide-selection")!.textContent = part
        ? `${channelLabel(part.channel)} selected. ${part.description}` : "This custom part has no photo callout.";
      if (reveal && part) {
        root.scrollIntoView({ block: "start" });
        root.querySelector<HTMLButtonElement>(`.arm-callout[data-part="${index}"]`)!.focus({ preventScroll: true });
      }
    },
  };
}
