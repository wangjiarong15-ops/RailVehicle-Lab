// Run against an already running offline demo server:
//   node scripts/check_bogie_browser.cjs http://127.0.0.1:8501 screenshots [legacy-model.glb]
const assert = require("node:assert/strict");
const {mkdir, readFile} = require("node:fs/promises");
const {join} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const url = new URL(process.argv[2] || "http://127.0.0.1:8501");
assert(["127.0.0.1", "localhost"].includes(url.hostname), "Use a local demo server");
url.searchParams.set("embed_options", "dark_theme");
const output = process.argv[3];
const legacyModel = process.argv[4];
const executablePath = process.env.RAIL_BROWSER_PATH;
const args = process.env.RAIL_SOFTWARE_WEBGL ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : [];
const button = (page, name) => page.getByRole("button", {name, exact: true});
const option = async (page, key, name, {force = false} = {}) => {
  const select = page.locator(".st-key-" + key).getByTestId("stSelectbox");
  const input = select.locator("input");
  if (!force && await input.inputValue() === name) return;
  const choice = page.locator("[role=option]").filter({hasText: name});
  await input.click();
  if (await input.getAttribute("aria-autocomplete") === "list") {
    if (force) {
      await input.fill("");
      await input.press("ArrowDown");
    }
    await input.fill(name);
    await input.press("ArrowDown");
    await input.press("Enter");
    return;
  }
  await choice.click();
};
async function until(read, check, reason, attempts = 60) {
  for (let i = 0; i < attempts; i++) {
    const value = await read();
    if (check(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error(reason);
}
async function screenshot(page, name) {
  if (output) await page.screenshot({path: join(output, name.replace("webgl", "glb") + ".png")});
}

async function downloadInspection(page, frame, canvas) {
  // Observe the actual strings painted onto the downloaded PNG before browser encoding.
  await canvas.evaluate(() => {
    if (!window.bogieExportText) {
      const fillText = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (text, ...args) {
        window.bogieExportText.push(String(text));
        return fillText.call(this, text, ...args);
      };
    }
    window.bogieExportText = [];
  });
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    frame.getByRole("button", {name: "导出检查图", exact: true}).click(),
  ]);
  assert.equal(download.suggestedFilename(), "railvehicle-bogie-inspection.png");
  const png = await readFile(await download.path());
  assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  assert.equal(png.toString("ascii", 12, 16), "IHDR");
  await until(() => frame.locator("#export-status").textContent(),
    (status) => /已导出|导出成功|已下载/.test(status), "The workbench did not report a successful export");
  const painted = await canvas.evaluate(() => window.bogieExportText);
  assert(painted.includes("三维转向架检查记录"), "The PNG title was not painted");
  return {download, png, painted};
}

async function checkInspectionExport(page, frame, canvas) {
  const size = await canvas.evaluate((element) => ({width: element.width, height: element.height}));
  const {download, png, painted} = await downloadInspection(page, frame, canvas);
  assert(!painted.includes("观测"), "Teaching PNG unexpectedly contained a replay observation");
  assert(png.readUInt32BE(16) >= size.width, "Export shrank the model horizontally");
  assert(png.readUInt32BE(20) > size.height, "Export did not leave space for inspection details");

  const modelPixels = await page.evaluate(async (base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], {type: "image/png"}));
    try {
      const sample = document.createElement("canvas");
      sample.width = sample.height = 80;
      const context = sample.getContext("2d");
      context.drawImage(bitmap, 0, 0, 80, 80);
      const {data} = context.getImageData(12, 24, 56, 32);
      let count = 0;
      for (let i = 0; i < data.length; i += 4) {
        // The selected brake disc is highlighted blue. The dark background and
        // surrounding text do not have enough blue saturation to pass this test.
        if (data[i + 2] > 100 && data[i + 2] - data[i] > 35 && data[i + 3] === 255) count++;
      }
      return count;
    } finally {
      bitmap.close();
    }
  }, png.toString("base64"));
  assert(modelPixels > 80, "The exported PNG does not contain the highlighted model");
  if (output) await download.saveAs(join(output, "railvehicle-bogie-inspection.png"));
}

async function checkReplayInspectionExport(page, frame, canvas, data) {
  const {download, painted} = await downloadInspection(page, frame, canvas);
  const notes = painted.join(" ");
  await until(data, (state) => state.exportObservation === "32", "PNG was not linked to observation row 32");
  assert.match(notes, /来源 .*内置虚构演示.*非实际测量.*虚构批次 1/);
  assert.match(notes, /观测 .*第 32\/120 条.*2026-10-08.*09:30:31/);
  assert.match(notes, /速度 .*km\/h/);
  assert.match(notes, /加速度 .*横向 .*1\.800.*m\/s².*垂向 .*m\/s²/);
  assert.match(notes, /阈值状态 .*横向加速度超阈值.*加速度绝对值阈值 1 m\/s²/);
  if (output) await download.saveAs(join(output, "railvehicle-bogie-replay-inspection.png"));
}

