import * as THREE from "three";

const ranges = {
  x: [-2.7, 2.7, "纵向"],
  y: [-2.2, 2.2, "横向"],
  z: [-.4, 3, "垂向"],
};

export function sectionPlane(renderer, workbench, onChange) {
  const toggle = document.querySelector("#section-toggle");
  const controls = document.querySelector("#section-controls");
  const axisControl = document.querySelector("#section-axis");
  const positionControl = document.querySelector("#section-position");
  const positionText = document.querySelector("#section-value");
  const flip = document.querySelector("#section-flip");
  const plane = new THREE.Plane();
  const planes = [plane], noPlanes = [];
  let enabled = false, axis = "x", position = 0, flipped = false;

  function update() {
    const sign = flipped ? 1 : -1;
    plane.normal.set(0, 0, 0);
    plane.normal[axis] = sign;
    plane.constant = -sign * position;
    renderer.clippingPlanes = enabled ? planes : noPlanes;
    toggle.setAttribute("aria-pressed", String(enabled));
    controls.hidden = !enabled;
    flip.setAttribute("aria-pressed", String(flipped));
    positionText.textContent = axis.toUpperCase() + " = " + position.toFixed(2) +
      " m · 保留" + (flipped ? "正" : "负") + "侧";
    workbench.dataset.sectionAxis = enabled ? axis : "";
    workbench.dataset.sectionPosition = String(position);
    workbench.dataset.sectionFlipped = String(flipped);
    onChange();
  }
  function togglePlane() {
    enabled = !enabled;
    update();
  }
  function changeAxis() {
    axis = axisControl.value;
    position = 0;
    syncPosition();
    update();
  }
  function syncPosition() {
    const [min, max, label] = ranges[axis];
    positionControl.min = min;
    positionControl.max = max;
    positionControl.value = position;
    positionControl.setAttribute("aria-valuetext", label + " " + position.toFixed(2) + " 米");
  }
  function movePlane() {
    position = Number(positionControl.value);
    positionControl.setAttribute("aria-valuetext", ranges[axis][2] + " " + position.toFixed(2) + " 米");
    update();
  }
  function flipPlane() {
    flipped = !flipped;
    update();
  }
  toggle.disabled = false;
  toggle.addEventListener("click", togglePlane);
  axisControl.addEventListener("change", changeAxis);
  positionControl.addEventListener("input", movePlane);
  flip.addEventListener("click", flipPlane);
  workbench.dataset.sectionAxis = "";
  return {
    get enabled() { return enabled; },
    capture() { return {enabled, axis, position, flipped}; },
    accepts(state) {
      if (!state || !Object.hasOwn(ranges, state.axis)) return false;
      const [min, max] = ranges[state.axis];
      return typeof state.enabled === "boolean" && typeof state.flipped === "boolean" &&
        Number.isFinite(state.position) && state.position >= min && state.position <= max;
    },
    restore(state) {
      ({enabled, axis, position, flipped} = state);
      axisControl.value = axis;
      syncPosition();
      update();
    },
    contains(point) { return !enabled || plane.distanceToPoint(point) >= -1e-6; },
    clipBounds(box) {
      if (enabled && !box.isEmpty()) {
        if (flipped) box.min[axis] = Math.max(box.min[axis], position);
        else box.max[axis] = Math.min(box.max[axis], position);
      }
      return box;
    },
    dispose() {
      toggle.removeEventListener("click", togglePlane);
      axisControl.removeEventListener("change", changeAxis);
      positionControl.removeEventListener("input", movePlane);
      flip.removeEventListener("click", flipPlane);
      renderer.clippingPlanes = noPlanes;
    },
  };
}
