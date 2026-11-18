// Run against a local instance seeded with two fictional batches, with AI disabled.
// node scripts/check_frontend_product_browser.cjs http://127.0.0.1:8572 output-directory
const assert = require("node:assert/strict");
const { mkdir, readFile } = require("node:fs/promises");
const { resolve } = require("node:path");
const { chromium } = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const address = new URL(process.argv[2]);
assert.equal(address.origin, `http://127.0.0.1:${address.port}`);
address.searchParams.set("embed_options", "dark_theme");
const output = resolve(process.argv[3]);
const heading = (page, name) => page.getByRole("heading", { name, exact: true });
const sidebar = (page) => page.getByTestId("stSidebar");
const button = (page, key) => page.locator(`[class~="st-key-${key}"] button`);
const nav = (page, name) => sidebar(page).getByRole("button", { name: new RegExp(`${name}$`) });

async function audit(browser, width, height) {
  const page = await browser.newPage({ viewport: { width, height }, acceptDownloads: true });
  page.setDefaultTimeout(20000);
  await page.route("**/*", (route) => new URL(route.request().url()).origin === address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const errors = [];
  const captures = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const main = page.getByTestId("stMain");
  async function save(name, resetScroll = true) {
    await page.getByRole("button", { name: "Stop", exact: true }).waitFor({ state: "hidden" });
    assert.equal(await page.getByTestId("stException").count(), 0, name);
    assert(await main.evaluate((node) => node.scrollWidth <= node.clientWidth + 1), `${name}: horizontal overflow`);
    if (resetScroll) await main.evaluate((node) => node.scrollTo(0, 0));
    await page.screenshot({ path: resolve(output, `${width}-${name}.png`), animations: "disabled" });
    captures.push(name);
  }
  async function open(label, title) {
    await nav(page, label).click();
    await heading(page, title).waitFor();
    await nav(page, label).and(page.getByTestId("stBaseButton-primary")).waitFor();
  }
  const expectThreshold = () => page.waitForFunction(
    () => Number(document.querySelector(".st-key-analysis_threshold input")?.value) === 1.1);
  try {
    await page.goto(address.href);
    await heading(page, "工程工作台").waitFor();
    for (const label of ["工作台", "车辆管理", "数据管理", "运行分析", "异常检测", "批次对比", "AI 智能分析", "三维可视化", "分析报告"])
      await nav(page, label).waitFor();
    await page.getByText("DEMO-001", { exact: false }).first().waitFor();
    await page.getByTestId("stMetricValue").first().waitFor();
    await page.getByTestId("stPlotlyChart").first().locator(".main-svg").first().waitFor();
    await save("01-home");

    await open("车辆管理", "车辆参数");
    await button(page, "vehicle_edit_open").waitFor();
    await save("02-vehicles");
    await open("数据管理", "CSV 数据导入");
    await page.getByTestId("stFileUploader").waitFor();
    await save("03-data");
    await open("运行分析", "运行分析");
    await page.getByTestId("stPlotlyChart").first().waitFor();
    const threshold = page.locator(".st-key-analysis_threshold input");
    await threshold.fill("1.1");
    await threshold.press("Enter");
    await expectThreshold();
    await save("04-analysis");
    await open("异常检测", "异常检测");
    await page.locator(".st-key-anomaly_review_event").waitFor();
    await expectThreshold();
    await save("05-anomalies");
    await button(page, "run_analysis_bogie_jump").click();
    await heading(page, "三维诊断工作台").waitFor();
    await page.getByText("已定位到所选统计证据的异常样本。", { exact: true }).waitFor();
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const viewer = frame.locator('#workbench[data-status="ready"][data-model="glb"][data-components="54"]');
    await viewer.waitFor({ timeout: 30000 });
    const canvas = frame.locator("canvas");
    const box = await canvas.boundingBox();
    assert(box && box.width >= 420 && box.height >= 320, "GLB canvas too small");
    await save("06-bogie");
    await viewer.scrollIntoViewIfNeeded();
    await save("07-bogie-detail", false);
    await button(page, "bogie_play").click();
    await page.waitForFunction(() => document.querySelector(".st-key-bogie_pause button")?.disabled === false);
    await button(page, "bogie_pause").click();
    await page.waitForFunction(() => document.querySelector(".st-key-bogie_pause button")?.disabled);
    await button(page, "bogie_reset").click();
    await button(page, "bogie_return_analysis").click();
    await heading(page, "异常检测").waitFor();
    await expectThreshold();

    await open("AI 智能分析", "AI 分析中心");
    await page.getByText("核查证据", { exact: true }).waitFor();
    assert(await button(page, "ai_center_generate").isDisabled(), "AI must be disabled on the fixture server");
    await save("08-ai");
    await page.getByRole("button", { name: "返回分析页面", exact: true }).click();
    await heading(page, "异常检测").waitFor();
    await open("分析报告", "分析报告");
    await expectThreshold();
    await button(page, "generate_analysis_report").click();
    const [pdf] = await Promise.all([page.waitForEvent("download"),
      page.getByRole("button", { name: "下载 PDF 报告", exact: true }).click()]);
    assert.equal((await readFile(await pdf.path())).subarray(0, 4).toString(), "%PDF");
    await save("09-report");
    await open("批次对比", "批次对比");
    await page.locator(".st-key-comparison_vehicle_id").waitFor();
    await save("10-comparison");
    await open("运行分析", "运行分析");
    await expectThreshold();
    await page.getByTestId("stSidebarCollapseButton").locator("button").click();
    await page.getByTestId("stExpandSidebarButton").waitFor();
    assert.equal(await sidebar(page).getAttribute("aria-expanded"), "false");
    await page.getByTestId("stExpandSidebarButton").click();
    await nav(page, "运行分析").waitFor();
    assert.equal(await sidebar(page).getAttribute("aria-expanded"), "true");
    await expectThreshold();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ width, height, screenshots: captures.length, glbParts: 54, pdf: true }));
  } catch (error) {
    await page.screenshot({ path: resolve(output, `${width}-failure.png`) });
    throw error;
  } finally {
    await page.close();
  }
}

async function main() {
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.RAIL_BROWSER_PATH, headless: true,
    args: process.env.RAIL_SOFTWARE_WEBGL ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : [] });
  try {
    const results = await Promise.allSettled([audit(browser, 1366, 768), audit(browser, 1920, 1080)]);
    for (const result of results) if (result.status === "rejected") console.error(result.reason);
    if (results.some(({ status }) => status === "rejected")) process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
