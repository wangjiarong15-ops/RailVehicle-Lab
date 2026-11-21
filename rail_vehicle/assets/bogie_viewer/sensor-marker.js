import * as THREE from "three";

// Model-space coordinates refer to the assembled, stationary GLB, in metres.
// Follow the selected part through the same animation, camera and explosion as its mesh.
export function sensorMarker(assembly, scene, camera, section) {
  const svg = document.querySelector("#sensor-overlay");
  const group = svg.querySelector("g"), text = group.querySelector("text"), line = group.querySelector("line");
  const inverse = new Map();
  scene.updateMatrixWorld(true);
  for (const [id,node] of assembly.partNodes) inverse.set(id,node.matrixWorld.clone().invert());
  const point = new THREE.Vector3(), position = new THREE.Vector3(), tip = new THREE.Vector3(), matrix = new THREE.Matrix4();
  return (sensor, visible, width, height) => {
    const node = sensor && assembly.partNodes.get(sensor.part);
    let show = Boolean(node && sensor.position && visible);
    if (show) {
      matrix.multiplyMatrices(node.matrixWorld, inverse.get(sensor.part));
      point.fromArray(sensor.position);
      position.copy(point).applyMatrix4(matrix);
      show = section.contains(position);
      position.project(camera);
      show = show && Math.abs(position.z) <= 1;
    }
    svg.style.display = show ? "block" : "none";
    if (!show) return;
    const x = (position.x+1)*width/2, y = (1-position.y)*height/2;
    group.setAttribute("transform", `translate(${x} ${y})`);
    line.style.display = sensor.direction ? "" : "none";
    if (sensor.direction) {
      tip.fromArray(sensor.direction).multiplyScalar(.22).add(point)
        .applyMatrix4(matrix).project(camera);
      line.setAttribute("x2", (tip.x+1)*width/2-x);
      line.setAttribute("y2", (1-tip.y)*height/2-y);
    }
    const label = sensor.serial + (sensor.illustration ? " · 示意" : " · 登记安装点");
    if (text.textContent !== label) text.textContent = label;
  };
}
