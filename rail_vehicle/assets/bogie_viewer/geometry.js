import * as THREE from "three";

// Smooth only adjacent faces within the crease angle; keep the bevels and wheel rims crisp.
function rigidGeometry(vertices, faces, tones) {
  const points = vertices.map((point) => new THREE.Vector3(...point));
  const adjacent = points.map(() => []);
  const vectors = faces.map(([a, b, c], index) => {
    for (const point of [a, b, c]) adjacent[point].push(index);
    return new THREE.Vector3().subVectors(points[b], points[a])
      .cross(new THREE.Vector3().subVectors(points[c], points[a])).normalize();
  });
  const geometry = new THREE.BufferGeometry();
  const position = [], normals = [], normal = new THREE.Vector3();
  for (let tone = 0; tone < 3; tone++) {
    const start = position.length / 3;
    faces.forEach((face, index) => {
      if (tones[index] !== tone) return;
      for (const vertex of face) {
        const point = points[vertex];
        position.push(point.x, point.y, point.z);
        normal.set(0, 0, 0);
        for (const neighbor of adjacent[vertex]) {
          if (vectors[index].dot(vectors[neighbor]) >= .80) normal.add(vectors[neighbor]);
        }
        normal.normalize();
        normals.push(normal.x, normal.y, normal.z);
      }
    });
    if (position.length / 3 > start) geometry.addGroup(start, position.length / 3 - start, tone);
  }
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(position, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.computeBoundingBox();
  return geometry;
}

class Coil extends THREE.Curve {
  getPoint(t, target = new THREE.Vector3()) {
    const angle = t * 8 * Math.PI;
    return target.set(.073 * Math.cos(angle), t * .201, .073 * Math.sin(angle));
  }
}

const Y = new THREE.Vector3(0, 1, 0);
const lower = new THREE.Vector3(), upper = new THREE.Vector3(), direction = new THREE.Vector3();

function orient(group, from, to) {
  direction.subVectors(to, from);
  const length = direction.length();
  group.position.copy(from);
  group.quaternion.setFromUnitVectors(Y, direction.divideScalar(length));
  return length;
}

export function createAssembly(scene, data, material) {
  const frame = new THREE.Group(), wheel = new THREE.Group(), fixed = new THREE.Group();
  scene.add(frame, wheel, fixed);
  const parents = {frame, wheel, fixed};
  const objects = [], spins = [], links = [];
  function track(mesh, part, tone = null) {
    mesh.userData = {part, tone};
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    objects.push(mesh);
    return mesh;
  }
  const markerGeometry = new THREE.BoxGeometry(.060, .006, .010);
  for (const [[part, group, parent, offset], vertices, faces, tones] of data.mesh) {
    const holder = new THREE.Group();
    parents[parent].add(holder);
    holder.userData.offset = new THREE.Vector3(...offset);
    const mesh = track(new THREE.Mesh(rigidGeometry(vertices, faces, tones), material[part]), part);
    if (group && (part === "轮对" || group[2] === "disc")) {
      const axle = new THREE.Group(), [x, side] = group;
      axle.position.set(x, 0, data.axleZ);
      mesh.position.set(-x, 0, -data.axleZ);
      holder.add(axle);
      axle.add(mesh);
      spins.push(axle);
      if (side && part === "轮对") {
        const marker = track(new THREE.Mesh(markerGeometry, material[part][1]), part, 1);
        marker.position.set(.27, side * (data.wheelY + .100), .01);
        axle.add(marker);
        marker.castShadow = false;
      }
    } else holder.add(mesh);
  }
  const cylinder = new THREE.CylinderGeometry(1, 1, 1, 16);
  const coil = new THREE.TubeGeometry(new Coil(), 80, .0095, 8);
  for (const [part, kind, a, b] of data.links) {
    const node = new THREE.Group();
    scene.add(node);
    const add = (geo, tone) => {
      const mesh = track(new THREE.Mesh(geo, material[part][tone]), part, tone);
      node.add(mesh);
      return mesh;
    };
    const segments = kind === "coil" ? [[add(coil, 0), 0, 1, null]]
      : kind === "damper" ? [[add(cylinder, 2), 0, .59, .032], [add(cylinder, 1), .57, 1, .019]]
        : [[add(cylinder, kind === "coupling" ? 1 : 2), 0, 1, kind === "coupling" ? .063 : .028]];
    links.push({node, a: new THREE.Vector3(...a), b: new THREE.Vector3(...b), segments, part});
  }

  let visible = new Set(), highlight = new Set(), selected, spread = 0;
  return {
    objects, frame, wheel,
    appearance(args, selection) {
      visible = new Set(args.visible);
      if (args.rails) visible.add("轨道参照");
      highlight = new Set(args.highlight);
      selected = selection;
      spread = args.spread;
      for (const parent of Object.values(parents)) {
        for (const holder of parent.children) holder.position.copy(holder.userData.offset).multiplyScalar(spread);
      }
      for (const mesh of objects) {
        const {part, tone} = mesh.userData;
        mesh.visible = visible.has(part);
        const finishes = material[highlight.has(part) ? "alert" : part === selected ? "focus" : part];
        mesh.material = tone === null ? finishes : finishes[tone];
      }
      for (const {node, part} of links) node.visible = !spread && visible.has(part);
    },
    pose(phase, lateral, vertical, yaw) {
      frame.position.set(0, lateral, vertical);
      frame.rotation.z = yaw;
      // Wheelset stays at rail height. Brake discs rotate with axles; calipers and axleboxes do not.
      wheel.position.y = lateral * data.wheelPoseGain;
      wheel.rotation.z = yaw * data.wheelPoseGain;
      for (const axle of spins) axle.rotation.y = phase;
      frame.updateMatrixWorld(true);
      wheel.updateMatrixWorld(true);
      for (const {node, a, b, segments} of links) {
        if (!node.visible) continue;
        lower.copy(a).applyMatrix4(wheel.matrixWorld);
        upper.copy(b).applyMatrix4(frame.matrixWorld);
        const length = orient(node, lower, upper);
        for (const [mesh, start, end, radius] of segments) {
          if (radius === null) mesh.scale.y = length / .201;
          else {
            mesh.position.y = length * (start + end) / 2;
            mesh.scale.set(radius, length * (end - start), radius);
          }
        }
      }
    },
  };
}
