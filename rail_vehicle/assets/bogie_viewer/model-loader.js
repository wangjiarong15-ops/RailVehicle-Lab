import * as THREE from "three";

const close = (a, b) => a.distanceTo(new THREE.Vector3(...b)) < .0001;
// Distinguish rolled steel and painted bevels without changing the baked mesh or paint colour.
const surfaceRoughness = new Map([["paint_edge", .38], ["steel", .34]]);

function inspect(gltf, contract) {
  const root = new THREE.Group();
  // Blender's glTF exporter writes Y-up. Restore the workbench's Z-up axes once.
  root.rotation.x = Math.PI / 2;
  root.add(gltf.scene);
  root.updateMatrixWorld(true);
  const assembly = gltf.scene.getObjectByName("rail_bogie");
  if (contract.version !== 1 || contract.units !== "m" ||
      assembly?.userData.rail_contract !== contract.version || assembly.userData.rail_units !== "m")
    throw new Error("Invalid bogie contract");
  const layers = new Map(contract.layers.map((id) => {
    const layer = assembly.children.find((child) => child.name === "rail_" + id);
    if (!layer) throw new Error("Missing assembly layer: " + id);
    return [id, layer];
  }));
  const entries = Object.entries(contract.parts);
  const actual = [...layers.values()].reduce((sum, layer) => sum + layer.children.length, 0);
  if (entries.length !== actual || entries.length === 0) throw new Error("Unexpected assembly part count");
  const parts = new Map(entries.map(([id, spec]) => {
    const node = layers.get(spec.parent)?.children.find((child) => child.name === id);
    if (!node || node.userData.rail_part !== id || node.userData.rail_group !== spec.group ||
        !close(node.getWorldPosition(new THREE.Vector3()), spec.origin))
      throw new Error("Invalid part or pivot: " + id);
    const meshes = [];
    node.traverse((item) => { if (item.isMesh) meshes.push(item); });
    if (!meshes.length) throw new Error("Missing part mesh: " + id);
    const entry = {node, spec, meshes};
    if (id.startsWith("damper_") && node.children.length !== 1) {
      const body = node.children.find((child) => child.userData.rail_damper_segment === "body");
      const rod = node.children.find((child) => child.userData.rail_damper_segment === "rod");
      // GLTFLoader represents a mesh with several material primitives as a Group.
      if (node.children.length !== 2 || !body || !rod || ![body, rod].every((child) =>
        (child.isMesh || child.children.length && child.children.every((mesh) => mesh.isMesh && !mesh.children.length)) &&
        child.position.lengthSq() === 0 &&
        child.quaternion.equals(new THREE.Quaternion()) && child.scale.equals(new THREE.Vector3(1, 1, 1))))
        throw new Error("Invalid damper segments: " + id);
      entry.damperRod = rod;
      entry.rodMeshes = new Set(rod.isMesh ? [rod] : rod.children);
    } else if (node.children.some((child) => child.userData.rail_damper_segment !== undefined)) {
      throw new Error("Incomplete damper segments: " + id);
    }
    return [id, entry];
  }));
  const materials = new Set([...parts.values()].flatMap(({meshes}) =>
    meshes.flatMap((mesh) => [mesh.material].flat())));
  for (const material of materials) {
    if (material?.isMeshStandardMaterial && surfaceRoughness.has(material.name))
      material.roughness = surfaceRoughness.get(material.name);
  }
  return {root, parts};
}

function disposeRejected(gltf) {
  const resources = new Set();
  gltf?.scene.traverse((object) => {
    if (object.geometry) resources.add(object.geometry);
    for (const material of [object.material].flat().filter(Boolean)) resources.add(material);
  });
  resources.forEach((resource) => resource.dispose());
}

export async function loadDetailedModel() {
  let gltf;
  try {
    const signal = AbortSignal.timeout(6000);
    const [asset, specification, {GLTFLoader}] = await Promise.all([
      fetch("./bogie.glb", {signal}),
      fetch("./model_contract.json", {signal}),
      import("./vendor/loaders/GLTFLoader.js"),
    ]);
    if (!asset.ok || !specification.ok) return null;
    const [bytes, contract] = await Promise.all([asset.arrayBuffer(), specification.json()]);
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(() => { throw new Error("Bogie GLB must be self-contained"); });
    gltf = await new GLTFLoader(manager).parseAsync(bytes, "");
    return inspect(gltf, contract);
  } catch {
    disposeRejected(gltf);
    return null;
  }
}
