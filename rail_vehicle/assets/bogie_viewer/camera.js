import * as THREE from "three";
import {OrbitControls} from "./vendor/OrbitControls.js";

const angles = {
  "整体视图": [5, -6.8, 3.7], "侧视图": [0, -1, 0], "正视图": [1, 0, 0],
  "俯视图": [0, 0, 1], "底视图": [0, 0, -1],
};
const halfHeight = 1.55;

export function cameraRig(canvas, invalidate) {
  const camera = new THREE.OrthographicCamera(-3, 3, halfHeight, -halfHeight, .05, 80);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = .13;
  controls.enablePan = true;
  controls.rotateSpeed = .72;
  controls.zoomSpeed = .8;
  controls.minZoom = .34;
  controls.maxZoom = 7;
  controls.addEventListener("change", invalidate);
  let aspect = 1;
  function resize(width, height) {
    const previous = aspect;
    aspect = width / height;
    const adjustment = Math.min(1, aspect / 1.6) / Math.min(1, previous / 1.6);
    camera.zoom = THREE.MathUtils.clamp(camera.zoom * adjustment, controls.minZoom, controls.maxZoom);
    camera.left = -halfHeight * aspect;
    camera.right = halfHeight * aspect;
    camera.updateProjectionMatrix();
  }
  function fit(box, vector, horizontal) {
    const center = box.getCenter(new THREE.Vector3());
    // Clear residual drag/pan before setting the new preset.
    controls.enableDamping = false;
    controls.update();
    controls.target.copy(center);
    // A slight lean defines screen up at the poles while preserving OrbitControls' Z-up.
    if (horizontal) vector.y = -.0001;
    camera.position.copy(center).add(vector.normalize().multiplyScalar(9));
    camera.lookAt(center);
    controls.update();
    controls.enableDamping = true;
    camera.updateMatrixWorld();
    let x = 0, y = 0;
    for (const a of [box.min.x, box.max.x]) for (const b of [box.min.y, box.max.y])
      for (const c of [box.min.z, box.max.z]) {
        const point = new THREE.Vector3(a, b, c).applyMatrix4(camera.matrixWorldInverse);
        x = Math.max(x, Math.abs(point.x));
        y = Math.max(y, Math.abs(point.y));
      }
    camera.zoom = THREE.MathUtils.clamp(.91 * halfHeight / Math.max(y, x / aspect, .01), .34, 7);
    camera.updateProjectionMatrix();
    invalidate();
  }
  return {
    camera, controls, resize,
    reset(view, args) {
      const [x, y, z] = angles[view] ?? angles["整体视图"];
      const extentX = args.rails ? 2.25 : 1.90 + .30 * args.spread;
      const extentY = 1.40 + .46 * args.spread;
      const box = new THREE.Box3(new THREE.Vector3(-extentX, -extentY, args.rails ? -.37 : -.045),
        new THREE.Vector3(extentX, extentY, 1.49 + .93 * args.spread));
      fit(box, new THREE.Vector3(x, y, z), view === "俯视图" || view === "底视图");
    },
    focus(box) {
      const offset = camera.position.clone().sub(controls.target);
      fit(box, offset, false);
    },
  };
}