async function checkSection(page, frame, canvas, data) {
  const section = frame.getByRole("button", {name: "剖切视图", exact: true});
  const axis = frame.getByLabel("剖切轴", {exact: true});
  const position = frame.getByLabel("剖切位置", {exact: true});
  const flip = frame.getByRole("button", {name: "翻转保留侧", exact: true});
  assert.equal(await section.getAttribute("aria-pressed"), "false");
  const uncut = await canvas.screenshot();
  const previous = await data();
  await section.click();
  await until(data, (d) => d.sectionAxis === "x" && d.sectionPosition !== undefined &&
    Number(d.sectionPosition) === 0 && d.sectionFlipped === "false" &&
    Number(d.frames) > Number(previous.frames), "The initial X section did not render");
  assert.equal(await section.getAttribute("aria-pressed"), "true");
  assert.equal(await axis.inputValue(), "x");
  assert.notDeepEqual(await canvas.screenshot(), uncut, "Enabling the section did not change the rendered canvas");
  await screenshot(page, "bogie-glb-section");

  await axis.selectOption("z");
  const zSection = await until(data, (d) => d.sectionAxis === "z", "The section axis did not change to Z");
  await position.press("ArrowRight");
  const shifted = await until(data, (d) => Number(d.sectionPosition) > Number(zSection.sectionPosition),
    "Moving the section position did not change the clipping plane");
  assert.equal(Number(await position.inputValue()), Number(shifted.sectionPosition));
  const beforeFlip = await canvas.screenshot();
  const frameBeforeFlip = Number((await data()).frames);
  await flip.click();
  await until(data, (d) => d.sectionFlipped === "true" && Number(d.frames) > frameBeforeFlip,
    "Flipping the section did not render the opposite side");
  assert.equal(await flip.getAttribute("aria-pressed"), "true");
  assert.notDeepEqual(await canvas.screenshot(), beforeFlip, "Flipping did not change the rendered canvas");

  await flip.click();
  await position.press("Home");
  const minimum = Number(await position.getAttribute("min"));
  await until(data, (d) => d.sectionFlipped === "false" && Number(d.sectionPosition) === minimum,
    "The section did not reach its lower limit");
  const viewport = await canvas.boundingBox();
  const pickCenter = () => canvas.click({position: {x: viewport.width / 2, y: viewport.height / 2}});
  await pickCenter();
  assert.equal((await data()).selected, "", "Picking reached geometry removed by the section plane");
  const measure = frame.getByRole("button", {name: "两点测距", exact: true});
  await measure.click();
  await pickCenter();
  assert.equal((await data()).measurePoints, "0", "Measurement reached geometry removed by the section plane");
  await measure.click();

  const frameBeforeClose = Number((await data()).frames);
  await section.click();
  await until(data, (d) => d.sectionAxis === "" && Number(d.frames) > frameBeforeClose,
    "Disabling the section did not restore the full model");
  assert.equal(await section.getAttribute("aria-pressed"), "false");
  assert.equal(await axis.isVisible(), false);
  await pickCenter();
  assert((await data()).selected, "Picking the same location did not recover after disabling the section");
  await canvas.click({position: {x: 5, y: 5}});
  assert.equal((await data()).selected, "", "Clicking the background did not clear the test selection");
}

async function checkDamperGeometry(root, format) {
  // Build a separate scene from the real component modules, so the endpoints can
  // be checked at exact poses without polling or instrumenting the live viewer.
  const checked = await root.evaluate(async (_, format) => {
    const [THREE, {createAssembly}, {loadDetailedModel}, {materials}, {playbackBatches}, data] =
      await Promise.all([import("./vendor/three.module.min.js"), import("./geometry.js"),
        import("./model-loader.js"), import("./lighting.js"), import("./playback-batches.js"),
        fetch("./assembly.json").then((response) => response.json())]);
    const useGlb = format !== "procedural", legacy = format === "legacy";
    const model = useGlb ? await loadDetailedModel() : null;
    if (useGlb && !model) throw new Error("Could not load the damper meshes from GLB");
    const scene = new THREE.Scene();
    const assembly = createAssembly(scene, data, materials(), model);
    const visible = {visible: Object.keys(data.parts), rails: true, highlight: [], spread: 0};
    assembly.appearance(visible);
    assembly.pose(0, 0, 0, 0);
    scene.updateMatrixWorld(true);
    const vector = (point) => new THREE.Vector3(...point);
    const dampers = data.links.flatMap(([, kind, a, b], index) => {
      if (kind !== "damper") return [];
      const imported = model && [...model.parts.values()].find(({spec}) => spec.link === index);
      if (model && (!imported || Boolean(imported.damperRod) === legacy))
        throw new Error(`Wrong damper hierarchy at link ${index} for ${format}`);
      // Compatibility geometry has no single-part IDs. Find its link by its
      // lower joint and its pair of cylindrical meshes.
      const node = imported ? imported.node.parent : scene.children.find((candidate) =>
        candidate.position.distanceTo(vector(a)) < 1e-6 && candidate.children.filter((mesh) => mesh.isMesh).length === 2);
      if (!node) throw new Error(`Damper missing at link ${index}`);
      return [{a: vector(a), b: vector(b), node, offset: imported && vector(imported.spec.offset),
        meshes: imported?.meshes ?? node.children.filter((mesh) => mesh.isMesh)}];
    });
    const compare = (actual, expected, message) => {
      if (!Number.isFinite(actual) || Math.abs(actual - expected) > 1e-5)
        throw new Error(`${message}: ${actual} versus ${expected}`);
    };
    const bounds = (meshes, a, b) => {
      const axis = b.clone().sub(a).normalize();
      return meshes.map((mesh) => {
        const position = mesh.geometry.getAttribute("position"), vertex = new THREE.Vector3();
        let min = Infinity, max = -Infinity;
        for (let i = 0; i < position.count; i++) {
          const station = vertex.fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld).sub(a).dot(axis);
          min = Math.min(min, station);
          max = Math.max(max, station);
        }
        return {min, max};
      }).sort((a, b) => a.min - b.min);
    };
    const batches = model && playbackBatches(assembly, model);
    const neutral = dampers.map(({meshes, a, b}) => bounds(meshes, a, b));
    const poses = [[0, 0, 0, 0], [1.57, 0, -.065, 0], [4.71, .075, .065, 0], [0, 0, 0, -.049]];
    if (model) poses.push([0, 0, 0, 0, .8]);
    for (const [phase, lateral, vertical, yaw, spread = 0] of poses) {
      assembly.appearance({...visible, spread});
      batches?.update(!spread);
      assembly.pose(phase, lateral, vertical, yaw);
      scene.updateMatrixWorld(true);
      for (const [index, {a, b, meshes, node, offset}] of dampers.entries()) {
        const lower = a.clone().applyMatrix4(assembly.wheel.matrixWorld);
        const upper = b.clone().applyMatrix4(assembly.frame.matrixWorld);
        if (spread) for (const joint of [lower, upper]) joint.addScaledVector(offset, spread);
        const [body, rod] = bounds(meshes, lower, upper);
        const [restBody, restRod] = neutral[index];
        compare(body.min, 0, "The cylinder left its lower joint");
        compare(rod.max, lower.distanceTo(upper), "The rod left its upper joint");
        const scale = legacy ? lower.distanceTo(upper) / a.distanceTo(b) : 1;
        compare(body.max - body.min, scale * (restBody.max - restBody.min), "Unexpected cylinder length");
        compare(rod.max - rod.min, scale * (restRod.max - restRod.min), "Unexpected rod length");
        if (body.max <= rod.min) throw new Error("The rod disengaged from the cylinder");
        if (batches && !spread) {
          const merged = [];
          node.traverse((mesh) => {
            if (mesh.isMesh && mesh.material.vertexColors && mesh.layers.mask === 1) merged.push(mesh);
          });
          if (merged.length !== (legacy ? 1 : 2))
            throw new Error("Unexpected count of damper software batches");
          const [batchBody, batchRod] = bounds(merged, lower, upper);
          compare((batchRod ?? batchBody).max, rod.max, "The batched rod is out of position");
          if (batchRod) {
            compare(batchBody.max, body.max, "The batched cylinder moved away from the visible source");
            compare(batchRod.min, rod.min, "The batched rod did not follow the upper joint");
          }
        }
      }
    }
    batches?.dispose();
    return dampers.length;
  }, format);
  assert.equal(checked, 4, `All four dampers must remain connected in ${format}`);
}

