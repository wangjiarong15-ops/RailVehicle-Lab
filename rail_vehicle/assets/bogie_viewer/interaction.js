import * as THREE from "three";

const anchors = {
  "构架": ["frame", [-.55, -1.16, 1.17]], "轮对": ["wheel", [1.53, -.99, .37]],
  "一系悬挂": ["wheel", [1.30, -1.20, .74]], "二系悬挂": ["frame", [0, -1.16, 1.49]],
  "轴箱": ["wheel", [-1.30, -1.38, .52]], "电机与传动": ["frame", [-.87, -.15, .70]],
  "制动装置": ["wheel", [1.30, -.64, .70]],
};

export function inspection(canvas, descriptions, assembly, rig, onSelect, reset) {
  const ray = new THREE.Raycaster(), cursor = new THREE.Vector2();
  const focus = document.querySelector("#focus");
  const resetButton = document.querySelector("#reset");
  const inspectionText = document.querySelector("#inspection");
  const labelLayer = document.querySelector("#labels");
  const tags = Object.keys(descriptions).map((part) => {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = part;
    labelLayer.append(tag);
    return [part, tag];
  });
  let down, selection, pickedMesh;
  const matches = (mesh, part) => mesh.userData.id === part || mesh.userData.part === part;
  const active = (mesh) => mesh.visible && mesh.parent.visible && mesh.userData.part !== "轨道参照";
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
    pickedMesh = ray.intersectObjects(assembly.objects.filter(active), false)[0]?.object;
    onSelect(pickedMesh?.userData.id ?? pickedMesh?.userData.part ?? null);
  }
  function zoom() {
    const box = new THREE.Box3();
    if (pickedMesh && !pickedMesh.userData.id && matches(pickedMesh, selection) && active(pickedMesh))
      box.expandByObject(pickedMesh);
    else for (const mesh of assembly.objects) {
      if (active(mesh) && matches(mesh, selection)) box.expandByObject(mesh);
    }
    if (!box.isEmpty()) rig.focus(box);
  }
  canvas.addEventListener("pointerdown", start);
  canvas.addEventListener("pointerup", pick);
  canvas.addEventListener("dblclick", zoom);
  focus.addEventListener("click", zoom);
  resetButton.addEventListener("click", reset);
  resetButton.disabled = false;
  const point = new THREE.Vector3();
  return {
    select(part) {
      if (pickedMesh && !matches(pickedMesh, part)) pickedMesh = null;
      selection = part;
      focus.disabled = !part;
      const group = pickedMesh?.userData.part ?? part;
      inspectionText.textContent = part ? (group === part ? part : group + " · " + part) + " · " + descriptions[group]
        : "点击零件查看名称、作用和安装位置";
    },
    labels(args, width, height) {
      labelLayer.hidden = !args.labels;
      if (!args.labels) return;
      for (const [part, tag] of tags) {
        const [parent, coord] = anchors[part];
        const candidate = assembly.objects.find((mesh) => active(mesh) && mesh.userData.part === part);
        if (args.spread && candidate) new THREE.Box3().setFromObject(candidate).getCenter(point);
        else point.set(...coord).applyMatrix4(assembly[parent].matrixWorld);
        point.project(rig.camera);
        tag.hidden = !candidate || Math.abs(point.x) > .95 || Math.abs(point.y) > .9;
        tag.style.left = (point.x + 1) * width / 2 + "px";
        tag.style.top = (1 - point.y) * height / 2 + "px";
      }
    },
    dispose() {
      canvas.removeEventListener("pointerdown", start);
      canvas.removeEventListener("pointerup", pick);
      canvas.removeEventListener("dblclick", zoom);
      focus.removeEventListener("click", zoom);
      resetButton.removeEventListener("click", reset);
    },
  };
}
