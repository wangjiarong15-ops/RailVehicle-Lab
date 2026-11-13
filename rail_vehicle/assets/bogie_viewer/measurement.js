import * as THREE from "three";

// The scene root uses the assembly's metre-based, Z-up world coordinates.
export function measurement(scene, invalidate) {
  const overlay = new THREE.Group();
  scene.add(overlay);
  const markerGeometry = new THREE.SphereGeometry(.022, 12, 8);
  const markerMaterials = [0x64e2f6, 0xffe784].map((color) =>
    new THREE.MeshBasicMaterial({color, depthTest: false, depthWrite: false, toneMapped: false}));
  const markers = markerMaterials.map((material) => {
    const marker = new THREE.Mesh(markerGeometry, material);
    marker.visible = false;
    marker.renderOrder = 21;
    overlay.add(marker);
    return marker;
  });
  const positions = new THREE.BufferAttribute(new Float32Array(6), 3);
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

  let count = 0;
  function getState() {
    if (count !== 2) return {count, distance: null, delta: null};
    const delta = markers[1].position.clone().sub(markers[0].position);
    return {count, distance: delta.length(), delta: delta.toArray()};
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
    add(point) {
      if (![point.x, point.y, point.z].every(Number.isFinite)) return getState();
      if (count === 2) clear();
      const marker = markers[count];
      marker.position.copy(point);
      marker.visible = true;
      positions.setXYZ(count, point.x, point.y, point.z);
      count++;
      line.visible = count === 2;
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
