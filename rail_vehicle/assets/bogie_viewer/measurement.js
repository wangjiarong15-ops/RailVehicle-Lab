import * as THREE from "three";

// The scene root uses the assembly's metre-based, Z-up world coordinates.
export function measurement(scene, invalidate) {
  const overlay = new THREE.Group();
  scene.add(overlay);
  const markerGeometry = new THREE.SphereGeometry(.022, 12, 8);
  const markerMaterials = [0x64e2f6, 0xffe784, 0xf5a4ba].map((color) =>
    new THREE.MeshBasicMaterial({color, depthTest: false, depthWrite: false, toneMapped: false}));
  const markers = markerMaterials.map((material) => {
    const marker = new THREE.Mesh(markerGeometry, material);
    marker.visible = false;
    marker.renderOrder = 21;
    overlay.add(marker);
    return marker;
  });
  const positions = new THREE.BufferAttribute(new Float32Array(9), 3);
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute("position", positions);
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x64e2f6, depthTest: false, depthWrite: false, toneMapped: false,
  });
  const line = new THREE.Line(lineGeometry, lineMaterial);
  line.visible = false;
  line.renderOrder = 20;
  line.frustumCulled = false;
  overlay.add(line);

  let count = 0, target = 2;
  function getState() {
    if (count !== target) return {count, target, distance: null, delta: null, angle: null};
    const delta = markers[1].position.clone().sub(markers[0].position);
    if (target === 2) return {count, target, distance: delta.length(), delta: delta.toArray(), angle: null};
    const arm = markers[2].position.clone().sub(markers[1].position);
    delta.negate();
    const angle = Math.min(delta.lengthSq(), arm.lengthSq()) > 1e-12
      ? Math.atan2(new THREE.Vector3().crossVectors(delta, arm).length(), delta.dot(arm)) * 180 / Math.PI
      : null;
    return {count, target, distance: null, delta: null, angle};
  }
  function clear() {
    if (!count) return getState();
    count = 0;
    for (const marker of markers) marker.visible = false;
    line.visible = false;
    invalidate();
    return getState();
  }
  return {
    setMode(mode) {
      target = mode === "angle" ? 3 : 2;
      return clear();
    },
    add(point) {
      if (![point.x, point.y, point.z].every(Number.isFinite)) return getState();
      if (count === target) clear();
      const marker = markers[count];
      marker.position.copy(point);
      marker.visible = true;
      positions.setXYZ(count, point.x, point.y, point.z);
      count++;
      line.visible = count >= 2;
      lineGeometry.setDrawRange(0, count);
      if (line.visible) positions.needsUpdate = true;
      invalidate();
      return getState();
    },
    getState,
    clear,
    dispose() {
      overlay.removeFromParent();
      markerGeometry.dispose();
      for (const material of markerMaterials) material.dispose();
      lineGeometry.dispose();
      lineMaterial.dispose();
    },
  };
}
