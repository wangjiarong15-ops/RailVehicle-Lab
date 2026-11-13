// Run against an already running offline demo server:
//   node scripts/check_bogie_browser.cjs http://127.0.0.1:8501 screenshots
const assert = require("node:assert/strict");
const {mkdir} = require("node:fs/promises");
const {join} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const url = new URL(process.argv[2] || "http://127.0.0.1:8501");
assert(["127.0.0.1", "localhost"].includes(url.hostname), "Use a local demo server");
url.searchParams.set("embed_options", "dark_theme");
const output = process.argv[3];
const executablePath = process.env.RAIL_BROWSER_PATH;
const args = process.env.RAIL_SOFTWARE_WEBGL ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : [];
const button = (page, name) => page.getByRole("button", {name, exact: true});
const option = async (page, key, name) => {
  const select = page.locator(".st-key-" + key).getByTestId("stSelectbox");
  const input = select.locator("input");
  if (await input.inputValue() === name) return;
  const choice = page.locator("[role=option]").filter({hasText: name});
  const trigger = select.getByRole("button", {name: "Open", exact: true});
  await input.click();
  if (await trigger.count()) {
    await input.fill(name);
    await input.press("ArrowDown");
    await input.press("Enter");
    return;
  }
  await choice.click();
};
async function until(read, check, reason) {
  for (let i = 0; i < 60; i++) {
    const value = await read();
    if (check(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error(reason);
}
async function screenshot(page, name) {
  if (output) await page.screenshot({path: join(output, name.replace("webgl", "glb") + ".png")});
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
    await page.getByRole("heading", {name: "三维转向架仿真"}).waitFor();
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
    if (!complete) return;
    await page.setViewportSize({width: 1366, height: 768});
    await until(() => canvas.boundingBox(), (resized) => resized.height < box.height - 100, "Viewport did not resize");
    await page.setViewportSize({width, height});
    await until(() => canvas.boundingBox(), (resized) => Math.abs(resized.height - box.height) < 2, "Viewport did not grow again");

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
    await until(data, (d) => d.selected === "电机与传动", "Manual focus did not update");
    await page.getByText("显示部件标注", {exact: true}).click();
    await frame.locator(".tag").filter({hasText: "电机与传动"}).waitFor();
    await page.getByRole("heading", {name: "三维转向架仿真"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-detail");
    await option(page, "bogie_inspection_preset", "完整转向架");
    if ((await data()).parts !== "7") {
      await page.getByRole("button", {name: "恢复所选预设", exact: true}).click();
    }
    await until(data, (d) => d.parts === "7" && d.view === "整体视图", "Full assembly preset did not restore");
    await page.getByText("分解示意", {exact: true}).click();
    await until(data, (d) => Number(d.spread) > 0, "Exploded view did not activate");
    await page.getByRole("heading", {name: "三维转向架仿真"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-exploded");
    await page.getByText("分解示意", {exact: true}).click();
    await until(data, (d) => Number(d.spread) === 0, "Reassembly did not finish");
    await page.getByText("显示部件标注", {exact: true}).click();
    await page.getByText("视角与部件", {exact: true}).click();

    const play = page.locator(".st-key-bogie_play button"), pause = page.locator(".st-key-bogie_pause button");
    const reset = page.locator(".st-key-bogie_reset button");
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
      await reset.click();
      await until(data, (d) => Number(d.phase) === 0, "Reset did not return to initial pose");
    }

    await option(page, "bogie_renderer", "Plotly 兼容模式");
    await page.locator(".gl-container canvas").first().waitFor({state: "attached"});
    await option(page, "bogie_renderer", "Three.js 工作台");
    await frame.locator("#workbench[data-status=ready][data-frames]").waitFor();
    await page.getByText("内置虚构演示", {exact: true}).click();
    await page.getByText(/定位超阈值区段（/).click();
    await button(page, "定位区段起点").click();
    await until(data, (d) => d.highlighted.length > 0, "Replay did not highlight event");
    await page.getByText(/第 31\/120 条/).waitFor();
    await button(page, "下一帧").click();
    await page.getByText(/第 32\/120 条/).waitFor();
    await page.getByTestId("stPlotlyChart").waitFor();
    await page.getByRole("heading", {name: "三维转向架仿真"}).scrollIntoViewIfNeeded();
    await screenshot(page, "bogie-webgl-replay");
    await page.getByRole("button", {name: /核查虚构证据/}).click();
    await page.getByText(/本演示未生成 AI 解读/).waitFor();
    await page.getByRole("button", {name: "在三维回放中查看虚构区段", exact: true}).click();
    await page.getByText("已定位到虚构演示的异常样本。").waitFor();
    assert.equal(await page.getByTestId("stException").count(), 0);
    assert.deepEqual(errors, []);
    console.log("Bogie interaction, all seven scenes, Plotly fallback and offline evidence round trip passed");
  } catch (error) {
    await screenshot(page, "bogie-webgl-error").catch(() => {});
    throw error;
  } finally {
    await page.close();
  }
}

async function checkFallback(browser, response) {
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
    assert.equal(await root.getAttribute("data-parts"), "7");
    assert.equal(await frame.locator("#browse-parts").isVisible(), false);
    await page.locator(".st-key-bogie_play button").click();
    await frame.locator('#workbench[data-playing="true"]').waitFor();
    await until(() => root.getAttribute("data-phase"), (phase) => Math.abs(Number(phase)) > .02,
      "Procedural fallback did not animate");
    assert.equal(await page.getByTestId("stException").count(), 0);
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
    await checkFallback(browser, {status: 404, body: "Missing"});
    await checkFallback(browser, {status: 200, contentType: "model/gltf-binary", body: "Corrupt GLB"});
    console.log("Procedural fallback passed for missing and corrupt GLB");
    console.log("Desktop layout passed at 1920x1080 and 1366x768");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