async function checkMotionClock(root) {
  const poses = await root.evaluate(async () => {
    // Exercise the exact module loaded by the viewer with controlled arrival times.
    const {motionClock} = await import("./motion.js");
    const clock = motionClock();
    const teaching = {period: 2.4, periodic: true, hold: false, phase: 5.7,
      playing: true, rate: 4, envelope: [0, 0, 0], spread: 0};
    clock.receive(teaching, 0);
    const forward = [];
    for (let now = 0; now <= 320; now += 16) forward.push(clock.tick(now).angle);
    // A late packet from the next teaching cycle carries a smaller wrapped phase.
    clock.receive({...teaching, phase: .5}, 320);
    for (let now = 336; now <= 416; now += 16) forward.push(clock.tick(now).angle);

    const history = {...teaching, periodic: false, rate: 1, phase: 12, envelope: [.075, .065, .049]};
    clock.receive(history, 500);
    const beforeRewind = clock.tick(570);
    clock.receive({...history, phase: 3}, 600);
    const rewound = clock.tick(600);
    clock.tick(665);
    clock.receive({...history, hold: true, playing: false, phase: 0, envelope: [0, 0, 0]}, 680);
    const gap = clock.tick(680), held = clock.tick(1000);
    clock.receive({...history, phase: 8}, 1020);
    const resumed = clock.tick(1060);
    const stale = clock.tick(1821), frozen = clock.tick(1850);
    clock.receive({...history, phase: 10}, 1900);
    return {forward, beforeRewind, rewound, gap, held, resumed, stale, frozen, reanchored: clock.tick(1900)};
  });
  assert(poses.forward.at(-1) > 2 * Math.PI, "Teaching motion did not cross the cycle boundary");
  assert(poses.forward.every((angle, index, list) => index === 0 || angle > list[index - 1]),
    "An out-of-date teaching snapshot made the wheel backtrack");
  assert(poses.beforeRewind.angle > 12);
  assert.equal(poses.rewound.angle, 3, "An event loop rewind must use the destination batch time immediately");
  const neutral = {running: false, settling: false, angle: 0, lateral: 0, vertical: 0, yaw: 0};
  assert.deepEqual(poses.gap, neutral, "A gap retained the previous sample's pose");
  assert.deepEqual(poses.held, neutral, "A gap did not hold the neutral model");
  assert(poses.resumed.running && poses.resumed.angle > 8 && Math.abs(poses.resumed.vertical) > .001,
    "The model did not resume from the next observation");
  assert(!poses.stale.running && poses.frozen.angle === poses.stale.angle,
    "A disconnected viewer kept advancing the illustration");
  assert.equal(poses.reanchored.angle, 10, "The first fresh snapshot did not resync the viewer");
}

async function checkTeachingControls(page, data, play, pause, reset) {
  await option(page, "bogie_teaching", "正常运行");
  await option(page, "bogie_rate", "4×");
  await play.click();
  const wrapped = await until(data, (d) => d.playing === "true" && Number(d.phase) >= 2 * Math.PI,
    "4× teaching playback did not continue through a full cycle");
  await option(page, "bogie_rate", "0.5×");
  await until(data, (d) => d.playing === "true" && Number(d.phase) > Number(wrapped.phase) + .3,
    "A teaching rate change stopped or rewound the running wheel");
  await pause.click();
  const paused = await until(data, (d) => d.playing === "false", "Teaching pause did not reach the viewer");
  await page.waitForTimeout(230);
  assert.equal((await data()).phase, paused.phase, "Paused teaching wheel kept turning");
  await play.click();
  await until(data, (d) => d.playing === "true" && Number(d.frames) > Number(paused.frames) + 4,
    "Teaching playback did not resume");
  await reset.click();
  await until(data, (d) => d.playing === "false" && Number(d.phase) === 0, "Teaching reset did not return to zero");
  await option(page, "bogie_rate", "1×");
  console.log("Teaching phase continued through wrap, rate change, pause, resume and reset");
}

