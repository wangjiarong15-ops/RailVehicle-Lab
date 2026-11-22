import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {RoomEnvironment} from "three/addons/environments/RoomEnvironment.js";
import {loadBogie, loadViewerData, buildRig} from "./core.js";
import {createInspector, createSensors} from "./interactions.js";

const canvas = document.querySelector("#scene");
const stage = document.querySelector("#stage");
const panelBody = document.querySelector("#panel-body");
const loading = document.querySelector("#loading");
const errorBox = document.querySelector("#error");
const hint = document.querySelector("#hint");
const motionControls = document.querySelector("#motion-controls");
const sensorLayer = document.querySelector("#sensor-layer");

const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1521);
scene.fog = new THREE.Fog(0x0b1521, 16, 40);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
camera.up.set(0, 0, 1); // 与装配一致的工程 Z-up 视角
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = .08;
controls.minDistance = 2;
controls.maxDistance = 30;

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.add(new THREE.HemisphereLight(0x9fb8d8, 0x1c2a3a, 0.7));
const key = new THREE.DirectionalLight(0xe8f2ff, 1.6); key.position.set(6, 7, 9); scene.add(key);
const rim = new THREE.DirectionalLight(0x50d3c3, 0.5); rim.position.set(-7, -5, 4); scene.add(rim);
const grid = new THREE.GridHelper(24, 48, 0x34485e, 0x1d3048);
grid.rotation.x = Math.PI / 2;
grid.material.transparent = true; grid.material.opacity = .5;
scene.add(grid);

const viewAnim = {from: null, to: null, t: 1, radius: 5, center: new THREE.Vector3(0, 0, .8)};
function setView(name) {
  const r = viewAnim.radius, c = viewAnim.center;
  const spots = {
    home: new THREE.Vector3(r * 1.4, -r * 1.5, r * .85),
    side: new THREE.Vector3(0.05, -r * 2.6, r * .25),
    front: new THREE.Vector3(r * 2.6, 0.05, r * .25),
    top: new THREE.Vector3(0.05, 0.05, r * 2.8),
  };
  viewAnim.from = camera.position.clone();
  viewAnim.to = spots[name].clone();
  controls.target.copy(c);
  viewAnim.t = 0;
  document.querySelectorAll("#toolbar [data-view]").forEach((b) =>
    b.classList.toggle("primary", b.dataset.view === name));
}
function focusOn(box) {
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length();
  const distance = Math.min(Math.max(size * 2.6, 1.6), viewAnim.radius * 3);
  const direction = camera.position.clone().sub(controls.target).normalize();
  viewAnim.center = center;
  viewAnim.from = camera.position.clone();
  viewAnim.to = center.clone().add(direction.multiplyScalar(distance));
  viewAnim.t = 0;
  controls.target.copy(center);
}

let rig = null, inspector = null, sensors = null, data = null;
let mode = "parts", playing = false, scenario = "normal";

function describePart(id) {
  const entry = rig.parts.get(id);
  const spec = entry?.spec;
  const description = data.descriptions[spec.group] ?? "";
  const origin = spec.origin.map((v) => v.toFixed(2)).join(", ");
  return `<h2>${spec.label}</h2>
    <p class="group">${spec.group}${spec.spin ? " · 绕车轴旋转件" : ""}</p>
    <p>${description}</p>
    <p class="coords">装配原点 XYZ（${origin}）m</p>
    <div class="panel-actions">
      <button type="button" data-act="focus">聚焦观察</button>
      <button type="button" data-act="isolate">仅看此件</button>
      <button type="button" data-act="showall">显示全部</button>
      <button type="button" data-act="ghost" aria-pressed="false">构架透明</button>
    </div>`;
}
function describeSensor(sensor) {
  return `<h2>${sensor.label}</h2><p class="group">传感器测点 · 示意</p>
    <p>${sensor.note}</p>
    <ul>${sensor.channels.map((c) => `<li>${c.name}方向</li>`).join("")}</ul>`;
}
function defaultPanel() {
  return `<h2>双轴铁路转向架</h2>
    <p>模型与 Three.js 均为本地资源，不依赖外部 CDN。零件说明与装配原点取自工程模型契约。</p>
    <dl>
      <div><dt>零部件</dt><dd>点击零件查看名称、作用与安装位置，可聚焦或仅看该件。</dd></div>
      <div><dt>传感器</dt><dd>展示横/垂向测点与正方向，均为教学示意位置。</dd></div>
      <div><dt>教学动画</dt><dd>预设运动示意，非真实故障诊断或实车测量结果。</dd></div>
    </dl>
    <p class="note">仅用于三维展示与交互演示，参数不代表实车测量数据。</p>`;
}
function showPanel(html) { panelBody.innerHTML = html; }

function setMode(next) {
  mode = next;
  document.querySelectorAll("#modes [data-mode]").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.mode === next)));
  sensors.setVisible(next === "sensors");
  motionControls.hidden = next !== "motion";
  hint.textContent = next === "parts" ? "左键拖拽旋转 · 滚轮缩放 · 右键平移 · 点击零件查看详情"
    : next === "sensors" ? "点击测点标签查看说明 · 箭头为测量正方向（示意）"
    : "选择场景后播放，观察构架与轮对的简化运动";
  if (next !== "parts") inspector.select(null);
  if (next !== "motion") { playing = false; updatePlay(); }
  if (next === "motion") showPanel(defaultPanel());
  else if (next === "sensors") showPanel(defaultPanel());
  else showPanel(defaultPanel());
}

