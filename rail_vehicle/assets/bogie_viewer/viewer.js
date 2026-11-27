import * as THREE from "three";
import {cameraRig} from "./camera.js";
import {cameraLink} from "./camera-link.js";
import {createAssembly} from "./geometry.js";
import {inspection} from "./interaction.js";
import {materials, studio} from "./lighting.js";
import {motionClock} from "./motion.js";
import {responsePlayer} from "./response-player.js";
import {loadDetailedModel} from "./model-loader.js";
import {playbackBatches} from "./playback-batches.js";
import {partBrowser} from "./part-browser.js";
import {measurement} from "./measurement.js";
import {sectionPlane} from "./section-plane.js";
import {exportInspectionPng} from "./inspection-export.js";
import {viewBookmarks} from "./view-bookmarks.js";
import {sensorMarker} from "./sensor-marker.js";
import {vehicleOverview} from "./vehicle-overview.js";
import {wheelRailFrameParts, wheelRailScene} from "./wheelrail-scene.js";

async function readAssembly(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("Missing bogie assembly: " + path);
  const data = await response.json();
  if (data.format !== 1 || !data.links?.length || !data.parts || data.axleX?.length !== 2 ||
      ![data.axleZ, data.wheelY, data.wheelPoseGain].every(Number.isFinite))
    throw new Error("Unsupported bogie assembly");
  return data;
}

