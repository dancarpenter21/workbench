import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import URDFLoader from "urdf-loader";

type Vector = (number | null)[] | null;
export type PreviewModel = {
  source_notes?: { reference_polyline_m?: number[][]; reference_polyline_label?: string };
  links: { name: string; size?: Vector; xyz?: Vector; rpy?: Vector }[];
  joints: { name: string; type: string; parent: string; child: string; xyz: Vector; rpy: Vector;
    axis?: Vector; lower?: number | null; upper?: number | null }[];
};
export type RenderedPreview = { urdf: string; omissions: string[]; visible_boxes: number };

export function mountModelPreview(host: HTMLElement, onPoseChanged: () => void = () => {}, onLinkSelected: (name: string) => void = () => {}) {
  host.innerHTML = `<h3>3D draft preview</h3><p>Preview only · sliders do not move the arm · inspect the displayed pose using Check collisions</p><label><input type="checkbox" class="model-collision-toggle" /> Show collision allowances</label><div class="model-camera-actions"></div><div class="model-canvas"></div><p role="status" class="model-preview-status"></p><div class="model-sliders"></div>`;
  const status = host.querySelector<HTMLElement>(".model-preview-status")!;
  const canvas = host.querySelector<HTMLElement>(".model-canvas")!;
  let renderer: THREE.WebGLRenderer;
  try { renderer = new THREE.WebGLRenderer({ antialias: true }); }
  catch { status.textContent = "3D rendering unavailable in this browser. Measurements can still be edited."; return { positions() { return {}; }, update(_model: PreviewModel, _draft: RenderedPreview) {}, setVisible(_visible: boolean) {} }; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  canvas.append(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x17212d);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.001, 100);
  camera.up.set(0, 0, 1); camera.position.set(0.65, -0.65, 0.5);
  const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, 0.15);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x668899, 3));
  const grid = new THREE.GridHelper(1, 20, 0x607080, 0x334455); grid.rotation.x = Math.PI / 2; scene.add(grid);
  scene.add(new THREE.AxesHelper(0.12));
  let robot: ReturnType<URDFLoader["parse"]> | null = null;
  const angles = new Map<string, number>();
  let referenceLine: THREE.Line | null = null;
  const render = () => renderer.render(scene, camera);
  const showCollisions = host.querySelector<HTMLInputElement>(".model-collision-toggle")!;
  function applyCollisionVisibility() {
    robot?.traverse(object => {
      if ((object as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) {
        object.visible = showCollisions.checked;
      }
    }); render();
  }
  showCollisions.onchange = applyCollisionVisibility;
  controls.addEventListener("change", render);
  let pointerStart = [0, 0];
  renderer.domElement.addEventListener("pointerdown", event => { pointerStart = [event.clientX, event.clientY]; });
  renderer.domElement.addEventListener("pointerup", event => {
    if (!robot || Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 5) return;
    const bounds = renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1), camera);
    for (const hit of ray.intersectObject(robot, true)) {
      let object: THREE.Object3D | null = hit.object; let name: string | null = null; let visible = true;
      while (object) {
        if (!object.visible) visible = false;
        const frame = object as THREE.Object3D & { isURDFLink?: boolean; urdfName?: string };
        if (frame.isURDFLink && name === null) name = frame.urdfName ?? null;
        object = object.parent;
      }
      if (visible && name && !name.startsWith("scene_")) { onLinkSelected(name); break; }
    }
  });
  const resize = () => { const width = canvas.clientWidth; if (!width) return; renderer.setSize(width, 360); camera.aspect = width / 360; camera.updateProjectionMatrix(); controls.update(); render(); };
  new ResizeObserver(resize).observe(canvas);
  for (const [name, position] of [["Perspective", [0.65, -0.65, 0.5]], ["Front", [0, -0.8, 0.15]], ["Side", [0.8, 0, 0.15]], ["Top", [0, 0, 0.9]]] as const) {
    const button = document.createElement("button"); button.type = "button"; button.textContent = name;
    button.onclick = () => { camera.position.set(position[0], position[1], position[2]); controls.update(); render(); };
    host.querySelector(".model-camera-actions")!.append(button);
  }
  return {
    positions() { return Object.fromEntries(angles); },
    setVisible(visible: boolean) { if (visible) resize(); },
    update(model: PreviewModel, draft: RenderedPreview) {
      if (referenceLine) { scene.remove(referenceLine); referenceLine.geometry.dispose(); (referenceLine.material as THREE.Material).dispose(); referenceLine = null; }
      const points = model.source_notes?.reference_polyline_m;
      if (Array.isArray(points) && points.length >= 2 && points.every(point => Array.isArray(point) && point.length === 3 && point.every(value => typeof value === "number" && Number.isFinite(value)))) {
        referenceLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points.map(point => new THREE.Vector3(point[0], point[1], point[2]))), new THREE.LineDashedMaterial({ color: 0xffdd66, dashSize: 0.006, gapSize: 0.003 }));
        referenceLine.computeLineDistances(); scene.add(referenceLine);
      }
      if (robot) { scene.remove(robot); robot.traverse(object => { if (object instanceof THREE.Mesh || object instanceof THREE.Line) { object.geometry.dispose(); const materials = Array.isArray(object.material) ? object.material : [object.material]; materials.forEach(m => m.dispose()); } }); }
      const loader = new URDFLoader(); loader.parseCollision = true;
      robot = loader.parse(draft.urdf); scene.add(robot);
      for (const [name, link] of Object.entries(robot.links)) if (!name.startsWith("scene_")) link.add(new THREE.AxesHelper(0.025));
      for (const name of angles.keys()) if (!robot.joints[name] || robot.joints[name].jointType === "fixed") angles.delete(name);
      robot.traverse(object => {
        if ((object as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) {
          object.traverse(child => { if (child instanceof THREE.Mesh) { const previous = Array.isArray(child.material) ? child.material : [child.material]; previous.forEach(material => material.dispose()); child.material = new THREE.MeshBasicMaterial({ color: 0xffaa44, transparent: true, opacity: 0.22, wireframe: true }); } });
        }
      }); applyCollisionVisibility();
      status.textContent = `${draft.visible_boxes} measured boxes shown. ${draft.omissions.join("; ")} Grid spacing: 50 mm. Axes: red X, green Y, blue Z. ${referenceLine ? model.source_notes?.reference_polyline_label ?? "Sketch reference only; not collision geometry." : ""}`;
      const sliders = host.querySelector(".model-sliders")!; sliders.replaceChildren();
      for (const joint of model.joints) {
        if (joint.type === "fixed" || !robot.joints[joint.name] || robot.joints[joint.name].jointType === "fixed") continue;
        const label = document.createElement("label");
        const input = document.createElement("input"); input.type = "range"; input.min = String(joint.lower); input.max = String(joint.upper); input.step = "0.001";
        input.value = String(Math.max(joint.lower!, Math.min(joint.upper!, angles.get(joint.name) ?? 0)));
        input.setAttribute("aria-label", `${joint.name} simulated angle`);
        const value = document.createElement("span");
        const change = () => { const angle = Number(input.value); angles.set(joint.name, angle); onPoseChanged(); robot!.setJointValue(joint.name, angle); value.textContent = `${joint.name}: ${(angle * 180 / Math.PI).toFixed(1)}°`; render(); };
        input.oninput = change; label.append(value, input); sliders.append(label); change();
      }
      resize(); render();
    },
  };
}