function updatePlay() {
  const button = document.querySelector("#play");
  button.textContent = playing ? "暂停" : "播放";
  button.setAttribute("aria-pressed", String(playing));
}

function onSelectPart(id) {
  if (mode !== "parts") return;
  if (!id) { showPanel(defaultPanel()); return; }
  showPanel(describePart(id));
  if (window.innerWidth <= 720) openDrawer();
}

/* panel action delegation */
panelBody.addEventListener("click", (event) => {
  const act = event.target.dataset?.act;
  if (!act) return;
  const id = inspector.selected;
  if (act === "focus" && id) focusOn(inspector.boxOf(id));
  if (act === "isolate" && id) inspector.isolate(id);
  if (act === "showall") inspector.showAll();
  if (act === "ghost") {
    const on = event.target.getAttribute("aria-pressed") !== "true";
    event.target.setAttribute("aria-pressed", String(on));
    inspector.setGhost(on);
  }
});

/* motion envelope per scenario, matching the workbench illustration */
const envelopes = {normal: [0, 0, 0], lateral: [.075, 0, 0], vertical: [0, .065, 0], fault: [.075, .065, .049]};
const envelope = [0, 0, 0];
let phase = 0;

/* ---- boot ---- */
Promise.all([loadViewerData(), loadBogie()]).then(([viewerData, root]) => {
  data = viewerData;
  rig = buildRig(root, data);
  scene.add(root);
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  viewAnim.radius = Math.max(size.x, size.y, size.z) * .8;
  viewAnim.center = new THREE.Vector3(0, 0, size.z * .4);
  controls.target.copy(viewAnim.center);
  camera.position.copy(viewAnim.center).add(new THREE.Vector3(
    viewAnim.radius * 1.4, -viewAnim.radius * 1.5, viewAnim.radius * .85));
  controls.update();

  inspector = createInspector(rig, camera, canvas, onSelectPart);
  scene.updateMatrixWorld(true);
  sensors = createSensors(rig, scene, camera, data, sensorLayer, (sensor) => {
    showPanel(describeSensor(sensor));
    if (window.innerWidth <= 720) openDrawer();
  });
  showPanel(defaultPanel());
  loading.hidden = true;
}).catch((cause) => {
  console.error(cause);
  loading.hidden = true;
  errorBox.hidden = false;
  errorBox.textContent = `三维视图不可用：${cause?.message ?? "加载失败"}`;
});

/* ---- toolbar wiring ---- */
document.querySelectorAll("#toolbar [data-view]").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));
document.querySelector("#reset-view").addEventListener("click", () => setView("home"));
const autoRotate = document.querySelector("#auto-rotate");
autoRotate.addEventListener("click", () => {
  controls.autoRotate = !controls.autoRotate;
  controls.autoRotateSpeed = 1.2;
  autoRotate.setAttribute("aria-pressed", String(controls.autoRotate));
});
document.querySelectorAll("#modes [data-mode]").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));
document.querySelector("#play").addEventListener("click", () => { playing = !playing; updatePlay(); });
document.querySelector("#anim-reset").addEventListener("click", () => { phase = 0; });
document.querySelector("#scenario").addEventListener("change", (e) => {
  scenario = e.target.value;
  if (!rig) return;
  const dampers = scenario === "fault"
    ? [...rig.parts.keys()].filter((id) => id.startsWith("damper_")) : [];
  inspector.setAlerts(dampers);
  showPanel(scenario === "fault"
    ? `<h2>悬挂异常示意</h2><p class="group">预设动画 · 非诊断结论</p>
       <p>高亮一系减振器，叠加横向、垂向与摇头的简化运动，用于说明悬挂衰减异常时的车体姿态表现。</p>
       <p class="note">该动画为教学示意，不代表真实故障诊断或实车测量结果。</p>`
    : defaultPanel());
});

/* ---- mobile drawer ---- */
const drawerToggle = document.querySelector("#panel-toggle");
function openDrawer() { document.body.classList.add("drawer-open"); drawerToggle.setAttribute("aria-expanded", "true"); }
drawerToggle.addEventListener("click", () => {
  const open = document.body.classList.toggle("drawer-open");
  drawerToggle.setAttribute("aria-expanded", String(open));
});

/* ---- resize & loop ---- */
function resize() {
  const {clientWidth: w, clientHeight: h} = stage;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(stage);
resize();

const ease = (t) => 1 - Math.pow(1 - t, 3);
let last = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = Math.min((now - last) / 1000, .1); last = now;
  if (viewAnim.to && viewAnim.t < 1) {
    viewAnim.t = Math.min(1, viewAnim.t + dt * 2.2);
    camera.position.lerpVectors(viewAnim.from, viewAnim.to, ease(viewAnim.t));
  }
  const target = mode === "motion" ? envelopes[scenario] : [0, 0, 0];
  for (let i = 0; i < 3; i++) envelope[i] += (target[i] - envelope[i]) * (1 - Math.exp(-dt * 5));
  if (playing && data) phase += dt * Math.PI * 2 / data.motion.period * Number(document.querySelector("#rate").value);
  if (rig) {
    rig.pose(phase,
      envelope[0] * Math.sin(phase), envelope[1] * Math.sin(phase), envelope[2] * Math.cos(phase));
    sensors?.update(stage.clientWidth, stage.clientHeight);
  }
  controls.update();
  renderer.render(scene, camera);
});
