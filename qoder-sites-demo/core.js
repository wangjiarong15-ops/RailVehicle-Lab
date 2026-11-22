import * as THREE from "three";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";

export async function loadViewerData() {
  const signal = AbortSignal.timeout(10000);
  const response = await fetch("./assets/viewer-data.json", {signal});
  if (!response.ok) throw new Error("viewer-data.json 请求失败");
  return response.json();
}

export async function loadBogie(onStage = () => {}) {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(() => { throw new Error("模型必须为自包含 GLB"); });
  const signal = AbortSignal.timeout(20000);
  onStage("正在下载三维模型…");
  const asset = await fetch("./bogie.glb", {signal});
  if (!asset.ok) throw new Error(`bogie.glb 请求失败：${asset.status}`);
  onStage("正在解析装配结构…");
  const {scene} = await new GLTFLoader(manager).parseAsync(await asset.arrayBuffer(), "");
  const root = new THREE.Group();
  root.rotation.x = Math.PI / 2; // glTF 导出的 Y-up 转回工程 Z-up 坐标系
  root.add(scene);
  return root;
}

const Y_AXIS = new THREE.Vector3(0, 1, 0);

/* Rebuild the layer/holder/spin/link transform rig from the part contract so that
   pose() can drive frame, wheelset and suspension links like the workbench viewer. */
export function buildRig(root, data) {
  const assembly = root.getObjectByName("rail_bogie");
  if (!assembly) throw new Error("模型缺少 rail_bogie 装配节点");
  const layers = {};
  for (const id of data.contract.layers) {
    const layer = assembly.children.find((child) => child.name === "rail_" + id);
    if (!layer) throw new Error("模型缺少装配层：" + id);
    layers[id] = layer;
  }
  const parents = {frame: new THREE.Group(), wheel: new THREE.Group(), fixed: new THREE.Group()};
  root.add(parents.frame, parents.wheel, parents.fixed);

  const objects = [], spins = [], links = [], partNodes = new Map(), parts = new Map();
  const lower = new THREE.Vector3(), upper = new THREE.Vector3(), direction = new THREE.Vector3();

  function orient(group, from, to) {
    direction.subVectors(to, from);
    const length = direction.length();
    group.position.copy(from);
    group.quaternion.setFromUnitVectors(Y_AXIS, direction.divideScalar(length));
    return length;
  }
  function spinAt(holder, pivot) {
    const axle = new THREE.Group();
    axle.position.set(...pivot);
    holder.add(axle);
    spins.push(axle);
    return axle;
  }

  for (const [id, spec] of Object.entries(data.contract.parts)) {
    const node = layers[spec.parent]?.children.find((child) => child.name === id);
    if (!node) throw new Error("模型缺少零件：" + id);
    const meshes = [];
    node.traverse((item) => {
      if (item.isMesh) {
        item.userData.partId = id;
        item.userData.group = spec.group;
        item.userData.original = item.material;
        meshes.push(item);
      }
    });
    if (!meshes.length) throw new Error("零件缺少网格：" + id);
    objects.push(...meshes);
    const entry = {id, spec, node, meshes};
    if (id.startsWith("damper_")) {
      entry.damperRod = node.children.find((child) => child.userData?.rail_damper_segment === "rod")
        ?? node.children.find((child) => child.name.includes("rod"));
    }
    parts.set(id, entry);
    partNodes.set(id, node);
    if (spec.parent === "links") continue;
    const holder = new THREE.Group();
    parents[spec.parent].add(holder);
    holder.position.set(...spec.offset);
    (spec.spin ? spinAt(holder, spec.spin) : holder).attach(node);
  }

  const importedLinks = new Map([...parts.values()]
    .filter((entry) => entry.spec.parent === "links")
    .map((entry) => [entry.spec.link, entry]));
  for (const [index, [part, kind, a, b]] of data.links.entries()) {
    const entry = importedLinks.get(index);
    if (!entry) continue;
    const node = new THREE.Group();
    root.add(node);
    const start = new THREE.Vector3(...a), end = new THREE.Vector3(...b);
    const length = orient(node, start, end);
    node.attach(entry.node);
    const slide = entry.damperRod && ((current) => {
      // glTF 将 Blender 的 +Y 连杆轴编码为 -Z。
      entry.damperRod.position.z = length - current;
    });
    links.push({node, a: start, b: end, part: entry.spec.group, id: entry.id, length, slide});
  }

  const seatHeight = data.links.find(([, kind]) => kind === "coil")[3][2];
  const gain = data.constants.wheelPoseGain;

  function endpoints(link, from, to) {
    from.copy(link.a).applyMatrix4(parents.wheel.matrixWorld);
    to.copy(link.b).applyMatrix4(parents.frame.matrixWorld);
  }

  return {
    parents, objects, parts, partNodes, links,
    pose(phase, lateral, vertical, yaw) {
      parents.frame.position.set(0, lateral, vertical);
      parents.frame.rotation.z = yaw;
      parents.wheel.position.y = lateral * gain;
      parents.wheel.rotation.z = yaw * gain;
      for (const axle of spins) axle.rotation.y = phase;
      parents.frame.updateWorldMatrix(true, false);
      parents.wheel.updateWorldMatrix(true, false);
      for (const link of links) {
        if (!link.node.visible) continue;
        endpoints(link, lower, upper);
        const length = orient(link.node, lower, upper);
        if (link.slide) link.slide(length);
      }
    },
  };
}
