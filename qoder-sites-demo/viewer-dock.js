/* Permanently visible live control dock for the steerable 3D viewports
   (inspect / motion). Every control applies to the renderer immediately. */

const $ = (selector) => document.querySelector(selector);

export function createViewerDock({renderer, scene, camera, controls, rig, inspector, grid, lights, fog, points, particleCount, sensors, setDrift, onResize, onResetView, onUserCamera, setAutoRotate, onUserInteract}) {
  const key = $("#dk-key"), azimuth = $("#dk-azimuth"), rim = $("#dk-rim"), env = $("#dk-env");
  const dist = $("#dk-dist"), speed = $("#dk-speed"), quality = $("#dk-quality");
  const particles = $("#dk-particles"), glow = $("#dk-glow");
  const wire = $("#dk-wire"), ghost = $("#dk-ghost"), gridBtn = $("#dk-grid"), fogBtn = $("#dk-fog");
  const auto = $("#dk-auto"), driftBtn = $("#dk-drift"), exportBtn = $("#dk-export");
  const out = (input) => input.parentElement.querySelector("output");
  const DEFAULTS = {key: 1.6, azimuth: 49, rim: .5, env: 1, speed: 1.2, particles: .6, glow: .5};
  let azimuthValue = DEFAULTS.azimuth, driftOn = true;

  function paint() {
    out(key).textContent = Number(key.value).toFixed(2);
    out(azimuth).textContent = `${azimuth.value}°`;
    out(rim).textContent = Number(rim.value).toFixed(2);
    out(env).textContent = Number(env.value).toFixed(2);
    out(dist).textContent = `${Number(dist.value).toFixed(1)} m`;
    out(speed).textContent = `${Number(speed.value).toFixed(1)}×`;
    out(particles).textContent = `${Math.round(Number(particles.value) * 100)}%`;
    out(glow).textContent = Number(glow.value).toFixed(2);
  }
  function applyLight() {
    lights.key.intensity = Number(key.value);
    const rad = azimuthValue * Math.PI / 180;
    lights.key.position.set(Math.cos(rad) * 9.2, Math.sin(rad) * 9.2, 9);
    lights.rim.intensity = Number(rim.value);
    scene.environmentIntensity = Number(env.value);
  }
  function applyAtmosphere() {
    points.geometry.setDrawRange(0, Math.round(particleCount * Number(particles.value)));
    sensors?.setGlow(Number(glow.value));
  }
  function syncCamera() {
    const d = camera.position.distanceTo(controls.target);
    dist.value = String(Math.min(Number(dist.max), Math.max(Number(dist.min), d)));
    out(dist).textContent = `${Number(dist.value).toFixed(1)} m`;
  }
  function syncQuality() { quality.value = String(renderer.getPixelRatio()); }
  function pressed(button, on) { button.setAttribute("aria-pressed", String(on)); }

  key.addEventListener("input", () => { applyLight(); paint(); });
  azimuth.addEventListener("input", () => { azimuthValue = Number(azimuth.value); applyLight(); paint(); });
  rim.addEventListener("input", () => { applyLight(); paint(); });
  env.addEventListener("input", () => { applyLight(); paint(); });
  particles.addEventListener("input", () => { applyAtmosphere(); paint(); });
  glow.addEventListener("input", () => { applyAtmosphere(); paint(); });
  dist.addEventListener("input", () => {
    const direction = camera.position.clone().sub(controls.target).normalize();
    camera.position.copy(controls.target).add(direction.multiplyScalar(Number(dist.value)));
    controls.update();
    onUserCamera();
    paint();
  });
  speed.addEventListener("input", () => { controls.autoRotateSpeed = Number(speed.value); paint(); });
  quality.addEventListener("change", () => { renderer.setPixelRatio(Number(quality.value)); onResize(); });
  wire.addEventListener("click", () => {
    const on = wire.getAttribute("aria-pressed") !== "true";
    pressed(wire, on); inspector.setWireframe(on);
  });
  ghost.addEventListener("click", () => {
    const on = ghost.getAttribute("aria-pressed") !== "true";
    pressed(ghost, on); inspector.setGhost(on);
  });
  gridBtn.addEventListener("click", () => {
    const on = gridBtn.getAttribute("aria-pressed") !== "true";
    pressed(gridBtn, on); grid.visible = on;
  });
  fogBtn.addEventListener("click", () => {
    const on = fogBtn.getAttribute("aria-pressed") !== "true";
    pressed(fogBtn, on); scene.fog = on ? fog : null;
  });
  auto.addEventListener("click", () => setAutoRotate(!controls.autoRotate));
  driftBtn.addEventListener("click", () => {
    driftOn = !driftOn;
    pressed(driftBtn, driftOn); setDrift(driftOn);
  });
  controls.addEventListener("change", syncCamera);
  $("#view-dock").addEventListener("input", onUserInteract);
  $("#view-dock").addEventListener("click", onUserInteract);

  $("#dk-reset-view").addEventListener("click", () => { onResetView(); syncCamera(); });
  $("#dk-defaults").addEventListener("click", resetAll);
  exportBtn.addEventListener("click", () => {
    renderer.render(scene, camera);
    const link = document.createElement("a");
    link.download = "railvehicle-view.png";
    link.href = renderer.domElement.toDataURL("image/png");
    link.click();
    const label = exportBtn.textContent;
    exportBtn.textContent = "已导出 PNG";
    setTimeout(() => { exportBtn.textContent = label; }, 1200);
  });

  function resetAll() {
    key.value = String(DEFAULTS.key); azimuth.value = String(DEFAULTS.azimuth);
    rim.value = String(DEFAULTS.rim); env.value = String(DEFAULTS.env);
    speed.value = String(DEFAULTS.speed);
    particles.value = String(window.innerWidth <= 720 ? .35 : DEFAULTS.particles);
    glow.value = String(DEFAULTS.glow);
    azimuthValue = DEFAULTS.azimuth;
    controls.autoRotateSpeed = DEFAULTS.speed;
    applyLight(); applyAtmosphere();
    for (const [button, on] of [[wire, false], [ghost, false], [gridBtn, true], [fogBtn, true], [driftBtn, true]]) pressed(button, on);
    inspector.setWireframe(false); inspector.setGhost(false);
    grid.visible = true; scene.fog = fog;
    driftOn = true; setDrift(true);
    setAutoRotate(false);
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    syncQuality(); onResize(); onResetView(); syncCamera(); paint();
  }

  if (window.innerWidth <= 720) particles.value = "0.35";
  applyLight(); applyAtmosphere(); syncQuality(); syncCamera(); paint();
  pressed(gridBtn, grid.visible); pressed(fogBtn, !!scene.fog); pressed(driftBtn, driftOn);

  return {
    resetAll, syncCamera,
    syncDisplay() {
      pressed(wire, !!rig.objects[0]?.material.wireframe);
      pressed(ghost, inspector.ghosted);
    },
    state() {
      return {
        key: lights.key.intensity, azimuth: azimuthValue, rim: lights.rim.intensity,
        env: scene.environmentIntensity,
        dist: camera.position.distanceTo(controls.target),
        wire: !!rig.objects[0]?.material.wireframe, ghost: inspector.ghosted,
        grid: grid.visible, fog: !!scene.fog,
        auto: controls.autoRotate, speed: controls.autoRotateSpeed,
        quality: renderer.getPixelRatio(),
        particles: points.geometry.drawRange.count, glow: sensors?.glowLevel ?? 0, drift: driftOn,
      };
    },
  };
}