import * as THREE from "three";

const anchors = {
  "构架": ["frame", [-.55, -1.16, 1.17]], "轮对": ["wheel", [1.53, -.99, .37]],
  "一系悬挂": ["wheel", [1.30, -1.20, .74]], "二系悬挂": ["frame", [0, -1.16, 1.49]],
  "轴箱": ["wheel", [-1.30, -1.38, .52]], "电机与传动": ["frame", [-.87, -.15, .70]],
  "制动装置": ["wheel", [1.30, -.64, .70]],
};

export function inspection(canvas, data, assembly, rig, onSelect, reset, parts, section, measure) {
  const ray = new THREE.Raycaster(), cursor = new THREE.Vector2();
  ray.layers.enable(1); // Source meshes remain pickable during batched playback.
  const focus = document.querySelector("#focus");
  const resetButton = document.querySelector("#reset");
  const inspectionText = document.querySelector("#inspection");
  const labelLayer = document.querySelector("#labels");
  const axleLayer = document.querySelector("#axle-labels");
  const descriptions = data.parts;
  const tags = Object.keys(descriptions).map((part) => {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = part;
    labelLayer.append(tag);
    return [part, tag];
  });
  let down, selection, pickedMesh;
  const matches = (mesh, part) => mesh.userData.id === part || mesh.userData.part === part;
  const active = (mesh) => mesh.visible && mesh.parent.visible;
  function start(event) {
    down = event.button === 0 ? [event.clientX, event.clientY] : null;
  }
  function pick(event) {
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
    down = null;
    const rect = canvas.getBoundingClientRect();
    cursor.set(2 * (event.clientX - rect.left) / rect.width - 1,
      1 - 2 * (event.clientY - rect.top) / rect.height);
    ray.setFromCamera(cursor, rig.camera);
    const measuring = measure.active();
    const hit = ray.intersectObjects(assembly.objects.filter((mesh) =>
      active(mesh) && (measuring || mesh.userData.part !== "轨道参照")), false)
      .find(({point}) => section.contains(point));
    if (measuring) {
      if (hit) measure.hit(hit.point);
      return;
    }
    pickedMesh = hit?.object;
    onSelect(pickedMesh?.userData.id ?? pickedMesh?.userData.part ?? null);
  }
  function zoom() {
    const box = new THREE.Box3();
    if (pickedMesh && !pickedMesh.userData.id && matches(pickedMesh, selection) && active(pickedMesh))
      box.expandByObject(pickedMesh);
    else for (const mesh of assembly.objects) {
      if (active(mesh) && matches(mesh, selection)) box.expandByObject(mesh);
    }
    section.clipBounds(box);
    if (!box.isEmpty()) rig.focus(box);
  }
  canvas.addEventListener("pointerdown", start);
  canvas.addEventListener("pointerup", pick);
  const doubleClick = () => { if (!measure.active()) zoom(); };
  canvas.addEventListener("dblclick", doubleClick);
  focus.addEventListener("click", zoom);
  resetButton.addEventListener("click", reset);
  resetButton.disabled = false;
  const point = new THREE.Vector3();
  return {
    zoom,
    select(part, available) {
      if (pickedMesh && !matches(pickedMesh, part)) pickedMesh = null;
      selection = part;
      focus.disabled = !part || !available;
      const spec = parts?.get(part)?.spec;
      const group = spec?.group ?? part;
      const title = spec?.label ? spec.label + " (" + part + ")" : part;
      const origin = spec?.origin?.map(value => value.toFixed(2)).join(", ");
      inspectionText.textContent = part ? [title, descriptions[group], origin ? "装配原点 XYZ (" + origin + ") m" : "",
        spec?.spin ? "绕横向车轴转动" : "", available ? "" : "当前不可见"]
        .filter(Boolean).join(" · ") : "点击零件查看名称、作用和安装位置";
    },
    labels(args, width, height, isolated) {
      axleLayer.hidden = !args.simulation?.channels.front_travel_m || Boolean(isolated || section.enabled || args.spread);
      if (!axleLayer.hidden) {
        const view = rig.camera.matrixWorld.elements;
        const side = view[9] < 0 ? -1 : 1;
        const nearer = view[8] > 0 ? 1 : 0;
        const endOn = Math.abs(view[8]) > .995;
        for (const [index, x] of data.axleX.entries()) {
          const tag = axleLayer.children[index];
          point.set(x, side*data.wheelY, data.axleZ*.45).applyMatrix4(assembly.wheel.matrixWorld).project(rig.camera);
          tag.hidden = (endOn && index !== nearer) || Math.abs(point.x) > .95 || Math.abs(point.y) > .95;
          tag.style.left = (point.x+1)*width/2 + "px";
          tag.style.top = (1-point.y)*height/2 + "px";
        }
      }
      labelLayer.hidden = !args.labels;
      if (!args.labels) return;
      for (const [part, tag] of tags) {
        const [parent, coord] = anchors[part];
        const candidate = assembly.objects.find((mesh) => active(mesh) && mesh.userData.part === part);
        if (args.spread && candidate) new THREE.Box3().setFromObject(candidate).getCenter(point);
        else point.set(...coord).applyMatrix4(assembly[parent].matrixWorld);
        const retained = section.contains(point);
        point.project(rig.camera);
        tag.hidden = !candidate || !retained || Math.abs(point.x) > .95 || Math.abs(point.y) > .9;
        tag.style.left = (point.x + 1) * width / 2 + "px";
        tag.style.top = (1 - point.y) * height / 2 + "px";
      }
    },
    dispose() {
      canvas.removeEventListener("pointerdown", start);
      canvas.removeEventListener("pointerup", pick);
      canvas.removeEventListener("dblclick", doubleClick);
      focus.removeEventListener("click", zoom);
      resetButton.removeEventListener("click", reset);
    },
  };
}
