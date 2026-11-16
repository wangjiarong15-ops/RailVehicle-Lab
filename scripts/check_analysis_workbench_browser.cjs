// Run against a local full-mode test instance with the standard demo CSV
// registered as DEMO-001 in a disposable database. Keep AI disabled.
// node scripts/check_analysis_workbench_browser.cjs http://127.0.0.1:8518 screenshots
const assert = require("node:assert/strict");
const { mkdir, readFile } = require("node:fs/promises");
const { resolve } = require("node:path");
const { chromium } = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const url = new URL(process.argv[2] || "http://127.0.0.1:8518");
assert(["127.0.0.1", "localhost"].includes(url.hostname), "Use only a local test server");
url.searchParams.set("embed_options", "dark_theme");
const output = resolve(process.argv[3] || "screenshots");
const button = (page, name) => page.getByRole("button", { name, exact: true });
const tab = (page, name) => page.getByRole("tab", { name, exact: true });
const widget = (page, key) => page.locator(`.st-key-${key}`);

async function capture(page, name) {
  await page.screenshot({ path: resolve(output, `${name}.png`), animations: "disabled" });
}

async function checkViewport(page) {
  assert(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth + 1),
    "The document exceeds the desktop viewport width");
  assert.equal(await page.getByTestId("stException").count(), 0);
}

async function checkLinkedCameras(page, frames, view) {
  const pose = async (index) => view(index).evaluate(({dataset}) =>
    Object.fromEntries(["camera", "target", "zoom", "cameraLink"].map((key) => [key, dataset[key]])));
  const center = async (index) => {
    const canvas = frames.nth(index).locator("canvas");
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    assert(box, "The comparison canvas is not visible");
    return [box.x + box.width / 2, box.y + box.height / 2];
  };
  async function drag(index, button = "left") {
    const [x, y] = await center(index);
    await page.mouse.move(x, y);
    await page.mouse.down({button});
    await page.mouse.move(x + 58, y + 28, {steps: 7});
    await page.mouse.up({button});
  }
  async function until(predicate, message) {
    const deadline = Date.now() + 6000;
    do {
      const [a, b] = await Promise.all([pose(0), pose(1)]);
      if (predicate(a, b)) return [a, b];
      await new Promise((done) => setTimeout(done, 40));
    } while (Date.now() < deadline);
    assert.fail(message);
  }
  const [initial] = await until((a, b) => a.cameraLink === "linked" && b.cameraLink === "linked" &&
    a.camera && a.camera === b.camera, "The comparison cameras did not connect");
  await drag(0);
  const [rotated] = await until((a, b) => a.camera !== initial.camera && a.camera === b.camera,
    "Rotation from the current sample did not reach the reference camera");
  await drag(1, "right");
  await until((a, b) => a.target !== rotated.target && a.target === b.target,
    "Panning the reference did not reach the current sample camera");
  const [x, y] = await center(1);
  await page.mouse.move(x, y);
  await page.mouse.wheel(0, -180);
  await until((a, b) => Number(a.zoom) > Number(rotated.zoom) && Math.abs(Number(a.zoom) - Number(b.zoom)) < .03,
    "Zooming the reference did not reach the current sample camera");
  await widget(page, "bogie_link_cameras").getByText("联动两侧镜头", {exact: true}).click();
  const [unlinked, alone] = await until((a, b) => a.cameraLink === "off" && b.cameraLink === "off",
    "Both viewers should detach when the user disables linking");
  await drag(0);
  await until((a, b) => a.camera !== unlinked.camera && b.camera === alone.camera,
    "The reference should stay fixed after unlinking");
  await widget(page, "bogie_link_cameras").getByText("联动两侧镜头", {exact: true}).click();
  const [relinked] = await until((a, b) => a.cameraLink === "linked" && b.cameraLink === "linked" && a.camera === b.camera,
    "Re-enabling the link should use the current sample camera");
  const presets = frames.nth(0).getByRole("combobox", {name: "视窗视角"});
  await presets.selectOption("侧视图");
  const [sideView] = await until((a, b) => a.camera !== relinked.camera && a.camera === b.camera,
    "The camera preset did not reach both views");
  await presets.selectOption("整体视图");
  await until((a, b) => a.camera !== sideView.camera && a.camera === b.camera,
    "Returning to the overall preset did not align both views");
}

