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
const Z = new THREE.Vector3(0, 0, 1);
const AIR_SPRING_HEIGHT_M = .324;
const lower = new THREE.Vector3(), upper = new THREE.Vector3(), direction = new THREE.Vector3();

function orient(group, from, to) {
  direction.subVectors(to, from);
  const length = direction.length();
  group.position.copy(from);
  group.quaternion.setFromUnitVectors(Y, direction.divideScalar(length));
  return length;
}

export function createAssembly(scene, data, material, detailed) {
  const frame = new THREE.Group(), wheel = new THREE.Group(), fixed = new THREE.Group();
  scene.add(frame, wheel, fixed);
  const parents = {frame, wheel, fixed};
  const seatHeight = data.links.find(([,kind]) => kind === "coil")[3][2];
  const objects = [], spins = [], links = [], partNodes = new Map();
  const secondaryNodes = new Map();
  function track(mesh, part, tone = null, id = null) {
    Object.assign(mesh.userData, {part, tone, id, original: mesh.material});
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    objects.push(mesh);
    return mesh;
  }
  function holderAt(parent, offset) {
    const holder = new THREE.Group();
    parent.add(holder);
    holder.userData.offset = new THREE.Vector3(...offset);
    return holder;
  }
  function spinAt(holder, pivot) {
    const axle = new THREE.Group();
    axle.position.set(...pivot);
    holder.add(axle);
    spins.push(axle);
    return axle;
  }
  if (detailed) for (const [id, {node, spec, meshes}] of detailed.parts) {
    for (const mesh of meshes) track(mesh, spec.group, 0, id);
    if (id.startsWith("wheel_")) {
      // The paint witness rotates with the wheel but should not invalidate its static shadow.
      for (const mesh of meshes) if (mesh.material.name === "marker") mesh.castShadow = false;
    }
    if (spec.parent === "links") continue;
    const holder = holderAt(parents[spec.parent], spec.offset);
    if (spec.parent === "wheel") holder.userData.wheelEnd = spec.origin[0] > 0 ? 0 : 1;
    let parent = spec.spin ? spinAt(holder, spec.spin) : holder;
    if (id.startsWith("air_spring_")) {
      parent = new THREE.Group();
      parent.position.fromArray(spec.origin);
      holder.add(parent);
      secondaryNodes.set(id, parent);
    }
    parent.attach(node);
    partNodes.set(id, node);
  }
  const markerGeometry = detailed ? null : new THREE.BoxGeometry(.060, .006, .010);
  const mounts = [...secondaryNodes.values()];
  const deck = mounts.length === 2 ? new THREE.Mesh(
    new THREE.BoxGeometry(1.08, Math.abs(mounts[0].position.y-mounts[1].position.y)+.49, .10),
    new THREE.MeshStandardMaterial({color:0x637f95, metalness:.49, roughness:.53})
  ) : null;
  const deckZ = mounts.length ? mounts[0].position.z + AIR_SPRING_HEIGHT_M : 0;
  if (deck) { deck.visible = false; deck.castShadow = true; frame.add(deck); }
  if (!detailed) for (const [[part, group, parent, offset], vertices, faces, tones] of data.mesh) {
    const holder = holderAt(parents[parent], offset);
    const mesh = track(new THREE.Mesh(rigidGeometry(vertices, faces, tones), material[part]), part);
    if (group && (part === "轮对" || group[2] === "disc")) {
      const [x, side] = group;
      const axle = spinAt(holder, [x, 0, data.axleZ]);
      mesh.position.set(-x, 0, -data.axleZ);
      axle.add(mesh);
      if (side && part === "轮对") {
        const marker = track(new THREE.Mesh(markerGeometry, material[part][1]), part, 1);
        marker.position.set(.27, side * (data.wheelY + .100), .01);
        axle.add(marker);
        marker.castShadow = false;
      }
    } else holder.add(mesh);
  }
  const cylinder = detailed ? null : new THREE.CylinderGeometry(1, 1, 1, 16);
  const coil = detailed ? null : new THREE.TubeGeometry(new Coil(), 80, .0095, 8);
  const importedParts = detailed ? [...detailed.parts] : [];
  const importedLinks = new Map(importedParts.filter(([, entry]) => entry.spec.parent === "links")
    .map(([id, entry]) => [entry.spec.link, {id, ...entry}]));
  for (const [index, [part, kind, a, b]] of data.links.entries()) {
    const node = new THREE.Group();
    scene.add(node);
    const start = new THREE.Vector3(...a), end = new THREE.Vector3(...b);
    const imported = importedLinks.get(index);
    if (imported) {
      const length = orient(node, start, end);
      node.attach(imported.node);
      partNodes.set(imported.id, imported.node);
      const slide = imported.damperRod && ((current) => {
        // glTF encodes Blender's local +Y link axis as -Z.
        imported.damperRod.position.z = length - current;
      });
      links.push({node, a: start, b: end, part, id: imported.id, length, slide,
        offset: new THREE.Vector3(...imported.spec.offset)});
      continue;
    }
    const add = (geo, tone) => {
      const mesh = track(new THREE.Mesh(geo, material[part][tone]), part, tone);
      node.add(mesh);
      return mesh;
    };
    if (kind === "damper") {
      const length = start.distanceTo(end);
      const [bodyLength, rodLength] = data.damperRatios.map((ratio) => ratio * length);
      const body = add(cylinder, 2), rod = add(cylinder, 1);
      body.position.y = bodyLength / 2;
      body.scale.set(.032, bodyLength, .032);
      rod.scale.set(.019, rodLength, .019);
      links.push({node, a: start, b: end, part, length, slide(current) {
        rod.position.y = current - rodLength / 2;
      }});
      continue;
    }
    const segments = kind === "coil" ? [[add(coil, 0), 0, 1, null]]
      : [[add(cylinder, kind === "coupling" ? 1 : 2), 0, 1, kind === "coupling" ? .063 : .028]];
    links.push({node, a: start, b: end, segments, part});
  }

  let visible = new Set(), highlight = new Set(), selected, spread = 0, wheelTravel = [0, 0], wheelY = [0, 0], deckAllowed = false;
  const shadowCasters = objects.filter(mesh => mesh.castShadow);
  // Keep the imported steel, rubber and paint finishes readable during inspection.
  const tinted = new Map();
  function inspectionFinish(mesh, role) {
    const {original, tone} = mesh.userData;
    if (!detailed || !original.isMeshStandardMaterial)
      return tone === null ? material[role] : material[role][tone];
    let pair = tinted.get(original);
    if (!pair) { pair = {}; tinted.set(original, pair); }
    if (!pair[role]) {
      const surface = original.clone();
      const lightness = Math.max(original.color.r, original.color.g, original.color.b);
      surface.color.copy(material[role][0].color).multiplyScalar(.65 + .55 * lightness);
      pair[role] = surface;
    }
    return pair[role];
  }
  const suspensionLinks = new Map(links.filter(link => link.id && link.part === "一系悬挂")
    .map(link => [link.id, link]));
  function endpoints(link, from, to) {
    from.copy(link.a);
    to.copy(link.b);
    if (spread && link.offset) {
      from.addScaledVector(link.offset, spread);
      to.addScaledVector(link.offset, spread);
    } else {
      from.applyMatrix4(wheel.matrixWorld);
      const axle = link.a.x > 0 ? 0 : 1;
      from.z += wheelTravel[axle];
      from.y += wheelY[axle];
      to.applyMatrix4(frame.matrixWorld);
    }
  }
  function setSpread(value) {
    spread = value;
    for (const parent of Object.values(parents)) {
      for (const holder of parent.children) {
        if (holder.userData.offset) holder.position.copy(holder.userData.offset).multiplyScalar(spread);
      }
    }
  }
  return {
    objects, frame, wheel, partNodes, secondaryNodes, layers: parents, setSpread,
    dispose() {
      for (const mesh of objects) mesh.material = mesh.userData.original;
      for (const pair of tinted.values()) for (const surface of Object.values(pair)) surface.dispose();
      tinted.clear();
      if (deck) { deck.removeFromParent(); deck.geometry.dispose(); deck.material.dispose(); }
    },
    // World-space mounting centres in metres; rest distance uses the assembled contract.
    suspensionMounts(id, from, to) {
      const link = suspensionLinks.get(id);
      if (!link?.node.visible) return null;
      endpoints(link, from, to);
      return link.length;
    },
    appearance(args, selection) {
      setSpread(args.drawSpread ?? args.spread);
      // Transparent frame and wire inspection should not cast solid silhouettes over the mechanism.
      for (const mesh of shadowCasters)
        mesh.castShadow = args.surfaceMode !== "wire" && !(args.surfaceMode === "ghost" && mesh.userData.part === "构架");
      visible = new Set(args.visible);
      deckAllowed = visible.has("二系悬挂") && !args.isolated &&
        ![...secondaryNodes.keys()].some(id=>args.hidden?.has(id));
      if (args.rails) visible.add("轨道参照");
      highlight = new Set(args.highlight);
      selected = selection;
      for (const mesh of objects) {
        const {part, id, original} = mesh.userData;
        mesh.visible = visible.has(part) && !args.hidden?.has(id) && (!args.isolated || args.isolated === id);
        const finish = highlight.has(part) ? "alert" : part === selected || id && id === selected ? "focus" : null;
        mesh.material = finish ? inspectionFinish(mesh, finish) : original;
      }
      for (const {node, part, id} of links) {
        node.visible = (id || !args.spread) && visible.has(part) && !args.hidden?.has(id) &&
          (!args.isolated || args.isolated === id);
      }
    },
    pose(phase, lateral, vertical, yaw, pitch = 0, wheelOffsets, body) {
      // Tilt about the midpoint of the upper seats, in the two-wheel support plane.
      const coupled = Boolean(body?.wheelY);
      frame.position.set(seatHeight*Math.sin(pitch), lateral, vertical+seatHeight*(1-Math.cos(pitch)));
      if (coupled) frame.position.set(0, lateral, vertical);
      frame.rotation.x = body?.frameRoll ?? 0;
      frame.rotation.y = -pitch;
      frame.rotation.z = yaw;
      // Wheelset stays at rail height. Brake discs rotate with axles; calipers and axleboxes do not.
      wheel.position.y = coupled ? 0 : lateral * (body?.rigidSupport ? 1 : data.wheelPoseGain);
      wheel.rotation.z = coupled ? 0 : yaw * data.wheelPoseGain;
      wheelTravel = (detailed || coupled) && wheelOffsets ? wheelOffsets : [0, 0];
      wheelY = body?.wheelY ?? [0, 0];
      for (const holder of wheel.children) {
        const end = holder.userData.wheelEnd;
        if (end !== undefined) {
          holder.position.y = holder.userData.offset.y * spread + wheelY[end];
          holder.position.z = holder.userData.offset.z * spread + wheelTravel[end];
        }
      }
      for (const axle of spins) axle.rotation.y = phase;
      if (deck) {
        const active = body && !spread;
        const {y = 0, heave = 0, roll = 0} = active ? body : {};
        const sine = Math.sin(roll), cosine = Math.cos(roll);
        deck.position.set(0, y-.05*sine, deckZ+heave+.05*cosine);
        deck.rotation.x = roll;
        deck.visible = Boolean(active && deckAllowed);
        for (const pivot of mounts) {
          const side = pivot.position.y;
          direction.set(0, y+side*(cosine-1), AIR_SPRING_HEIGHT_M+heave+side*sine);
          const length = direction.length();
          pivot.quaternion.setFromUnitVectors(Z, direction.divideScalar(length));
          pivot.scale.z = length/AIR_SPRING_HEIGHT_M;
        }
      }
      // Only the two support transforms are needed here. The renderer updates descendants.
      frame.updateWorldMatrix(true, false);
      wheel.updateWorldMatrix(true, false);
      for (const link of links) {
        const {node, segments, length: restLength, slide} = link;
        if (!node.visible) continue;
        endpoints(link, lower, upper);
        const length = orient(node, lower, upper);
        if (slide || restLength) {
          if (slide) slide(length);
          else node.scale.y = length / restLength;
          continue;
        }
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
