// Test local Streamlit instances using an already installed Playwright/Edge.
// node scripts/check_product_browser.cjs <demo_app URL> <screenshot folder> [app URL with an empty temporary database]
const assert = require("node:assert/strict");
const {mkdir, readFile} = require("node:fs/promises");
const {join} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const WORKBENCH = "工作台首页";
const BOGIE = "三维诊断工作台";
const AI = "AI 分析中心";
const output = process.argv[3];
const exact = (name) => ({name, exact: true});
const heading = (page, name) => page.getByRole("heading", exact(name));
const sidebar = (page) => page.getByTestId("stSidebarUserContent");
const nav = (page, name) => sidebar(page).getByRole("button", {name: new RegExp(`${name}$`)});

function localUrl(value) {
  const address = new URL(value);
  assert(["127.0.0.1", "localhost"].includes(address.hostname) && address.protocol === "http:" &&
    !address.username && !address.password, "Use a local Streamlit server");
  address.searchParams.set("embed_options", "dark_theme");
  return address;
}

async function open(page, address, legacyRoute) {
  const target = new URL(address);
  if (legacyRoute) target.searchParams.set("rail_page", legacyRoute);
  else target.searchParams.delete("rail_page");
  await page.goto(target.href);
  await nav(page, WORKBENCH).waitFor();
}

async function screenshot(page, name) {
  if (output) await page.screenshot({path: join(output, `railvehicle-product-${name}.png`)});
}

async function fromTop(page) {
  await page.getByTestId("stMain").evaluate((node) => node.scrollTo(0, 0));
}

async function assertNav(page, publicDemo = false) {
  const modules = [WORKBENCH, ...(publicDemo ? ["虚构数据分析"] : ["车辆管理", "数据导入", "运行分析"]),
    "三维转向架", "动力学仿真", "AI Copilot", "分析报告", "异常检测", "联动分析",
    ...(publicDemo ? [] : ["批次对比"]), "振动与频谱", "AI 证据解读"];
  for (const name of modules) await nav(page, name).waitFor();
  const sidebarActions = await sidebar(page).locator('[data-testid="stButton"] button:visible').all();
  assert.equal(sidebarActions.length, modules.length, "Unexpected primary sidebar action");
  for (const [index, name] of modules.entries()) assert.match(await sidebarActions[index].innerText(), new RegExp(name));
}

async function assertPublic(page) {
  assert.equal(await page.getByTestId("stFileUploader").count(), 0, "Public mode exposed a file upload");
  assert.equal(await page.locator(".st-key-vehicle_add").count(), 0, "Public mode exposed vehicle editing");
  assert.equal(await page.locator(".st-key-ai_center_generate").count(), 0, "Public mode exposed paid AI");
  assert.equal(await page.getByTestId("stException").count(), 0);
}

async function assertBogie(page) {
  await heading(page, BOGIE).waitFor();
  const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
  const ready = frame.locator("#workbench[data-status=ready][data-frames][data-model=glb][data-components='54']");
  await ready.waitFor();
  const box = await frame.locator("canvas").boundingBox();
  assert(box && box.width >= 390 && box.height >= 320, "Expected a usable GLB canvas");
}

async function demoAudit(page, address, width) {
  await open(page, address);
  await heading(page, "虚构数据分析").waitFor();
  await assertNav(page, true);
  await assertPublic(page);
  await screenshot(page, `workbench-${width}`);

  await page.getByRole("button", exact("计算虚构运行分析")).click();
  await page.getByTestId("stMetric").first().waitFor();
  await assertPublic(page);
  await screenshot(page, `data-${width}`);

  await page.locator(".st-key-analysis_replay_batch button").click();
  await assertBogie(page);
  await assertPublic(page);
  await fromTop(page);
  await screenshot(page, `diagnosis-${width}`);

  await page.locator(".st-key-bogie_return_analysis button").click();
  await page.locator(".st-key-workbench_demo_evidence button").click();
  await heading(page, AI).waitFor();
  await heading(page, "核查 Python 计算的证据").waitFor();
  await assertPublic(page);
  await screenshot(page, `ai-${width}`);
  await page.getByRole("button", exact("在三维回放中查看虚构区段")).click();
  await assertBogie(page);
  await page.getByText("已定位到虚构演示的异常样本。", {exact: true}).waitFor();

  await nav(page, WORKBENCH).click();
  await heading(page, "演示路线").waitFor();
  for (const name of ["statistics", "bogie", "evidence"]) {
    await page.locator(`.st-key-launch_demo_${name} button`).waitFor();
  }
  await sidebar(page).locator("summary").filter({ hasText: "专项工具" }).click();
  await nav(page, "演示中心").click();
  await heading(page, "演示中心").waitFor();
  await page.locator(".st-key-launch_demo_cockpit button").waitFor();
  await assertPublic(page);
  await screenshot(page, `advanced-${width}`);

  await open(page, address, "CSV 数据导入");
  await heading(page, "虚构数据分析").waitFor();
  await assertPublic(page);
  await open(page, address, "三维转向架仿真");
  await assertBogie(page);
  await open(page, address, "AI 智能分析中心");
  await heading(page, "核查 Python 计算的证据").waitFor();
  await assertPublic(page);
}

