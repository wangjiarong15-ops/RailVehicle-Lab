import * as THREE from "three";
import {mergeGeometries} from "./vendor/utils/BufferGeometryUtils.js";

// Batch primitives that share a transform while keeping the original meshes for picking.
export function playbackBatches(assembly, model) {
  const material = new THREE.MeshLambertMaterial({vertexColors: true});
  const groups = new Map();
  const axles = new Map();
  const local = new THREE.Matrix4();
  assembly.pose(0, 0, 0, 0);
  for (const [id, {spec, meshes, damperRod, rodMeshes}] of model.parts) {
    let anchor = assembly.partNodes.get(id).parent;
    if (spec.spin) {
      const x = spec.spin[0];
      if (!axles.has(x)) axles.set(x, anchor);
      anchor = axles.get(x);
    } else if (spec.parent !== "links") {
      anchor = assembly.layers[spec.parent];
    }
    for (const mesh of meshes) {
      // The piston slides independently of the cylinder once the batch is built.
      const transform = rodMeshes?.has(mesh) ? damperRod : anchor;
      transform.updateWorldMatrix(true, false);
      if (!groups.has(transform)) groups.set(transform, {sources: [], mesh: null});
      const batch = groups.get(transform);
      mesh.updateWorldMatrix(true, false);
      local.copy(transform.matrixWorld).invert().multiply(mesh.matrixWorld);
      const original = mesh.geometry;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", original.getAttribute("position").clone());
      geometry.setAttribute("normal", original.getAttribute("normal").clone());
      if (original.index) geometry.setIndex(original.index.clone());
      else geometry.setIndex(Array.from({length: geometry.attributes.position.count}, (_, i) => i));
      geometry.applyMatrix4(local);
      const colors = new Float32Array(geometry.attributes.position.count * 3);
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      batch.sources.push({mesh, geometry, colors});
    }
  }

  function clear() {
    for (const [anchor, batch] of groups) {
      if (!batch.mesh) continue;
      anchor.remove(batch.mesh);
      batch.mesh.geometry.dispose();
      batch.mesh = null;
    }
  }

  return {
    update(active) {
      clear();
      for (const [anchor, batch] of groups) {
        const visible = [];
        for (const {mesh, geometry, colors} of batch.sources) {
          mesh.layers.set(active ? 1 : 0);
          if (!active || !mesh.visible || !mesh.parent.visible) continue;
          const {r, g, b} = mesh.material.color;
          for (let i = 0; i < colors.length; i += 3) {
            colors[i] = r;
            colors[i+1] = g;
            colors[i+2] = b;
          }
          visible.push(geometry);
        }
        if (visible.length) {
          batch.mesh = new THREE.Mesh(mergeGeometries(visible), material);
          anchor.add(batch.mesh);
        }
      }
    },
    dispose() {
      clear();
      material.dispose();
      for (const {sources} of groups.values()) {
        for (const {geometry} of sources) geometry.dispose();
      }
    },
  };
}
