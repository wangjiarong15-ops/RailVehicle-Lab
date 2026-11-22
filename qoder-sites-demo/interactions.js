import * as THREE from "three";

const ACCENT = 0x50d3c3, ALERT = 0xfca549;
const finishCache = new Map();

function tinted(original, role) {
  if (!original?.isMeshStandardMaterial) return original;
  let pair = finishCache.get(original);
  if (!pair) { pair = {}; finishCache.set(original, pair); }
  if (!pair[role]) {
    const surface = original.clone();
    if (role === "focus") surface.emissive = new THREE.Color(ACCENT).multiplyScalar(.55);
    if (role === "alert") surface.emissive = new THREE.Color(ALERT).multiplyScalar(.6);
    if (role === "ghost") { surface.transparent = true; surface.opacity = .16; surface.depthWrite = false; }
    pair[role] = surface;
  }
  return pair[role];
}

export function createInspector(rig, camera, canvas, onSelect) {
  const ray = new THREE.Raycaster(), cursor = new THREE.Vector2();
  let down = null, selected = null, isolated = null, ghost = false;
  const alerts = new Set();

  function apply() {
    for (const mesh of rig.objects) {
      const {partId, group, original} = mesh.userData;
      mesh.visible = !isolated || partId === isolated;
      mesh.material = alerts.has(partId) ? tinted(original, "alert")
        : partId === selected ? tinted(original, "focus")
        : ghost && group === "构架" ? tinted(original, "ghost") : original;
    }
  }
  function raycastId(clientX, clientY) {
    camera.updateMatrixWorld();
    const rect = canvas.getBoundingClientRect();
    cursor.set(2 * (clientX - rect.left) / rect.width - 1,
      1 - 2 * (clientY - rect.top) / rect.height);
    ray.setFromCamera(cursor, camera);
    const hit = ray.intersectObjects(rig.objects, false)
      .find(({object}) => object.userData.group !== "轨道参照" && isShown(object));
    return hit?.object.userData.partId ?? null;
  }
  function pick(event) {
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 6) return;
    down = null;
    const id = raycastId(event.clientX, event.clientY);
    selected = id;
    apply();
    onSelect(id);
  }
  const isShown = (mesh) => {
    for (let node = mesh; node; node = node.parent) if (!node.visible) return false;
    return true;
  };
  canvas.addEventListener("pointerdown", (event) => {
    down = event.button === 0 || event.pointerType !== "mouse" ? [event.clientX, event.clientY] : null;
  });
  canvas.addEventListener("pointerup", pick);

  return {
    apply,
    get selected() { return selected; },
    select(partId) { selected = partId; apply(); },
    hover(clientX, clientY) { return raycastId(clientX, clientY); },
    isolate(partId) { isolated = partId; apply(); },
    showAll() { isolated = null; apply(); },
    setGhost(value) { ghost = value; apply(); },
    setAlerts(ids) { alerts.clear(); for (const id of ids) alerts.add(id); apply(); },
    boxOf(partId) {
      const box = new THREE.Box3();
      for (const mesh of rig.objects) if (mesh.userData.partId === partId && isShown(mesh)) box.expandByObject(mesh);
      return box;
    },
  };
}

export function createSensors(rig, scene, camera, data, layer, onSelect) {
  const group = new THREE.Group();
  group.visible = false;
  scene.add(group);
  const sphere = new THREE.SphereGeometry(.03, 16, 12);
  const inverse = new THREE.Color(0x102e31);
  const markers = data.sensors.map((sensor) => {
    const node = rig.partNodes.get(sensor.part);
    const body = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({color: ACCENT}));
    const arrows = sensor.channels.map((channel) => new THREE.ArrowHelper(
      new THREE.Vector3(...channel.dir).normalize(), new THREE.Vector3(), .34,
      channel.name.startsWith("横向") ? ACCENT : ALERT, .1, .06));
    group.add(body, ...arrows);
    const tag = document.createElement("button");
    tag.type = "button";
    tag.className = "sensor-tag";
    tag.textContent = `${sensor.id} · 示意`;
    tag.addEventListener("click", () => onSelect(sensor));
    layer.append(tag);
    return {sensor, node, body, arrows, tag};
  });
  const point = new THREE.Vector3();
  return {
    setVisible(visible) {
      group.visible = visible;
      layer.hidden = !visible;
    },
    update(width, height) {
      if (!group.visible) return;
      const placed = [];
      for (const marker of markers) {
        marker.node.getWorldPosition(point);
        marker.body.position.copy(point);
        marker.arrows.forEach((arrow) => arrow.position.copy(point));
        point.project(camera);
        const offscreen = Math.abs(point.x) > 1 || Math.abs(point.y) > 1 || point.z > 1;
        marker.tag.hidden = offscreen;
        if (offscreen) continue;
        placed.push({
          marker,
          x: (point.x + 1) * width / 2,
          y: (1 - point.y) * height / 2,
          w: marker.tag.offsetWidth || 70,
          h: marker.tag.offsetHeight || 24,
        });
      }
      // nudge overlapping labels apart so测点 tags stay readable
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const a = placed[i], b = placed[j];
          const overlapX = Math.abs(a.x - b.x) < (a.w + b.w) / 2;
          const overlapY = Math.abs(a.y - b.y) < (a.h + b.h) / 2 + 4;
          if (overlapX && overlapY) b.y = a.y + (a.h + b.h) / 2 + 8;
        }
      }
      for (const item of placed) {
        item.marker.tag.style.left = item.x + "px";
        item.marker.tag.style.top = item.y + "px";
      }
    },
  };
}