async function localAudit(page, address) {
  await open(page, address);
  await heading(page, "工程工作台").waitFor();
  await assertNav(page);
  const readCounts = () => Promise.all(["登记车辆", "导入批次", "采样点总数"].map(async (label) => {
    const metric = page.getByTestId("stMetric").filter({hasText: label});
    return (await metric.getByTestId("stMetricValue").innerText()).trim();
  }));
  assert.deepEqual(await readCounts(), ["0", "0", "0 点"],
    "The local audit writes a synthetic vehicle and CSV. Start app.py with a new RAIL_PORTABLE_HOME first.");
  await screenshot(page, "local-first-run");
  await page.getByRole("button", {name: /打开内置虚构演示/}).click();
  await heading(page, "运行分析").waitFor();
  assert(await page.getByRole("radio", exact("内置虚构演示")).isChecked());
  await page.getByText(/分析样本数.*120 条.*异常区段.*2 个/).waitFor();
  await page.getByText(/演示数据不会写入数据库.*历史导入批次/).waitFor();
  await screenshot(page, "local-fictional-analysis");
  await nav(page, WORKBENCH).click();
  await heading(page, "工程工作台").waitFor();
  assert.deepEqual(await readCounts(), ["0", "0", "0 点"], "Trying the demo must not populate SQLite");
  await page.getByRole("button", exact("登记车辆")).click();
  await heading(page, "车辆参数").waitFor();
  await page.locator(".st-key-vehicle_add button").click();
  const dialog = page.getByRole("dialog", exact("车辆档案"));
  await dialog.getByRole("textbox", exact("车辆编号 *")).fill("PRODUCT-QA-001");
  await dialog.getByRole("textbox", exact("车型 *")).fill("虚构验收车型");
  await dialog.getByRole("button", exact("保存车辆")).click();
  await dialog.waitFor({state: "detached"});
  await page.getByText("车辆档案已保存。", {exact: true}).waitFor();
  await page.locator(".st-key-vehicle_import button").waitFor();
  await screenshot(page, "local-vehicles");

  await page.locator(".st-key-vehicle_import button").click();
  await heading(page, "CSV 数据导入").waitFor();
  await page.getByTestId("stFileUploader").locator('input[type="file"]').setInputFiles({
    name: "fictional-product-qa.csv", mimeType: "text/csv",
    buffer: Buffer.from([
      "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel",
      "2026-10-08T09:30:00Z,PRODUCT-QA-001,75,0.1,0.1",
      "2026-10-08T09:30:01Z,PRODUCT-QA-001,76,1.8,0.2",
      "2026-10-08T09:30:02Z,PRODUCT-QA-001,77,0.2,0.3",
    ].join("\n")),
  });
  await page.getByText("格式检查通过，可以导入。", {exact: true}).waitFor();
  await page.getByRole("button", exact("导入到 SQLite")).click();
  await page.getByText(/导入成功.*共保存 3 行/).waitFor();
  await fromTop(page);
  await screenshot(page, "local-import");
  await page.locator(".st-key-import_anomalies_latest button").click();
  await heading(page, "异常检测").waitFor();
  await page.getByText(/分析样本数.*3 条.*异常区段.*1 个/).waitFor();
  await page.locator(".st-key-run_analysis_bogie_jump button").click();
  await assertBogie(page);
  await page.getByText("已定位到所选统计证据的异常样本。", {exact: true}).waitFor();
  const observation = page.locator(".st-key-bogie_status_card");
  await observation.getByText(/09:30:01.*第 2\/3 条/).waitFor();
  await observation.getByText("1.800", {exact: true}).waitFor();
  await fromTop(page);
  await screenshot(page, "local-event-replay");
  await page.locator(".st-key-bogie_return_analysis button").click();
  await heading(page, "异常检测").waitFor();
  await nav(page, "运行分析").click();
  await heading(page, "运行分析").waitFor();
  await page.getByText(/分析样本数.*3 条.*异常区段.*1 个/).waitFor();
  await page.locator(".st-key-analysis_replay_batch button").waitFor();
  await fromTop(page);
  await screenshot(page, "local-analysis");
  await page.locator(".st-key-analysis_replay_batch button").click();
  await assertBogie(page);
  await page.getByRole("button", {name: /按原分析范围准备报告/}).click();
  await heading(page, "分析报告").waitFor();
  await page.getByText("历史导入 · 车辆 PRODUCT-QA-001 · 批次 #1", {exact: true}).waitFor();
  await page.getByText("报告时间筛选（UTC，包含结束秒内的记录）：2026-10-08 09:30:00 至 2026-10-08 09:30:02",
                       {exact: true}).waitFor();
  await page.getByText("3 条观测 · 1 个规则异常区段", {exact: true}).waitFor();
  await page.locator(".st-key-generate_analysis_report button").click();
  const [pdf] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", exact("下载 PDF 报告")).click(),
  ]);
  assert.equal((await readFile(await pdf.path())).subarray(0, 4).toString(), "%PDF");
  await fromTop(page);
  await screenshot(page, "local-report");

  await page.locator(".st-key-analysis_replay_batch button").click();
  await assertBogie(page);
  const source = page.getByRole("radio", exact("历史运行批次"));
  assert(await source.isChecked(), "The local data handoff should select historical replay");
  await screenshot(page, "local-batch-replay");

  await open(page, address, "CSV 数据导入");
  await page.getByTestId("stFileUploader").waitFor();
  await open(page, address, "AI 智能分析中心");
  await heading(page, AI).waitFor();
  await page.getByText(/当前会话还没有可核查的分析结果/).waitFor();
  await page.locator(".st-key-ai_center_open_analysis button").click();
  await heading(page, "运行分析").waitFor();

  await nav(page, "车辆管理").click();
  await page.locator(".st-key-vehicle_add button").click();
  await dialog.getByRole("textbox", exact("车辆编号 *")).fill("PRODUCT-QA-002");
  await dialog.getByRole("textbox", exact("车型 *")).fill("虚构验收车型二");
  await dialog.getByRole("button", exact("保存车辆")).click();
  await dialog.waitFor({state: "detached"});
  await nav(page, "数据导入").click();
  await page.getByTestId("stFileUploader").locator('input[type="file"]').setInputFiles({
    name: "fictional-product-multi.csv", mimeType: "text/csv",
    buffer: Buffer.from([
      "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel",
      "2026-10-09T09:30:00Z,PRODUCT-QA-001,75,0.1,0.1",
      "2026-10-09T09:30:00Z,PRODUCT-QA-002,76,0.2,2.1",
      "2026-10-09T09:30:01Z,PRODUCT-QA-002,77,0.2,0.3",
    ].join("\n")),
  });
  await page.getByText("格式检查通过，可以导入。", {exact: true}).waitFor();
  await page.getByRole("button", exact("导入到 SQLite")).click();
  await page.getByText(/导入成功：批次 #2，共保存 3 行/).waitFor();
  const receiptVehicle = page.locator(".st-key-import_receipt_vehicle_2").getByRole("combobox");
  await receiptVehicle.click();
  await page.getByRole("option", exact("PRODUCT-QA-002 · 2 条")).click();
  await screenshot(page, "local-import-multi");
  await page.locator(".st-key-import_anomalies_selected button").click();
  await heading(page, "异常检测").waitFor();
  await page.getByText(/分析样本数.*2 条.*异常区段.*1 个/).waitFor();
  assert.equal(await page.getByTestId("stException").count(), 0);
}