export async function mount(sendValue) {
  const [layout, detailed] = await Promise.all([
    readAssembly("./assembly-layout.json").catch(() => null), loadDetailedModel(),
  ]);
  // Older bundles carry the same layout in the full procedural mesh document.
  let data = layout;
  if (!detailed || !data) {
    try {
      data = await readAssembly("./assembly.json");
      if (!detailed && !data.mesh?.length) throw new Error("Missing fallback geometry");
    } catch (cause) {
      throw new Error("assembly-unavailable", {cause});
    }
  }
  const workbench = document.querySelector("#workbench");
  const surface = document.querySelector("#surface");
  const message = document.querySelector("#message");
  const viewControl = document.querySelector("#view");
  const hideControl = document.querySelector("#hide");
  const restoreControl = document.querySelector("#restore");
  const measureControl = document.querySelector("#measure");
  const measurementPanel = document.querySelector("#measurement-panel");
  const measurementStatus = document.querySelector("#measurement-status");
  const clearMeasurementControl = document.querySelector("#clear-measurement");
  const exportControl = document.querySelector("#export-inspection");
  const exportStatus = document.querySelector("#export-status");
  const bookmarksToggle = document.querySelector("#bookmarks-toggle");
  const bookmarksPanel = document.querySelector("#bookmarks-controls");
  const modelStatus = document.querySelector("#model-status");
  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: "high-performance"});
  const gl = renderer.getContext();
  const gpuInfo = gl.getExtension("WEBGL_debug_renderer_info");
  const gpu = gpuInfo ? gl.getParameter(gpuInfo.UNMASKED_RENDERER_WEBGL) : "";
  const software = /swiftshader|llvmpipe|warp|basic render/i.test(gpu);
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.65);
  const quality = document.querySelector("#render-quality");
  let adaptive = software ? .6 : 1, currentRatio = 0, qualityLast = null, qualityFrames = [];
  function renderQuality(playing, now) {
    if (quality.value === "auto" && playing) {
      if (qualityLast !== null) qualityFrames.push(Math.min(200, now-qualityLast));
      qualityLast = now;
      if (qualityFrames.length >= 20) {
        const fps = 1000*qualityFrames.length/qualityFrames.reduce((sum, v) => sum+v, 0);
        if (fps < (software ? 19 : 32)) adaptive = Math.max(software ? .40 : .55, adaptive-.10);
        else if (fps > (software ? 32 : 54)) adaptive = Math.min(software ? .8 : 1, adaptive+.05);
        qualityFrames = [];
      }
    } else {
      qualityLast = null;
      qualityFrames = [];
    }
    const scales = {high:1, balanced:playing && software ? .6 : .85, low:playing ? .42 : .65,
      auto:playing ? adaptive : 1};
    const ratio = Math.min(pixelRatio, Math.max(.40, scales[quality.value]*pixelRatio));
    if (ratio !== currentRatio) {
      renderer.setPixelRatio(ratio);
      currentRatio = ratio;
      workbench.dataset.renderDpr = String(ratio);
    }
    if (workbench.dataset.renderQuality !== quality.value) workbench.dataset.renderQuality = quality.value;
  }
  renderQuality(false, performance.now());
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
  const wheelRailParts=wheelRailFrameParts(detailed);
  const assembly = createAssembly(scene, data, finishes, detailed);
  const hidden = new Set();
  let isolated = null;
  const batches = software && detailed ? playbackBatches(assembly, detailed) : null;
  modelStatus.textContent = detailed ? "Blender 精细模型" : "兼容几何 · GLB 不可用";
  const clock = motionClock();
  let args, serverArgs, localView, bookmarkDisplay, selection = null, appearance = "", angle = 0, width = 0, height = 0;
  let reviewSnapshot;
  let raf, frames = 0, dirty = true, alive = true, shadowPose = "", shadowDirty = true;
  let spreadNow = 0, spreadStart = 0, spreadFrom = 0;
  let visibleComponents = 0;
  let simulationPlaying = false;
  const invalidate = () => { dirty = true; };
  const player = responsePlayer(invalidate);
  const overviewButton = document.getElementById("vehicle-overview");
  const shellButton = document.getElementById("vehicle-shell");
  const heatButton = document.getElementById("vehicle-heat");
  const heatNote = document.getElementById("twin-heat-note");
  const twinPanes = document.getElementById("twin-panes");
  const forcesButton=document.getElementById("wheelrail-forces");
  const wheelFocusButton=document.getElementById("wheelrail-focus");
  const trackFocus=document.getElementById("wheelrail-target");
  const trackShell=document.getElementById("wheelrail-shell");
  const vectorsNote=document.getElementById("wheelrail-vector-note");
  let advanced, advancedOther, advancedId, showForces=false;
  let overview, comparison, overviewActive = false, heat = false;
  function overviewLayout() {
    const split = (overviewActive || Boolean(advanced)) && Boolean(serverArgs?.simulation?.alternative);
    overview?.resize(split ? width/2 : width,height);
    comparison?.resize(width/2,height);
    advanced?.resize(split ? width/2 : width,height);
    advancedOther?.resize(width/2,height);
    twinPanes.hidden = !split;
    heatButton.hidden = !(overviewActive && serverArgs?.simulation?.twin);
    heatNote.hidden = heatButton.hidden || !heat;
  }
  function configureAdvanced(next){
    const sim=next.simulation;
    const identity=sim?.advanced ? JSON.stringify([sim.parameters,sim.advanced.initial_focus]):null;
    if(identity===advancedId)return;
    advancedId=identity;
    const wasAdvanced=Boolean(advanced);
    advanced?.dispose();advancedOther?.dispose();advanced=advancedOther=null;
    showForces=false;
    trackShell.setAttribute("aria-pressed","false");
    trackShell.textContent="隐藏车体";
    forcesButton.setAttribute("aria-pressed","false");
    wheelFocusButton.textContent="前轴接触局部";
    wheelFocusButton.setAttribute("aria-pressed","false");
    if(sim?.advanced){
      const spec=sim.advanced;
      advanced=wheelRailScene(canvas,invalidate,scene.environment,spec.track,spec.geometry,wheelRailParts);
      if(sim.alternative){
        advancedOther=wheelRailScene(canvas,invalidate,scene.environment,spec.track,spec.geometry,wheelRailParts);
        advancedOther.controls.enabled=false;
      }
      modelStatus.textContent=wheelRailParts.length?"轮轨计算 · GLB 构架":"轮轨计算 · 兼容构架";
      workbench.dataset.wheelrailGlb=String(Boolean(wheelRailParts.length));
      setTrackFocus(spec.initial_focus??"vehicle");
    } else if (wasAdvanced) {
      modelStatus.textContent=detailed?"Blender 精细模型":"兼容几何 · GLB 不可用";
    }
    const active=Boolean(advanced);
    rig.controls.enabled=!active&&!overviewActive;
    forcesButton.hidden=wheelFocusButton.hidden=trackFocus.hidden=trackShell.hidden=!active;
    vectorsNote.hidden=true;
    workbench.dataset.wheelrail=String(active);
    overviewLayout();
    invalidate();
  }
  forcesButton.onclick=()=>{
    showForces=!showForces;
    vectorsNote.hidden=!showForces;
    forcesButton.setAttribute("aria-pressed",String(showForces));
    invalidate();
  };
  function setTrackFocus(target) {
    if(!advanced) return;
    trackFocus.value=target;
    advanced.focus(target);advancedOther?.focus(target);
    workbench.dataset.wheelrailFocus=target;
    const wheel=target==="front_wheel";
    wheelFocusButton.textContent=wheel?"查看整车":"前轴接触局部";
    wheelFocusButton.setAttribute("aria-pressed",String(wheel));
    player.refreshReadout();
  }
  trackFocus.onchange=()=>setTrackFocus(trackFocus.value);
  wheelFocusButton.onclick=()=>setTrackFocus(trackFocus.value==="front_wheel"?"vehicle":"front_wheel");
  trackShell.onclick=()=>{
    const hidden=trackShell.getAttribute("aria-pressed")!=="true";
    advanced?.setShell(hidden);advancedOther?.setShell(hidden);
    trackShell.textContent=hidden?"显示车体":"隐藏车体";
    trackShell.setAttribute("aria-pressed",String(hidden));
    workbench.dataset.wheelrailShell=String(!hidden);
    invalidate();
  };
  function changeOverview(active) {
    if (active && !overview) {
      overview = vehicleOverview(canvas, invalidate, scene.environment, serverArgs.simulation.coupled.dimensions);
    }
    overviewActive = active;
    if (overview) overview.controls.enabled = active;
    rig.controls.enabled = !active;
    workbench.dataset.vehicleOverview = String(active);
    overviewButton.setAttribute("aria-pressed", String(active));
    overviewButton.textContent = active ? "检查转向架 GLB" : "整车总览";
    shellButton.hidden = !active;
    overviewLayout();
    modelStatus.textContent = active ? "刚体整车示意 · " + (detailed ? "数值驱动" : "局部 GLB 不可用") :
      detailed ? "Blender 精细模型" : "兼容几何 · GLB 不可用";
    canvas.setAttribute("aria-label", active ? "可旋转的整车与前后转向架" : "可旋转的三维转向架");
    if (active) overview.reset(args?.view);
    invalidate();
  }
  overviewButton.onclick = () => changeOverview(!overviewActive);
  shellButton.onclick = () => {
    const transparent = shellButton.getAttribute("aria-pressed") !== "true";
    shellButton.setAttribute("aria-pressed", String(transparent));
    overview.setShellTransparency(transparent);
    comparison?.setShellTransparency(transparent);
  };
  heatButton.onclick = () => {
    heat = !heat;
    heatButton.setAttribute("aria-pressed", String(heat));
    overviewLayout();
    invalidate();
  };
  const surfaceMode = document.querySelector("#surface-mode");
  const copilotInspection = document.querySelector("#copilot-inspection");
  copilotInspection.onclick = () => {
    if (!args?.copilotInspection || !detailed || args.replayRunning || args.spread) return;
    const cut = section.capture();
    sendValue({schema:"rail.bogie-inspection.v1", part_id:detailed.parts.has(selection) ? selection : null,
      view:args.view, surface:surfaceMode.value, isolated:detailed.parts.has(isolated) ? isolated : null,
      cut_axis:cut.enabled ? cut.axis : null, cut_m:cut.enabled ? cut.position : null});
  };
  const fullScreen = document.querySelector("#full-screen");
  fullScreen.hidden = !document.fullscreenEnabled;
  fullScreen.onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await workbench.requestFullscreen();
    } catch { setExportStatus("浏览器未允许此视窗进入全屏。"); }
  };
  document.onfullscreenchange = () => {
    fullScreen.textContent = document.fullscreenElement ? "退出全屏" : "全屏";
    size();
    invalidate();
  };
  const variants = new Map();
  function inspectionSurface(original, mode) {
    if (Array.isArray(original)) return original.map(item => inspectionSurface(item, mode));
    let pair = variants.get(original);
    if (!pair) { pair = {}; variants.set(original, pair); }
    if (!pair[mode]) {
      const mat = original.clone();
      if (mode === "wire") mat.wireframe = true;
      else { mat.transparent = true; mat.opacity = .23; mat.depthWrite = false; }
      pair[mode] = mat;
    }
    return pair[mode];
  }
  surfaceMode.onchange = () => { if (args) { select(selection); shadowDirty = true; } };
  const rig = cameraRig(canvas, invalidate);
  const linkStatus = document.querySelector("#camera-link-status");
  const cameras = cameraLink(rig, (status) => {
    workbench.dataset.cameraLink = status;
    linkStatus.hidden = status === "off";
    linkStatus.textContent = {waiting: "正在连接另一视窗", linked: "镜头已联动",
      unsupported: "浏览器无法联动镜头"}[status] ?? "";
  });
  const gauge = measurement(scene, invalidate);
  let measuring = false;
  let settling = false;
  let exportPending = false;
  let exporting = false;
  const inspectionText = document.querySelector("#inspection");
  function setExportStatus(text) {
    exportStatus.textContent = text;
    exportStatus.hidden = !text;
  }
  function updateExportButton() {
    exportControl.disabled = !args || Boolean(args.simulation) || args.replayRunning || settling || exportPending || exporting;
  }
  function requestExport() {
    if (exportControl.disabled) return;
    exportPending = true;
    workbench.dataset.exportObservation = "";
    setExportStatus("");
    updateExportButton();
    invalidate();
  }
  function partName(id) {
    return detailed?.parts.get(id)?.spec.label ?? id ?? "";
  }
  function saveInspection() {
    exportPending = false;
    exporting = true;
    updateExportButton();
    const part = partName(selection);
    const isolatedName = partName(isolated);
    const metadata = {
      model: modelStatus.textContent,
      view: args.view + (args.relatedReview?.length ? " · 关联构件" : "") +
        (args.spread ? " · 拆解 " + Math.round(args.spread * 100) + "%" : ""),
      part: [part, isolatedName ? "仅显示 " + isolatedName : ""].filter(Boolean).join(" · "),
      section: section.enabled ? document.querySelector("#section-value").textContent : "",
      measurement: gauge.getState().count === 2 ? measurementStatus.textContent : "",
      observation: args.exportContext,
      reference: args.reference,
    };
    // Read the framebuffer in this render callback before WebGL clears it.
    exportInspectionPng(canvas, metadata).then(() => {
      if (alive) {
        workbench.dataset.exportObservation = metadata.observation?.sample_number ?? "";
        setExportStatus("已导出 PNG");
      }
    }).catch(() => {
      if (alive) setExportStatus("导出失败，请重试");
    }).finally(() => {
      exporting = false;
      if (alive) updateExportButton();
    });
  }
  exportControl.addEventListener("click", requestExport);
  function updateMeasurement() {
    const {count, distance, delta} = gauge.getState();
    workbench.dataset.measuring = String(measuring);
    workbench.dataset.measurePoints = String(count);
    workbench.dataset.measureDistance = distance ?? "";
    measureControl.setAttribute("aria-pressed", String(measuring));
    measureControl.disabled = !args || Boolean(args.simulation) || args.playing || Boolean(args.spread) || settling;
    clearMeasurementControl.disabled = count === 0;
    inspectionText.hidden = measuring;
    measurementPanel.hidden = !measuring;
    if (!measuring) return;
    const format = (value) => (Math.abs(value) < .0005 ? 0 : value).toFixed(3);
    measurementStatus.textContent = count === 2
      ? `直线距离 ${format(distance)} m · ΔX ${format(delta[0])} · ΔY ${format(delta[1])} · ΔZ ${format(delta[2])} m（模型示意）`
      : (count ? "点击第二个表面测点" : "点击第一个表面测点") + " · 模型示意";
  }
  function clearMeasurement() {
    gauge.clear();
    updateMeasurement();
  }
  function updateShadows() {
    renderer.shadowMap.enabled = !section.enabled && !(batches && (args?.playing || simulationPlaying));
    shadowDirty = true;
  }
  const section = sectionPlane(renderer, workbench, () => {
    clearMeasurement();
    updateShadows();
    invalidate();
  });
  const drawSensor = sensorMarker(assembly, scene, rig.camera, section);
  const selectedSensor = state => state?.sensors?.channels.find(s => s.channel === state.sensors.selected);
  function toggleMeasurement() {
    measuring = !measuring;
    clearMeasurement();
  }
  measureControl.addEventListener("click", toggleMeasurement);
  clearMeasurementControl.addEventListener("click", clearMeasurement);
  const reset = () => {
    if (advanced) advanced.reset(args?.view);
    if (overviewActive) overview.reset(args?.view);
    // The hidden inspection camera must be initialized before its first switch.
    if (args) rig.reset(args.view, args);
  };
  function select(part) {
    selection = part;
    const mode = surfaceMode.value;
    assembly.appearance({...args, drawSpread: spreadNow, hidden, isolated, surfaceMode: mode}, part);
    if (mode !== "solid") for (const mesh of assembly.objects) {
      if (mode === "wire" || (mode === "ghost" && mesh.userData.part === "构架") ||
          (mode === "selected" && mesh.userData.id === part))
        mesh.material = inspectionSurface(mesh.material, mode === "selected" ? "ghost" : mode);
    }
    batches?.update((args.playing || simulationPlaying) && !args.spread && mode === "solid");
    const visible = assembly.objects.filter((mesh) => mesh.visible && mesh.parent.visible);
    visibleComponents = new Set(visible.map((mesh) => mesh.userData.id ?? mesh.userData.part)).size;
    const available = visible.some((mesh) => mesh.userData.id === part || mesh.userData.part === part);
    picker.select(part, available);
    browser?.update(part, hidden, isolated, args.visible, args.rails);
    workbench.dataset.selected = part ?? "";
    workbench.dataset.selectedTransparent = String(visible.some(mesh => mesh.userData.id === part && mesh.material.transparent));
    showChannel();
    workbench.dataset.hidden = hidden.size;
    workbench.dataset.isolated = isolated ?? "";
    illumination.platform.visible = args.rails && !isolated;
    hideControl.disabled = !available || !assembly.partNodes.has(part);
    restoreControl.disabled = !hidden.size;
    invalidate();
  }
  function showChannel() {
    const group = detailed?.parts.get(selection)?.spec.group ?? selection;
    const sample = args.exportContext;
    const hint = document.getElementById("channel-context");
    const sensor = selectedSensor(args);
    if (sensor && (!detailed || !selection && !sensor.position)) {
      hint.textContent = sensor.serial + " · " + sensor.label + " · " +
        (!detailed ? "兼容模型缺少精细部件定位，可核查通道波形" : "安装位置未知或待核验");
      return;
    }
    const matches = args.sensors?.channels.filter(s => s.part && s.part === selection) ?? [];
    if (matches.length) {
      hint.textContent = matches.map(s => s.serial + " · " + s.label + " · v" + s.version +
        (s.position ? (s.illustration ? " · 示意安装点" : " · 登记安装点") : " · 安装位置待核验") +
        (s.direction ? " · 已登记测量方向" : " · 模型轴向未知")).join(" / ");
      return;
    }
    if (!group || !sample?.sample_number) {hint.textContent = ""; return;}
    const channels = Object.entries(args.reviewChannels ?? {}).filter(([, spec]) => spec.groups.includes(group));
    hint.textContent = channels.length ? sample.source + " · " + sample.sample_kind + " · " +
      (sample.row_scope ?? "批次") + "第 " + sample.sample_number + " 行 · " +
      channels.map(([field, spec]) => spec.label + " " + sample[field].toFixed(3)).join(" / ") +
      " m/s² · 安装点未知" : "此批次无该部件专用通道";
  }
  const picker = inspection(canvas, data, assembly, rig, select, reset, detailed?.parts, section, {
    enabled: () => !overviewActive && !advanced,
    active: () => measuring,
    hit(point) {
      if (measureControl.disabled) return;
      gauge.add(point);
      updateMeasurement();
    },
  });
  const browser = detailed ? partBrowser(detailed.parts, {
    select,
    show() {
      if (!hidden.delete(selection)) return;
      clearMeasurement();
      shadowDirty = true;
      select(selection);
    },
    isolate() {
      isolated = isolated ? null : selection;
      clearMeasurement();
      shadowDirty = true;
      select(selection);
      if (isolated) picker.zoom();
      else reset();
    },
  }) : null;
  const knownGroups = Object.keys(data.parts);
  const hasPart = (id) => id === null || assembly.partNodes.has(id);
  const hasSelection = (id) => hasPart(id) || knownGroups.includes(id);
  const bookmarkModel = detailed ? "glb" : "procedural";
  function validBookmark(state, checkParts) {
    if (!state || !["glb", "procedural"].includes(state.model) ||
        !Array.from(viewControl.options).some(({value}) => value === state.view) ||
        !rig.accepts(state.camera) || !section.accepts(state.section) ||
        !Array.isArray(state.visible) || state.visible.length > knownGroups.length ||
        !state.visible.every((group) => knownGroups.includes(group)) || typeof state.rails !== "boolean" ||
        !Array.isArray(state.hidden) || state.hidden.length > 54) return false;
    const isPartId = (id) => typeof id === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(id);
    const acceptsPart = checkParts ? (id) => id !== null && hasPart(id) : isPartId;
    if (state.model === "procedural") return state.hidden.length === 0 && state.isolated === null &&
      (state.selection === null || knownGroups.includes(state.selection));
    return state.hidden.every(acceptsPart) && (state.isolated === null || acceptsPart(state.isolated)) &&
      (checkParts ? hasSelection(state.selection) :
        state.selection === null || knownGroups.includes(state.selection) || isPartId(state.selection));
  }
  const bookmarks = viewBookmarks({
    capture() {
      if (!args || args.playing || args.spread || settling || reviewSnapshot) return null;
      return {
        model: bookmarkModel,
        view: args.view,
        camera: rig.capture(),
        visible: [...args.visible],
        rails: bookmarkDisplay?.rails ?? serverArgs.rails,
        hidden: [...hidden],
        isolated,
        selection,
        section: section.capture(),
      };
    },
    validate(state) {
      if (!validBookmark(state, state?.model === bookmarkModel)) return null;
      const {model, view, camera, visible, rails, hidden, isolated, selection, section} = state;
      return {
        model, view, visible, rails, hidden, isolated, selection,
        camera: {position: camera.position, target: camera.target, zoom: camera.zoom},
        section: {enabled: section.enabled, axis: section.axis, position: section.position, flipped: section.flipped},
      };
    },
    restore(state) {
      if (!args || args.playing || args.spread || settling || reviewSnapshot || state?.model !== bookmarkModel ||
          !validBookmark(state, true)) return false;
      localView = state.view;
      bookmarkDisplay = {visible: [...new Set(state.visible)], rails: state.rails};
      hidden.clear();
      for (const id of state.hidden) hidden.add(id);
      isolated = state.isolated;
      clearMeasurement();
      section.restore(state.section);
      apply(serverArgs);
      select(state.selection);
      rig.restore(state.camera);
      return true;
    },
  });
  function hideSelected() {
    if (!assembly.partNodes.has(selection)) return;
    hidden.add(selection);
    clearMeasurement();
    if (isolated === selection) {
      isolated = null;
      reset();
    }
    shadowDirty = true;
    select(selection);
  }
  function restoreHidden() {
    hidden.clear();
    clearMeasurement();
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
    overviewLayout();
    if (first && args) reset();
    invalidate();
  }
  const observer = new ResizeObserver(size);
  observer.observe(surface);
  size();
  quality.onchange = () => {
    renderQuality(Boolean(args?.playing || simulationPlaying),performance.now());
    invalidate();
  };

  function frame(now) {
    if (!alive) return;
    raf = requestAnimationFrame(frame);
    if (!args || document.hidden) return;
    const pose = player.tick(now) ?? clock.tick(now);
    renderQuality(Boolean(pose.running), now);
    if (simulationPlaying !== Boolean(args.simulation && pose.running)) {
      simulationPlaying = Boolean(args.simulation && pose.running);
      if (software) {
        updateShadows();
        select(selection);
      }
      invalidate();
    }
    if (spreadNow !== args.spread) {
      const t = Math.min(1, Math.max(0, (now-spreadStart)/360));
      spreadNow = t === 1 ? args.spread : spreadFrom + (args.spread-spreadFrom)*t*t*(3-2*t);
      assembly.setSpread(spreadNow);
      dirty = shadowDirty = true;
    }
    if (settling !== pose.settling) {
      settling = pose.settling;
      updateMeasurement();
      updateExportButton();
    }
    (advanced?.controls ?? (overviewActive ? overview.controls : rig.controls)).update();
    if (!(dirty || pose.running || pose.settling)) return;
    angle = pose.angle;
    if (advanced && pose.advanced){
      const clipping=renderer.clippingPlanes;
      renderer.clippingPlanes=[];
      try{
        if(advancedOther&&pose.alternate){
          const half=Math.floor(width/2);
          renderer.setScissorTest(true);
          renderer.setViewport(0,0,half,height);renderer.setScissor(0,0,half,height);
          advanced.draw(renderer,pose.advanced,pose.index,angle,showForces);
          renderer.setViewport(half,0,width-half,height);renderer.setScissor(half,0,width-half,height);
          advancedOther.draw(renderer,pose.alternate,pose.index,angle,showForces,advanced);
        }else advanced.draw(renderer,pose.advanced,pose.index,angle,showForces);
      }finally{
        renderer.setScissorTest(false);renderer.setViewport(0,0,width,height);
        renderer.clippingPlanes=clipping;
      }
      dirty=false;
      Object.assign(workbench.dataset,{
        frames:++frames,playing:String(simulationPlaying),triangles:renderer.info.render.triangles,
        wheelrailStation:pose.advanced.station[pose.index][0],
        wheelrailHeading:pose.advanced.pose[pose.index][0][5],
        wheelrailNormal:pose.advanced.tread_normal_n[pose.index][0].join(","),
        wheelrailOther:pose.alternate?.pose[pose.index][0][2]??"",
      });
      return;
    }
    if (overviewActive && pose.vehicle) {
      const clipping = renderer.clippingPlanes;
      renderer.clippingPlanes = [];
      try {
        if (pose.alternative) {
          if (!comparison) {
            comparison = vehicleOverview(canvas,invalidate,scene.environment,serverArgs.simulation.coupled.dimensions);
            comparison.setShellTransparency(shellButton.getAttribute("aria-pressed")==="true");
            overviewLayout();
          }
          const half = Math.floor(width/2);
          renderer.setScissorTest(true);
          renderer.setScissor(0,0,half,height);
          renderer.setViewport(0,0,half,height);
          overview.draw(renderer,pose.vehicle,angle,heat);
          renderer.setScissor(half,0,width-half,height);
          renderer.setViewport(half,0,width-half,height);
          comparison.draw(renderer,pose.alternative,angle,heat,overview);
        } else overview.draw(renderer,pose.vehicle,angle,heat);
      } finally {
        renderer.setScissorTest(false);
        renderer.setViewport(0,0,width,height);
        renderer.clippingPlanes = clipping;
      }
      dirty = false;
      Object.assign(workbench.dataset, {
        frames: ++frames, phase: angle.toFixed(4), playing: String(simulationPlaying),
        triangles: renderer.info.render.triangles, calls: renderer.info.render.calls,
        vehicleBody: [0,1,10,11,12].map(i=>pose.vehicle.q[i]).join(","),
        vehicleWheel: pose.vehicle.q.slice(6,10).join(","),
        twinAlternative: pose.alternative ? pose.alternative.q[10] : "",
      });
      return;
    }
    assembly.pose(angle, pose.lateral, pose.vertical, pose.yaw, pose.pitch, pose.wheelOffsets, pose.body);
    const partResponse = player.partResponse(selection);
    const partOutput = document.getElementById("simulation-part-response");
    if (partOutput.textContent !== partResponse) partOutput.textContent = partResponse;
    const nextShadowPose = [pose.lateral, pose.vertical, pose.yaw, pose.pitch ?? 0, ...(pose.wheelOffsets ?? []),
      pose.body?.y ?? 0, pose.body?.roll ?? 0, pose.body?.heave ?? 0, pose.body?.frameRoll ?? 0,
      ...(pose.body?.wheelY ?? [])]
      .map((value) => value.toFixed(3)).join(",");
    // The rotating steel parts are axially symmetric. Reuse their shadow in normal running.
    if (shadowDirty || shadowPose !== nextShadowPose) {
      renderer.shadowMap.needsUpdate = true;
      shadowPose = nextShadowPose;
      shadowDirty = false;
    }
    renderer.render(scene, rig.camera);
    if (exportPending && !args.replayRunning && !settling) saveInspection();
    picker.labels(args, width, height, isolated);
    const sensor = selectedSensor(args);
    const sensorGroup = detailed?.parts.get(sensor?.part)?.spec.group;
    drawSensor(sensor, (sensorGroup === "轨道参照" ? args.rails : args.visible.includes(sensorGroup)) &&
      !hidden.has(sensor.part) && (!isolated || isolated === sensor.part), width, height);
    dirty = false;
    // Lightweight diagnostics for checking interaction and renderer performance.
    Object.assign(workbench.dataset, {
      frames: ++frames, phase: angle.toFixed(4), zoom: rig.camera.zoom.toFixed(4),
      camera: rig.camera.position.toArray().map((x) => x.toFixed(3)).join(","),
      target: rig.controls.target.toArray().map((x) => x.toFixed(3)).join(","),
      triangles: renderer.info.render.triangles, mode: args.mode,
      playing: String(args.playing || simulationPlaying),
      parts: args.visible.length, highlighted: args.highlight.join("、"), calls: renderer.info.render.calls,
      spread: args.spread,
      visibleComponents,
      axleAngle: assembly.partNodes.get("axle_a")?.parent.rotation.y.toFixed(4) ?? "",
      framePitch: -assembly.frame.rotation.y,
      bodyRoll: pose.body?.roll ?? 0,
      secondaryScale: [...assembly.secondaryNodes.values()].map(node=>node.scale.z).join(","),
      wheelVertical: ["b", "a"].map(end => {
        const rotor = assembly.partNodes.get("axle_" + end)?.parent;
        return rotor?.parent.position.z ?? 0;
      }).join(","),
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
    measureControl.removeEventListener("click", toggleMeasurement);
    clearMeasurementControl.removeEventListener("click", clearMeasurement);
    exportControl.removeEventListener("click", requestExport);
    cameras.dispose();
    rig.controls.dispose();
    picker.dispose();
    browser?.dispose();
    bookmarks.dispose();
    for (const pair of variants.values()) for (const mat of Object.values(pair)) mat.dispose();
    batches?.dispose();
    assembly.dispose();
    overview?.dispose();
    comparison?.dispose();
    advanced?.dispose();
    advancedOther?.dispose();
    gauge.dispose();
    section.dispose();
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
    message.textContent = "三维绘图已中断。请重新打开本页，或在「视角与部件」中选择 Plotly 兼容模式。";
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
    if (overviewActive) overview.reset(localView);
    apply(serverArgs);
  }
  function apply(next, transition = false) {
    if (!alive) return;
    const previous = args;
    const review = Boolean(next.relatedReview?.length);
    const view = localView || (review ? "侧视图" : next.view);
    args = review ? {...next, view, visible: next.relatedReview, rails: false, focus: null, labels: true}
      : {...next, ...bookmarkDisplay, view, rails: (bookmarkDisplay?.rails ?? next.rails) && view !== "底视图"};
    copilotInspection.hidden = !args.copilotInspection || !detailed;
    copilotInspection.disabled = Boolean(args.replayRunning || args.spread);
    if (args.replayRunning || transition) {
      exportPending = false;
      setExportStatus("");
    }
    updateExportButton();
    showChannel();
    if (args.playing || args.spread) measuring = false;
    if (!previous || Boolean(previous.spread) !== Boolean(args.spread)) {
      illumination.fitShadows(Boolean(args.spread));
      shadowDirty = true;
    }
    if (previous?.spread !== args.spread) {
      spreadStart = performance.now();
      spreadFrom = spreadNow;
    }
    if (previous && (previous.phase !== args.phase || previous.mode !== args.mode ||
        previous.rails !== args.rails || previous.spread !== args.spread ||
        previous.visible.join() !== args.visible.join() || args.playing)) clearMeasurement();
    else updateMeasurement();
    if (software && previous?.playing !== args.playing) {
      updateShadows();
    }
    const isolatedGroup = detailed?.parts.get(isolated)?.spec.group;
    const isolationEnded = Boolean(isolated) &&
      !(isolatedGroup === "轨道参照" ? args.rails : args.visible.includes(isolatedGroup));
    if (isolationEnded) isolated = null;
    const reframe = transition || isolationEnded || !previous || previous.view !== args.view || previous.revision !== args.revision
      || Boolean(previous.spread) !== Boolean(args.spread) || previous.rails !== args.rails;
    const sensor = selectedSensor(args), lastSensor = selectedSensor(previous);
    const sensorChanged = sensor?.channel !== lastSensor?.channel || sensor?.version !== lastSensor?.version ||
      sensor?.serial !== lastSensor?.serial || sensor?.part !== lastSensor?.part;
    const newAppearance = JSON.stringify([
      args.visible, args.highlight, args.focus, args.rails, args.spread, software && detailed && args.playing, review,
    ]);
    if (transition || sensorChanged || newAppearance !== appearance) {
      appearance = newAppearance;
      let part = transition || !previous || previous.focus !== args.focus ? args.focus : selection;
      if (sensorChanged && !review) part = sensor?.position ? sensor.part : args.focus;
      const group = detailed?.parts.get(part)?.spec.group ?? part;
      select(detailed?.parts.has(part) || args.visible.includes(group) ? part : null);
      shadowDirty = true;
    }
    workbench.dataset.view = args.view;
    workbench.dataset.relatedReview = String(review);
    viewControl.value = args.view;
    if (reframe) reset();
    invalidate();
  }
  function sameBaseView(before, next) {
    return before.view === next.view && before.visible.join() === next.visible.join() && before.rails === next.rails &&
      before.revision === next.revision && before.focus === next.focus;
  }
  return function update(next) {
    if (!alive) return;
    const review = Boolean(next.relatedReview?.length);
    const entering = review && !reviewSnapshot;
    const leaving = review ? null : reviewSnapshot;
    const restoreView = leaving && sameBaseView(leaving.base, next);
    if (entering) {
      reviewSnapshot = {base: serverArgs ?? next, localView, bookmarkDisplay, hidden: [...hidden], isolated,
        selection: serverArgs ? selection : next.focus, camera: args && rig.capture(), section: section.capture(),
        bookmarksOpen: !bookmarksPanel.hidden};
      localView = bookmarkDisplay = null;
      hidden.clear();
      isolated = selection = null;
      measuring = false;
      section.restore({...reviewSnapshot.section, enabled: false});
      bookmarksPanel.hidden = true;
      bookmarksToggle.disabled = true;
      bookmarksToggle.setAttribute("aria-expanded", "false");
    } else if (leaving) {
      localView = restoreView ? leaving.localView : null;
      bookmarkDisplay = restoreView ? leaving.bookmarkDisplay : null;
      hidden.clear();
      for (const id of leaving.hidden) hidden.add(id);
      isolated = leaving.isolated;
      measuring = false;
      section.restore(leaving.section);
      bookmarksToggle.disabled = false;
      bookmarksPanel.hidden = !leaving.bookmarksOpen;
      bookmarksToggle.setAttribute("aria-expanded", String(leaving.bookmarksOpen));
      reviewSnapshot = null;
    } else if (!review) {
      if (!serverArgs || serverArgs.view !== next.view || serverArgs.revision !== next.revision) localView = null;
      if (serverArgs && (serverArgs.visible.join() !== next.visible.join() ||
          serverArgs.revision !== next.revision || serverArgs.rails !== next.rails)) bookmarkDisplay = null;
    }
    const command = next.studioCommand;
    const applyCommand = command && command.id !== serverArgs?.studioCommand?.id;
    const previousCommand = serverArgs?.studioCommand;
    const seek = command?.seek;
    const newSeek = seek && seek.id !== previousCommand?.seek?.id;
    if (applyCommand && (!previousCommand || !command.transport?.action) &&
        ["solid","ghost","wire"].includes(command.surface)) surfaceMode.value = command.surface;
    const hasCoupled = Boolean(next.simulation?.coupled);
    const enteredCoupled = hasCoupled && !serverArgs?.simulation?.coupled;
    serverArgs = next;
    player.load(next.simulation);
    overviewButton.hidden = !hasCoupled || !detailed;
    if (enteredCoupled || (!hasCoupled && overviewActive)) changeOverview(enteredCoupled);
    else overviewLayout();
    configureAdvanced(next);
    if (applyCommand && next.simulation && command.transport) {
      player.command(command.transport.action, newSeek ? seek.seconds : null);
    }
    clock.receive(next, performance.now());
    apply(next, entering || Boolean(leaving));
    if (restoreView) {
      select(leaving.selection);
      if (leaving.camera) rig.restore(leaving.camera);
    }
    cameras.connect(next.cameraGroup, next.reference);
  };
}
