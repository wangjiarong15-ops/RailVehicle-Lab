import * as THREE from "three";
import {cameraRig} from "./camera.js";
import {createAssembly} from "./geometry.js";
import {inspection} from "./interaction.js";
import {materials, studio} from "./lighting.js";
import {motionClock} from "./motion.js";
import {loadDetailedModel} from "./model-loader.js";
import {playbackBatches} from "./playback-batches.js";
import {partBrowser} from "./part-browser.js";

export async function mount() {
  const [response, detailed] = await Promise.all([fetch("./assembly.json"), loadDetailedModel()]);
  if (!response.ok) throw new Error("Missing bogie assembly");
  const data = await response.json();
  if (data.format !== 1) throw new Error("Unsupported bogie assembly");
  const workbench = document.querySelector("#workbench");
  const surface = document.querySelector("#surface");
  const message = document.querySelector("#message");
  const viewControl = document.querySelector("#view");
  const hideControl = document.querySelector("#hide");
  const restoreControl = document.querySelector("#restore");
  const modelStatus = document.querySelector("#model-status");
  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: "high-performance"});
  const gl = renderer.getContext();
  const gpuInfo = gl.getExtension("WEBGL_debug_renderer_info");
  const gpu = gpuInfo ? gl.getParameter(gpuInfo.UNMASKED_RENDERER_WEBGL) : "";
  const software = /swiftshader|llvmpipe|warp|basic render/i.test(gpu);
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.65);
  renderer.setPixelRatio(pixelRatio);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-label", "可旋转的三维转向架");
  canvas.setAttribute("role", "img");
  surface.append(canvas);
  const scene = new THREE.Scene();
  const illumination = studio(scene, renderer);
  const finishes = materials();
  const assembly = createAssembly(scene, data, finishes, detailed);
  const hidden = new Set();
  let isolated = null;
  const batches = software && detailed ? playbackBatches(assembly, detailed) : null;
  modelStatus.textContent = detailed ? "Blender 精细模型" : "兼容几何 · GLB 不可用";
  const clock = motionClock();
  let args, serverArgs, localView, selection = null, appearance = "", angle = 0, width = 0, height = 0;
  let raf, frames = 0, dirty = true, alive = true, shadowPose = "", shadowDirty = true;
  let visibleComponents = 0;
  const invalidate = () => { dirty = true; };
  const rig = cameraRig(canvas, invalidate);
  const reset = () => { if (args) rig.reset(args.view, args); };
  function select(part) {
    selection = part;
    assembly.appearance({...args, hidden, isolated}, part);
    batches?.update(args.playing && !args.spread);
    const visible = assembly.objects.filter((mesh) => mesh.visible && mesh.parent.visible);
    visibleComponents = new Set(visible.map((mesh) => mesh.userData.id ?? mesh.userData.part)).size;
    const available = visible.some((mesh) => mesh.userData.id === part || mesh.userData.part === part);
    picker.select(part, available);
    browser?.update(part, hidden, isolated, args.visible, args.rails);
    workbench.dataset.selected = part ?? "";
    workbench.dataset.hidden = hidden.size;
    workbench.dataset.isolated = isolated ?? "";
    illumination.platform.visible = args.rails && !isolated;
    hideControl.disabled = !available || !assembly.partNodes.has(part);
    restoreControl.disabled = !hidden.size;
    invalidate();
  }
  const picker = inspection(canvas, data.parts, assembly, rig, select, reset, detailed?.parts);
  const browser = detailed ? partBrowser(detailed.parts, {
    select,
    show() {
      if (!hidden.delete(selection)) return;
      shadowDirty = true;
      select(selection);
    },
    isolate() {
      isolated = isolated ? null : selection;
      shadowDirty = true;
      select(selection);
      if (isolated) picker.zoom();
      else reset();
    },
  }) : null;
  function hideSelected() {
    if (!assembly.partNodes.has(selection)) return;
    hidden.add(selection);
    if (isolated === selection) {
      isolated = null;
      reset();
    }
    shadowDirty = true;
    select(selection);
  }
  function restoreHidden() {
    hidden.clear();
    shadowDirty = true;
    select(selection);
  }
  hideControl.addEventListener("click", hideSelected);
  restoreControl.addEventListener("click", restoreHidden);

  function size() {
    const w = surface.clientWidth, h = surface.clientHeight;
    if (!w || !h || (w === width && h === height)) return;
    const first = !width;
    width = w;
    height = h;
    renderer.setSize(w, h);
    rig.resize(w, h);
    if (first && args) reset();
    invalidate();
  }
  const observer = new ResizeObserver(size);
  observer.observe(surface);
  size();

  function frame(now) {
    if (!alive) return;
    raf = requestAnimationFrame(frame);
    if (!args || document.hidden) return;
    const pose = clock.tick(now);
    rig.controls.update();
    if (!(dirty || pose.running || pose.settling)) return;
    angle = pose.angle;
    assembly.pose(angle, pose.lateral, pose.vertical, pose.yaw);
    const nextShadowPose = [pose.lateral, pose.vertical, pose.yaw].map((value) => value.toFixed(3)).join(",");
    // The rotating steel parts are axially symmetric. Reuse their shadow in normal running.
    if (shadowDirty || shadowPose !== nextShadowPose) {
      renderer.shadowMap.needsUpdate = true;
      shadowPose = nextShadowPose;
      shadowDirty = false;
    }
    renderer.render(scene, rig.camera);
    picker.labels(args, width, height);
    dirty = false;
    // Lightweight diagnostics for checking interaction and renderer performance.
    Object.assign(workbench.dataset, {
      frames: ++frames, phase: angle.toFixed(4), zoom: rig.camera.zoom.toFixed(4),
      camera: rig.camera.position.toArray().map((x) => x.toFixed(3)).join(","),
      triangles: renderer.info.render.triangles, mode: args.mode,
      playing: String(args.playing),
      parts: args.visible.length, highlighted: args.highlight.join("、"), calls: renderer.info.render.calls,
      spread: args.spread,
      visibleComponents,
      axleAngle: assembly.partNodes.get("axle_a")?.parent.rotation.y.toFixed(4) ?? "",
      axleboxAngle: assembly.partNodes.get("axlebox_a_l")?.parent.rotation.y.toFixed(4) ?? "",
      discAngle: assembly.partNodes.get("disc_a_l")?.parent.rotation.y.toFixed(4) ?? "",
      pose: [pose.lateral, pose.vertical, pose.yaw].map((value) => value.toFixed(4)).join(","),
    });
  }
  function dispose() {
    if (!alive) return;
    alive = false;
    cancelAnimationFrame(raf);
    observer.disconnect();
    viewControl.removeEventListener("change", chooseView);
    hideControl.removeEventListener("click", hideSelected);
    restoreControl.removeEventListener("click", restoreHidden);
    rig.controls.dispose();
    picker.dispose();
    browser?.dispose();
    batches?.dispose();
    const disposable = new Set(Object.values(finishes).flat());
    scene.traverse(({geometry, material}) => {
      if (geometry) disposable.add(geometry);
      for (const item of [material].flat()) if (item) disposable.add(item);
    });
    for (const mesh of assembly.objects) for (const item of [mesh.userData.original].flat()) disposable.add(item);
    for (const resource of disposable) resource.dispose();
    illumination.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }
  window.addEventListener("pagehide", (event) => { if (!event.persisted) dispose(); }, {once: true});
  canvas.addEventListener("webglcontextlost", () => {
    if (!alive) return;
    dispose();
    workbench.dataset.status = "unavailable";
    message.hidden = false;
    message.textContent = "三维绘图已中断。请重新打开本页或使用左侧 Plotly 兼容模式。";
  });
  message.hidden = true;
  workbench.dataset.status = "ready";
  workbench.dataset.model = detailed ? "glb" : "procedural";
  workbench.dataset.components = String(assembly.partNodes.size);
  workbench.dataset.renderer = software ? "software" : "default";
  viewControl.disabled = false;
  viewControl.addEventListener("change", chooseView);
  raf = requestAnimationFrame(frame);

  function chooseView() {
    localView = viewControl.value;
    apply(serverArgs);
  }
  function apply(next) {
    if (!alive) return;
    const previous = args;
    const view = localView || next.view;
    args = {...next, view, rails: next.rails && view !== "底视图"};
    if (software && previous?.playing !== args.playing) {
      const playbackScale = detailed ? .60 : .66;
      renderer.setPixelRatio(pixelRatio * (args.playing ? playbackScale : 1));
      if (batches) renderer.shadowMap.enabled = !args.playing;
    }
    const isolatedGroup = detailed?.parts.get(isolated)?.spec.group;
    const isolationEnded = Boolean(isolated) &&
      !(isolatedGroup === "轨道参照" ? args.rails : args.visible.includes(isolatedGroup));
    if (isolationEnded) isolated = null;
    const reframe = isolationEnded || !previous || previous.view !== args.view || previous.revision !== args.revision
      || Boolean(previous.spread) !== Boolean(args.spread) || previous.rails !== args.rails;
    const newAppearance = JSON.stringify([
      args.visible, args.highlight, args.focus, args.rails, args.spread, software && detailed && args.playing,
    ]);
    if (newAppearance !== appearance) {
      appearance = newAppearance;
      const part = !previous || previous.focus !== args.focus ? args.focus : selection;
      const group = detailed?.parts.get(part)?.spec.group ?? part;
      select(detailed?.parts.has(part) || args.visible.includes(group) ? part : null);
      shadowDirty = true;
    }
    workbench.dataset.view = args.view;
    viewControl.value = args.view;
    if (reframe) reset();
    invalidate();
  }
  return function update(next) {
    if (!serverArgs || serverArgs.view !== next.view || serverArgs.revision !== next.revision) localView = null;
    serverArgs = next;
    clock.receive(next, performance.now());
    apply(next);
  };
}