async function withPage(browser, address, width, height, audit) {
  const page = await browser.newPage({viewport: {width, height}, screen: {width, height}});
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => new URL(route.request().url()).origin === address.origin ?
    route.continue() : route.abort());
  try {
    await audit(page, address, width);
    assert.deepEqual(errors, [], "The browser reported an unexpected script error");
  } catch (error) {
    await screenshot(page, `failure-${width}`);
    throw error;
  } finally {
    await page.close();
  }
}

(async () => {
  const demo = localUrl(process.argv[2] || "http://127.0.0.1:8501");
  const local = process.argv[4] && localUrl(process.argv[4]);
  if (output) await mkdir(output, {recursive: true});
  const executablePath = process.env.RAIL_BROWSER_PATH;
  const args = process.env.RAIL_SOFTWARE_WEBGL ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : [];
  const browser = await chromium.launch({executablePath, args});
  try {
    for (const [width, height] of [[1920, 1080], [1366, 768]]) {
      await withPage(browser, demo, width, height, demoAudit);
      console.log(`Public demo passed at ${width}x${height}: unified workflow, advanced tools, legacy links, private route blocked, offline evidence and 54-part GLB.`);
    }
    if (local) {
      await withPage(browser, local, 1366, 768, localAudit);
      console.log("Local workspace passed at 1366x768: offline trial leaves SQLite empty; single- and multi-vehicle CSV, linked GLB replay and original-scope PDF.");
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