async function main() {
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.RAIL_BROWSER_PATH,
    headless: true, args: process.env.RAIL_SOFTWARE_WEBGL ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : [] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, acceptDownloads: true });
    await page.route("**/*", (route) => new URL(route.request().url()).origin === url.origin
      ? route.continue() : route.abort());
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url.href);
    await page.getByRole("heading", { name: "分析工作台", exact: true }).waitFor();
    await tab(page, "运行曲线").waitFor();
    await page.getByTestId("stPlotlyChart").first().waitFor();
    await checkViewport(page);
    await capture(page, "01-analysis-workbench");

    await tab(page, "异常事件").click();
    await button(page, "在三维回放中查看此区段").click();
    await page.getByText("已定位到所选统计证据的异常样本。", { exact: true }).waitFor();
    await page.frameLocator('iframe[title*=bogie_viewer]').first()
      .locator('#workbench[data-status=ready][data-model=glb][data-components="54"]').waitFor({ timeout: 30000 });
    await checkViewport(page);
    await widget(page, "bogie_model_card").evaluate((element) => element.scrollIntoView({ block: "center" }));
    await capture(page, "02-event-bogie-glb");
    await widget(page, "bogie_compare").getByText("对照未超阈值样本", { exact: true }).click();
    await page.getByText("同批次未超阈值样本 · 示意姿态", { exact: true }).waitFor();
    const frames = page.frameLocator('iframe[title*=bogie_viewer]');
    const view = (index) => frames.nth(index)
      .locator('#workbench[data-status=ready][data-model=glb]');
    await view(1).waitFor({ timeout: 30000 });
    assert.notEqual(await view(0).getAttribute("data-highlighted"), "");
    assert.equal(await view(1).getAttribute("data-highlighted"), "");
    await page.getByText("未超阈值", { exact: true }).waitFor();
    await widget(page, "bogie_compare_speed").getByRole("combobox").click();
    await page.getByRole("option", {name: "1 km/h", exact: true}).click();
    await page.getByText(/两条样本的速度差：/).waitFor();
    await checkLinkedCameras(page, frames, view);
    await widget(page, "bogie_model_card").evaluate((element) => element.scrollIntoView({ block: "center" }));
    await capture(page, "02b-observed-bogie-comparison");

    await widget(page, "bogie_return_analysis").getByRole("button").click();
    await widget(page, "ai_center_link_single").getByRole("button").click();
    await page.getByRole("heading", { name: "AI 分析中心", exact: true }).waitFor();
    await page.getByText("核查证据", { exact: true }).waitFor();
    assert(await button(page, "生成 AI 辅助解读").isDisabled(), "Use a fixture server with AI disabled");
    const evidence = widget(page, "ai_center_evidence_single");
    const evidenceId = "event.vertical.001";
    const verticalEvidence = page.getByText(/类别：\s*event；轴向：\s*vertical/);
    await evidence.getByRole("combobox").click();
    await page.getByRole("option", { name: evidenceId, exact: true }).click();
    await verticalEvidence.waitFor();
    await capture(page, "03-ai-evidence");

    await widget(page, "ai_center_evidence_jump").getByRole("button").click();
    await page.getByText("已定位到所选统计证据的异常样本。", { exact: true }).waitFor();
    await page.getByText(/第 71\/120 条/).waitFor();
    await widget(page, "bogie_return_evidence").getByRole("button").click();
    await verticalEvidence.waitFor();

    await button(page, "返回分析页面").click();
    await tab(page, "PDF 报告").click();
    await button(page, "生成 PDF 分析报告").click();
    await page.getByText("分析报告已生成，可以下载 PDF 文件。", { exact: true }).waitFor();
    const [download] = await Promise.all([
      page.waitForEvent("download"), button(page, "下载 PDF 报告").click(),
    ]);
    const pdf = await readFile(await download.path());
    assert.equal(pdf.subarray(0, 4).toString("ascii"), "%PDF");
    await capture(page, "04-pdf-report");

    await page.setViewportSize({ width: 1366, height: 768 });
    await tab(page, "运行曲线").click();
    await page.getByTestId("stPlotlyChart").first().waitFor();
    await checkViewport(page);
    await capture(page, "05-workbench-1366");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ browser: "Chromium", viewportWidths: [1600, 1366], glbComponents: 54,
      pdfBytes: pdf.length, screenshotDirectory: output }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