async function openReplayEvent(page, data) {
  await page.getByText("内置虚构演示", {exact: true}).click();
  await page.getByText(/定位超阈值区段（/).click();
  await button(page, "定位区段起点").click();
  await until(data, (d) => d.highlighted.length > 0, "Replay did not highlight event");
  await page.getByText(/第 31\/120 条/).waitFor();
}

async function checkRelatedReview(page, frame, canvas, data, width, full) {
  const inspect = button(page, "查看关联构件");
  const returnToView = button(page, "返回原检查视图");
  if (full) {
    // Hide a damper that should reappear in the temporary related view.
    await frame.getByRole("button", {name: "零件清单"}).click();
    const search = frame.getByRole("searchbox", {name: "查找零件"});
    await search.fill("damper_a_l");
    await frame.locator('[data-part="damper_a_l"]').click();
    await frame.getByRole("button", {name: "隐藏零件"}).click();
    await search.press("Escape");
    await frame.getByRole("combobox", {name: "视窗视角"}).selectOption("俯视图");
    await until(data, (d) => d.view === "俯视图" && d.hidden === "1" && d.selected === "damper_a_l",
      "A manual view with a hidden related part was not established");
    const initial = await data();
    await canvas.hover({position: {x: 190, y: 140}});
    await page.mouse.wheel(0, -280);
    await until(data, (d) => Number(d.zoom) > Number(initial.zoom), "Manual zoom did not change");
  }
  const before = await data();
  const row = page.getByText(/第 31\/120 条/);
  const observation = await row.textContent();
  const threshold = page.getByLabel("加速度绝对值阈值（m/s²）", {exact: true});
  const limit = await threshold.inputValue();
  await inspect.click();
  const active = await until(data, (d) => d.relatedReview === "true" && d.view === "侧视图" &&
    Number(d.parts) === d.highlighted.split("、").length, "Related part review did not show the scene categories");
  assert.equal(active.highlighted, before.highlighted, "Review changed the rule highlight");
  assert.equal(active.phase, before.phase, "Review changed the observed playback position");
  assert.equal(await row.textContent(), observation);
  assert.equal(await threshold.inputValue(), limit);
  if (full) assert.equal(active.hidden, "0", "Review did not reveal the hidden related part");
  await page.getByRole("heading", {name: "三维诊断工作台"}).scrollIntoViewIfNeeded();
  // A focused Streamlit button can retain its help tooltip after the pointer moves.
  await returnToView.evaluate((element) => element.blur());
  await page.mouse.move(30, 430);
  await page.waitForTimeout(200);
  await screenshot(page, `bogie-webgl-related-review-${width}`);

  await returnToView.click();
  const restored = await until(data, (d) => d.relatedReview === "false" && d.view === before.view &&
    Math.abs(Number(d.zoom) - Number(before.zoom)) < .025 && d.hidden === before.hidden &&
    d.selected === before.selected && d.visibleComponents === before.visibleComponents,
    "Leaving review did not restore the camera, selection and hidden parts");
  assert.equal(restored.camera, before.camera, "Leaving review did not restore the previous camera position");
  assert.equal(restored.phase, before.phase);
  assert.equal(await row.textContent(), observation);
  if (!full) return;

  await inspect.click();
  await until(data, (d) => d.relatedReview === "true", "Could not re-enter related review");
  await button(page, "下一帧").click();
  await page.getByText(/第 32\/120 条/).waitFor();
  await until(data, (d) => d.relatedReview === "false" && d.view === before.view && d.hidden === before.hidden,
    "Moving to a different observation did not exit related review");
  await frame.getByRole("button", {name: "恢复隐藏"}).click();
  await until(data, (d) => d.hidden === "0", "Could not restore the hidden damper after review");
  console.log("Linked-part review kept evidence fixed and restored the original inspection state");
}

async function audit(browser, width, height, complete = true) {
  const page = await browser.newPage({viewport: {width, height}, screen: {width, height}});
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) =>
    new URL(route.request().url()).hostname === url.hostname ? route.continue() : route.abort());
  try {
    const address = new URL(url);
    address.searchParams.set("rail_page", "三维转向架仿真");
    await page.goto(address.href);
    await page.getByRole("heading", {name: "三维诊断工作台"}).waitFor();
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const root = frame.locator("#workbench");
    const canvas = frame.locator("canvas");
    const data = () => root.evaluate((node) => ({...node.dataset}));
    await frame.locator("#workbench[data-status=ready][data-frames]").waitFor();
    assert.equal((await data()).model, "glb");
    assert.equal((await data()).components, "54");
    const box = await canvas.boundingBox();
    assert(box.width >= 390 && box.height >= 320, "Canvas should occupy the work area");
    assert(box.x >= 0 && box.x + box.width <= width && box.y + box.height < height, "Canvas should fit");
    assert.equal((await data()).parts, "7");
    await screenshot(page, "bogie-webgl-" + width);
    if (!complete) {
      await openReplayEvent(page, data);
      await checkRelatedReview(page, frame, canvas, data, width, false);
      return;
    }
    await checkMotionClock(root);
    await checkDamperGeometry(root, "glb");
    await page.setViewportSize({width: 1366, height: 768});
    await until(() => canvas.boundingBox(), (resized) => resized.height < box.height - 100, "Viewport did not resize");
    await page.setViewportSize({width, height});
    await until(() => canvas.boundingBox(), (resized) => Math.abs(resized.height - box.height) < 2, "Viewport did not grow again");

    await checkSection(page, frame, canvas, data);
    const initial = await data();
    await page.mouse.move(box.x + box.width * .56, box.y + box.height * .49);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * .67, box.y + box.height * .51, {steps: 12});
    await page.mouse.up();
    await until(data, (d) => d.camera !== initial.camera, "Orbit drag did not change camera");
    await page.mouse.wheel(0, -240);
    await until(data, (d) => Number(d.zoom) > Number(initial.zoom), "Wheel did not zoom");
    await frame.getByRole("button", {name: "恢复视角"}).click();
    await until(data, (d) => d.camera === initial.camera, "Camera reset did not restore view");
    for (const [x, y] of [[.28,.57],[.37,.57],[.41,.52],[.55,.45],[.61,.60]]) {
      await canvas.click({position: {x: box.width * x, y: box.height * y}});
      if ((await data()).selected) break;
    }
    assert((await data()).selected, "Canvas click should select a component");
    assert((await frame.locator("#inspection").textContent()).includes((await data()).selected));
    await frame.getByRole("button", {name: "聚焦零件"}).click();
    await until(data, (d) => Number(d.zoom) > Number(initial.zoom), "Focus did not magnify selection");
    await screenshot(page, "bogie-webgl-wheel-detail");
    await frame.getByRole("button", {name: "隐藏零件"}).click();
    await until(data, (d) => d.hidden === "1" && Number(d.visibleComponents) === Number(initial.visibleComponents) - 1,
      "Hiding a picked GLB part did not remove its geometry");
    await frame.getByRole("button", {name: "恢复隐藏"}).click();
    await until(data, (d) => d.hidden === "0" && d.visibleComponents === initial.visibleComponents, "Part was not restored");

    const partsButton = frame.getByRole("button", {name: "零件清单"});
    const search = frame.getByRole("searchbox", {name: "查找零件"});
    const disc = frame.locator('[data-part="disc_a_l"]');
    await partsButton.click();
    await search.fill("无匹配测试");
    await frame.getByText("没有匹配的零件", {exact: true}).waitFor();
    await search.fill("damper_a_l");
    assert.equal(await frame.locator(".part-row:visible").count(), 1);
    await frame.locator('[data-part="damper_a_l"]').click();
    await until(data, (d) => d.selected === "damper_a_l", "Damper selection lost its stable ID");
    await frame.getByRole("button", {name: "仅看所选"}).click();
    await until(data, (d) => d.isolated === "damper_a_l" && d.visibleComponents === "1",
      "The cylinder and rod did not isolate as one damper");
    await screenshot(page, "bogie-glb-damper");
    await frame.getByRole("button", {name: "退出隔离"}).click();
    await frame.getByRole("button", {name: "隐藏零件"}).click();
    await until(data, (d) => d.hidden === "1" && Number(d.visibleComponents) === Number(initial.visibleComponents) - 1,
      "Both damper surfaces should be hidden together");
    await frame.getByRole("button", {name: "恢复所选"}).click();
    await until(data, (d) => d.hidden === "0" && d.visibleComponents === initial.visibleComponents,
      "The damper did not restore as one part");
    await search.fill("制动盘");
    assert.equal(await frame.locator(".part-row:visible").count(), 4);
    await search.fill("disc_a_l");
    assert.equal(await frame.locator(".part-row:visible").count(), 1);
    await disc.click();
    await until(data, (d) => d.selected === "disc_a_l", "Search selection did not select the GLB part");
    assert((await frame.locator("#inspection").textContent()).includes("A 轴 左侧 制动盘"));
    await frame.getByRole("button", {name: "隐藏零件"}).click();
    await disc.getByText("已隐藏").waitFor();
    await frame.getByRole("button", {name: "恢复所选"}).click();
    await until(data, (d) => d.selected === "disc_a_l" && d.hidden === "0", "Selected part did not restore");
    await frame.getByRole("button", {name: "仅看所选"}).click();
    await until(data, (d) => d.isolated === "disc_a_l" && d.visibleComponents === "1",
      "Isolating a disc did not leave exactly one part");
    await screenshot(page, "bogie-glb-part-inspector");
    const measure = frame.getByRole("button", {name: "两点测距", exact: true});
    const clearPoints = frame.getByRole("button", {name: "清除测点", exact: true});
    const measurementStatus = frame.locator("#measurement-status");
    await measure.click();
    assert.equal(await measure.getAttribute("aria-pressed"), "true");
    const isolatedBox = await canvas.boundingBox();
    const measureAt = (x, y) => canvas.click({position: {x: isolatedBox.width * x, y: isolatedBox.height * y}});
    await measureAt(.40, .50);
    await until(data, (d) => d.measurePoints === "1", "First surface click did not add a measurement point");
    assert.equal((await data()).measureDistance, "");
    await measureAt(.60, .50);
    const measured = await until(data, (d) => d.measurePoints === "2" && Number(d.measureDistance) > 0,
      "Second surface click did not produce a positive distance");
    assert(Number(measured.measureDistance) < .6, "Measured distance exceeds the isolated brake disc's size");
    assert.equal(measured.selected, "disc_a_l", "Measurement clicks changed the inspected part");
    assert.match(await measurementStatus.textContent(), /m|米/);
    await screenshot(page, "bogie-glb-measurement");
    await checkInspectionExport(page, frame, canvas);
    await clearPoints.click();
    await until(data, (d) => d.measurePoints === "0" && !d.measureDistance, "Clear did not remove measurement points");
    await measureAt(.40, .50);
    await until(data, (d) => d.measurePoints === "1", "Could not place a new point after clearing");
    await measure.click();
    assert.equal(await measure.getAttribute("aria-pressed"), "false");
    await until(data, (d) => d.measurePoints === "0" && !d.measureDistance,
      "Leaving measurement mode did not remove the pending point");
    await frame.getByRole("button", {name: "退出隔离"}).click();
    await until(data, (d) => d.isolated === "" && d.visibleComponents === initial.visibleComponents,
      "Leaving isolation did not restore the assembly");
    await search.fill("轨道参照");
    await frame.locator('[data-part="reference_track"]').click();
    await frame.getByRole("button", {name: "仅看所选"}).click();
    await until(data, (d) => d.isolated === "reference_track", "Reference track was not isolated");
    await frame.getByRole("combobox", {name: "视窗视角"}).selectOption("底视图");
    await until(data, (d) => d.view === "底视图" && d.isolated === "" &&
      Number(d.visibleComponents) === Number(initial.visibleComponents) - 1,
    "Changing to an underside view should release isolation and hide the reference track");
    await frame.getByRole("combobox", {name: "视窗视角"}).selectOption("整体视图");
    await until(data, (d) => d.view === "整体视图" && d.visibleComponents === initial.visibleComponents,
      "Return to the main view did not restore the reference track");
    await search.press("Escape");
    await frame.getByRole("complementary", {name: "零件清单"}).waitFor({state: "hidden"});

    await page.getByText("视角与部件", {exact: true}).click();
    for (const view of ["侧视图", "正视图", "俯视图", "底视图", "整体视图"]) {
      await frame.getByRole("combobox", {name: "视窗视角"}).selectOption(view);
      await until(data, (d) => d.view === view, "Camera preset did not update");
    }
    await option(page, "bogie_inspection_preset", "传动与制动");
    await until(data, (d) => d.parts === "3" && d.view === "底视图", "Part filter did not hide frame");
    await option(page, "bogie_focus", "电机与传动");
    // A rerender after the preset can replace the input during its first edit.
    // Retry the interaction if the actual viewer did not receive the selection.
    try {
      await until(data, (d) => d.selected === "电机与传动", "Waiting for focus after preset", 15);
    } catch {
      await option(page, "bogie_focus", "电机与传动", {force: true});
      await until(data, (d) => d.selected === "电机与传动", "Manual focus did not update");
    }
    await page.getByText("显示部件标注", {exact: true}).click();
    await frame.locator(".tag").filter({hasText: "电机与传动"}).waitFor();
    await page.getByRole("heading", {name: "三维诊断工作台"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-detail");
    await option(page, "bogie_inspection_preset", "完整转向架");
    if ((await data()).parts !== "7") {
      await page.getByRole("button", {name: "恢复所选预设", exact: true}).click();
    }
    await until(data, (d) => d.parts === "7" && d.view === "整体视图", "Full assembly preset did not restore");
    await page.getByText("分解示意", {exact: true}).click();
    await until(data, (d) => Number(d.spread) > 0, "Exploded view did not activate");
    await page.getByRole("heading", {name: "三维诊断工作台"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-exploded");
    await page.getByText("分解示意", {exact: true}).click();
    await until(data, (d) => Number(d.spread) === 0, "Reassembly did not finish");
    await page.getByText("显示部件标注", {exact: true}).click();
    await page.getByText("视角与部件", {exact: true}).click();

    const transport = key => page.locator(".st-key-bogie_" + key + " button:visible:not([data-stale=true] button)");
    const play = transport("play"), pause = transport("pause"), reset = transport("reset");
    for (const [scenario, mode] of [
      ["正常运行", "正常运行"], ["横向振动", "横向振动"], ["垂向振动", "垂向振动"],
      ["蛇行运动示意", "蛇行运动示意"], ["一系悬挂异常示意", "垂向振动"],
      ["轴承异常示意", "正常运行"], ["异常运行", "异常运行"],
    ]) {
      await option(page, "bogie_teaching", scenario);
      await until(data, (d) => d.mode === mode, "Scene did not update");
      await play.click();
      const running = await until(data, (d) => d.playing === "true" && Math.abs(Number(d.phase)) > .02,
        "Illustration did not play");
      assert.equal(await measure.isDisabled(), true, "Measurement was available while the model was playing");
      assert.equal(running.axleAngle, running.discAngle, "Brake disc should spin with axle");
      assert.equal(Number(running.axleboxAngle), 0, "Axlebox should not spin with the axle");
      if (mode !== "正常运行") await until(data, (d) =>
        d.pose.split(",").some((v) => Math.abs(Number(v)) > .01), "Model did not move in the chosen scene");
      if (scenario === "正常运行") {
        if (running.renderer === "software") {
          assert(Number(running.calls) < Number(initial.calls) / 2, "Software playback did not batch the model");
          assert.equal(running.triangles, initial.triangles, "Software playback dropped geometry");
          const playingBox = await canvas.boundingBox();
          for (const [x, y] of [[.28,.57],[.37,.57],[.41,.52],[.55,.45],[.61,.60]]) {
            await canvas.click({position: {x: playingBox.width * x, y: playingBox.height * y}});
            if ((await data()).selected) break;
          }
          const picked = await data();
          assert(picked.selected && picked.playing === "true", "Part could not be picked during batched playback");
          await frame.getByRole("button", {name: "隐藏零件"}).click();
          await until(data, (d) => d.playing === "true" && d.hidden === "1" &&
            Number(d.visibleComponents) === Number(initial.visibleComponents) - 1,
          "Batch did not reflect a hidden part during playback");
          await frame.getByRole("button", {name: "恢复隐藏"}).click();
          await until(data, (d) => d.playing === "true" && d.hidden === "0" &&
            d.visibleComponents === initial.visibleComponents, "Batch did not restore the hidden part");
        }
        const beforeSample = await data();
        const started = Date.now();
        await new Promise((resolve) => setTimeout(resolve, 3200));
        const later = await data();
        const fps = (Number(later.frames) - Number(beforeSample.frames)) * 1000 / (Date.now() - started);
        assert(fps > 5, "Browser stopped drawing during continuous playback");
        console.log("Normal playback", JSON.stringify({width, height, fps: Math.round(fps),
          triangles: Number(later.triangles), drawCalls: Number(later.calls), renderer: later.renderer}));
      }
      await pause.click();
      const paused = await until(data, (d) => d.playing === "false", "Pause did not reach renderer");
      await new Promise((resolve) => setTimeout(resolve, 230));
      assert.equal((await data()).phase, paused.phase);
      await until(() => reset.count(), n => n === 1, "Transport did not finish rerendering");
      await reset.click();
      await until(data, (d) => Number(d.phase) === 0, "Reset did not return to initial pose");
    }
    await checkTeachingControls(page, data, play, pause, reset);

    await page.getByText("视角与部件", {exact: true}).click();
    await option(page, "bogie_renderer", "Plotly 兼容模式");
    await page.locator(".gl-container canvas").first().waitFor({state: "attached"});
    await option(page, "bogie_renderer", "Three.js 工作台");
    await frame.locator("#workbench[data-status=ready][data-frames]").waitFor();
    await page.getByText("视角与部件", {exact: true}).click();
    await openReplayEvent(page, data);
    await checkRelatedReview(page, frame, canvas, data, width, true);
    await page.getByTestId("stPlotlyChart").waitFor();
    await page.getByRole("heading", {name: "三维诊断工作台"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-replay");
    await checkReplayInspectionExport(page, frame, canvas, data);
    await button(page, "查看关联构件").click();
    await until(data, (d) => d.relatedReview === "true", "Could not start paused related review");
    await play.click();
    await until(data, (d) => d.playing === "true" && d.relatedReview === "false",
      "Starting replay did not close related review");
    assert.equal(await button(page, "返回原检查视图").count(), 0);
    const inspectWhilePlaying = button(page, "查看关联构件");
    if (await inspectWhilePlaying.count()) assert.equal(await inspectWhilePlaying.isDisabled(), true);
    await pause.click();
    await page.getByRole("button", {name: /核查虚构证据/}).click();
    await page.getByText(/本演示未生成 AI 解读/).waitFor();
    await page.getByRole("button", {name: "在三维回放中查看虚构区段", exact: true}).click();
    await page.getByText("已定位到虚构演示的异常样本。").waitFor();
    await page.getByText("独立教学动画", {exact: true}).click();
    const teaching = await downloadInspection(page, frame, canvas);
    assert(!teaching.painted.includes("观测"), "The replay observation leaked into a subsequent teaching PNG");
    assert(!teaching.painted.join(" ").includes("虚构批次 1"), "The teaching PNG retained stale batch context");
    assert.equal(await page.getByTestId("stException").count(), 0);
    assert.deepEqual(errors, []);
    console.log("Bogie section, interaction, all seven scenes, Plotly fallback and offline evidence round trip passed");
  } catch (error) {
    await screenshot(page, "bogie-webgl-error").catch(() => {});
    throw error;
  } finally {
    await page.close();
  }
}

async function checkFallback(browser, response, checkDampers = false) {
  const page = await browser.newPage({viewport: {width: 1366, height: 768}});
  page.setDefaultTimeout(20000);
  try {
    await page.route("**/bogie.glb", (route) => route.fulfill(response));
    const address = new URL(url);
    address.searchParams.set("rail_page", "三维转向架仿真");
    await page.goto(address.href);
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const root = frame.locator("#workbench[data-status=ready][data-model=procedural][data-frames]");
    await root.waitFor();
    if (checkDampers) await checkDamperGeometry(root, "procedural");
    assert.equal(await root.getAttribute("data-parts"), "7");
    assert.equal(await frame.locator("#browse-parts").isVisible(), false);
    const section = frame.getByRole("button", {name: "剖切视图", exact: true});
    await section.click();
    await until(() => root.getAttribute("data-section-axis"), (value) => value === "x",
      "Procedural fallback could not activate the section plane");
    await section.click();
    await until(() => root.getAttribute("data-section-axis"), (value) => value === "",
      "Procedural fallback could not disable the section plane");
    await page.locator(".st-key-bogie_play button").click();
    await frame.locator('#workbench[data-playing="true"]').waitFor();
    await until(() => root.getAttribute("data-phase"), (phase) => Math.abs(Number(phase)) > .02,
      "Procedural fallback did not animate");
    assert.equal(await page.getByTestId("stException").count(), 0);
  } finally {
    await page.close();
  }
}

async function checkLegacyModel(browser, path) {
  const page = await browser.newPage({viewport: {width: 1366, height: 768}});
  page.setDefaultTimeout(20000);
  try {
    const binary = await readFile(path);
    await page.route("**/bogie.glb", (route) => route.fulfill({contentType: "model/gltf-binary", body: binary}));
    const address = new URL(url);
    address.searchParams.set("rail_page", "三维转向架仿真");
    await page.goto(address.href);
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const root = frame.locator('#workbench[data-status=ready][data-model=glb][data-components="54"][data-frames]');
    await root.waitFor();
    await checkDamperGeometry(root, "legacy");
    console.log("Legacy GLB loaded and retained compatible damper motion");
  } finally {
    await page.close();
  }
}

async function checkBookmarks(browser) {
  const page = await browser.newPage({viewport: {width: 1536, height: 950}});
  page.setDefaultTimeout(20000);
  try {
    const address = new URL(url);
    address.searchParams.set("rail_page", "三维转向架仿真");
    await page.goto(address.href);
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const root = frame.locator("#workbench[data-status=ready][data-frames]");
    const data = () => root.evaluate((node) => ({...node.dataset}));
    const section = frame.getByRole("button", {name: "剖切视图", exact: true});
    const sectionAxis = frame.getByLabel("剖切轴", {exact: true});
    const toggle = frame.getByRole("button", {name: "检查视图", exact: true});
    const name = frame.getByLabel("视图名称", {exact: true});
    const saved = frame.getByLabel("已保存视图", {exact: true});
    const save = frame.getByRole("button", {name: "保存当前视图", exact: true});
    const restore = frame.getByRole("button", {name: "恢复视图", exact: true});
    const remove = frame.getByRole("button", {name: "删除视图", exact: true});
    const exportViews = frame.getByRole("button", {name: "导出书签", exact: true});
    const importViews = frame.getByRole("button", {name: "导入书签", exact: true});
    const status = () => frame.locator("#bookmark-status").textContent();
    const canvas = frame.locator("canvas");
    const view = frame.getByRole("combobox", {name: "视窗视角"});
    const bookmark = "轮对纵剖检查";
    const savedOption = saved.locator("option").filter({hasText: bookmark});

    await root.waitFor();
    const parts = frame.getByRole("button", {name: "零件清单", exact: true});
    const search = frame.getByRole("searchbox", {name: "查找零件"});
    await parts.click();
    await search.fill("disc_a_l");
    await frame.locator('[data-part="disc_a_l"]').click();
    await frame.getByRole("button", {name: "隐藏零件", exact: true}).click();
    await search.press("Escape");
    await view.selectOption("侧视图");
    await section.click();
    await toggle.click();
    const originalZoom = Number((await data()).zoom);
    await canvas.hover();
    await page.mouse.wheel(0, -240);
    await until(data, (d) => Number(d.zoom) > originalZoom, "The custom bookmark camera did not zoom");
    await name.fill(bookmark);
    const current = await data();
    assert.equal(current.sectionAxis, "x");
    assert.equal(Number(current.sectionPosition), 0);
    await save.click();
    await savedOption.waitFor({state: "attached"});

    const [download] = await Promise.all([page.waitForEvent("download"), exportViews.click()]);
    assert.equal(download.suggestedFilename(), "railvehicle-bogie-bookmarks.json");
    const backup = await readFile(await download.path());
    const envelope = JSON.parse(backup.toString("utf8"));
    assert.equal(envelope.format, "railvehicle-bogie-bookmarks");
    assert.equal(envelope.version, 1);
    assert.equal(envelope.entries.length, 1);
    assert.equal(envelope.entries[0].name, bookmark);
    const snapshot = envelope.entries[0].snapshot;
    assert.equal(snapshot.view, "侧视图");
    assert.deepEqual(snapshot.hidden, ["disc_a_l"]);
    assert.equal(snapshot.section.axis, "x");

    await frame.getByRole("button", {name: "恢复隐藏", exact: true}).click();
    await view.selectOption("俯视图");
    await sectionAxis.selectOption("z");
    await until(data, (d) => d.view === "俯视图" && d.sectionAxis === "z",
      "The active view did not move away from the saved view");
    await saved.selectOption({label: bookmark});
    await restore.click();
    await until(data, (d) => d.view === "侧视图" && d.sectionAxis === "x" && d.hidden === "1" &&
      Number(d.sectionPosition) === 0 && Math.abs(Number(d.zoom) - Number(current.zoom)) < .02,
      "The bookmark did not restore its camera preset and clipping plane");
    await screenshot(page, "bogie-glb-bookmark");

    await page.reload();
    await root.waitFor();
    await toggle.click();
    await savedOption.waitFor({state: "attached"});
    await saved.selectOption({label: bookmark});
    const beforeRestore = await canvas.screenshot();
    const initialFrame = Number((await data()).frames);
    await restore.click();
    await until(data, (d) => Number(d.frames) > initialFrame && d.view === "侧视图" &&
      d.sectionAxis === "x" && Number(d.sectionPosition) === 0 && d.hidden === "1" && d.selected === "disc_a_l" &&
      Math.abs(Number(d.zoom) - Number(current.zoom)) < .02,
      "The persisted bookmark did not restore a newly loaded workbench");
    assert.notDeepEqual(await canvas.screenshot(), beforeRestore, "Restoring the bookmark did not change the canvas");
    await remove.click();
    await savedOption.waitFor({state: "detached"});
    await page.reload();
    await root.waitFor();
    await toggle.click();
    assert.equal(await savedOption.count(), 0, "The deleted bookmark reappeared after reload");

    const [chooser] = await Promise.all([page.waitForEvent("filechooser"), importViews.click()]);
    await chooser.setFiles({name: download.suggestedFilename(), mimeType: "application/json", buffer: backup});
    await savedOption.waitFor({state: "attached"});
    await saved.selectOption({label: bookmark});
    await restore.click();
    await until(data, (d) => d.view === "侧视图" && d.sectionAxis === "x" &&
      d.hidden === "1" && d.selected === "disc_a_l" && Math.abs(Number(d.zoom) - Number(current.zoom)) < .02,
    "The imported bookmark did not restore the camera, section and hidden part");

    const file = frame.locator("#bookmark-file");
    snapshot.view = "俯视图";
    await file.setInputFiles({name: "same-name.json", mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(envelope))});
    await until(status, (value) => /同名|重复/.test(value), "The duplicate import was not reported");
    assert.equal(await savedOption.count(), 1);

    await file.setInputFiles({name: "invalid.json", mimeType: "application/json", buffer: Buffer.from("{")});
    await until(status, (value) => /格式|JSON|无效|无法/.test(value), "The malformed import was not reported");
    assert.equal(await savedOption.count(), 1);
    await view.selectOption("俯视图");
    await saved.selectOption({label: bookmark});
    await restore.click();
    await until(data, (d) => d.view === "侧视图" && d.sectionAxis === "x",
      "The duplicate or malformed import overwrote the existing bookmark");

    assert.equal(await page.getByTestId("stException").count(), 0);
    console.log("Inspection bookmarks passed save, restore, reload persistence, deletion, export and import");
  } finally {
    await page.close();
  }
}

(async () => {
  if (output) await mkdir(output, {recursive: true});
  const browser = await chromium.launch({executablePath, args});
  try {
    await audit(browser, 1920, 1080);
    await audit(browser, 1366, 768, false);
    await checkBookmarks(browser);
    await checkFallback(browser, {status: 404, body: "Missing"}, true);
    await checkFallback(browser, {status: 200, contentType: "model/gltf-binary", body: "Corrupt GLB"});
    console.log("Rigid damper joints, lengths, software batches, and one-part inspection passed");
    if (legacyModel) await checkLegacyModel(browser, legacyModel);
    console.log("Procedural fallback passed for missing and corrupt GLB");
    console.log("Desktop layout passed at 1920x1080 and 1366x768");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
