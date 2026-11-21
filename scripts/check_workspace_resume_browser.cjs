// Run against a local Streamlit instance seeded with the fictional 120-row samples.
// node scripts/check_workspace_resume_browser.cjs http://127.0.0.1:8588 output-directory
const assert = require("node:assert/strict");
const { mkdir } = require("node:fs/promises");
const { resolve } = require("node:path");
const { chromium } = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
assert.equal(address.origin, `http://127.0.0.1:${address.port}`);
address.searchParams.set("embed_options", "dark_theme");
const output = resolve(process.argv[3]);
const heading = (page, name) => page.getByRole("heading", { name, exact: true });
const button = (page, key) => page.locator(`.st-key-${key} button:visible`);
const nav = (page, label) => page.getByTestId("stSidebar").getByRole("button", { name: new RegExp(`${label}$`) });
const status = page => page.locator(".st-key-bogie_status_card");
const event = page => page.locator(".st-key-anomaly_review_event");
const stayLocal = page => page.route("**/*", route => new URL(route.request().url()).origin === address.origin ||
  /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
const saved = (page, name, index) => page.waitForFunction(([name, index]) => {
  const key = Object.keys(sessionStorage).find(key => key.startsWith("rail_workspace_v1:"));
  if (!key) return false;
  const data = JSON.parse(sessionStorage.getItem(key));
  return data.page === name && (index === null || data.event?.[0] === index) &&
    new URL(location.href).searchParams.get("rail_resume") === "1";
}, [name, index ?? null]);
async function theme(page, label, mode) {
  await nav(page, "设置").click();
  await page.getByRole("link", { name: new RegExp(label) }).click();
  await page.waitForURL(url => url.searchParams.get("embed_options") === `${mode}_theme`);
}

async function audit(browser, width, height) {
  const page = await browser.newPage({ viewport: { width, height }, locale: "en-US", timezoneId: "UTC" });
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  await page.route("**/*", route => new URL(route.request().url()).origin === address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture = async name => {
    assert.equal(await page.getByTestId("stException").count(), 0, name);
    await page.screenshot({ path: resolve(output, `${width}-${name}.png`) });
  };
  try {
    await page.goto(address.href);
    await nav(page, "异常检测").click();
    await heading(page, "异常检测").waitFor();
    await page.getByText("分析时间范围（UTC） · 调整筛选", { exact: true }).click();
    const seconds = page.locator('[class*="st-key-analysis_end_time_"]')
      .getByRole("spinbutton", { name: /second|秒/i });
    await seconds.press("1");
    await seconds.press("5");
    await seconds.press("Tab");
    const threshold = page.locator(".st-key-analysis_threshold input");
    await threshold.fill("1.1");
    await threshold.press("Enter");
    await page.getByText(/分析样本数：.*76 条/).waitFor();
    await page.getByRole("button", { name: "Stop", exact: true }).waitFor({ state: "hidden" });
    await page.getByText("分析时间范围（UTC） · 调整筛选", { exact: true }).click();
    await event(page).evaluate(node => node.scrollIntoView({ block: "center" }));
    const verticalOption = page.getByRole("option", { name: /2\.\s*垂向/ });
    for (let attempt = 0; attempt < 2; attempt++) {
      if (await verticalOption.isVisible()) break;
      await event(page).getByRole("button", { name: "Open", exact: true }).click();
      if (await verticalOption.waitFor({ timeout: 2000 }).then(() => true, () => false)) break;
    }
    await verticalOption.click();
    await saved(page, "异常检测", 1);
    const sameEvent = async () => {
      await heading(page, "异常检测").waitFor();
      await page.getByText("2026-10-09 09:31:10+00:00", { exact: true }).waitFor();
      await page.getByText(/分析样本数：.*76 条/).waitFor();
      assert.equal(Number(await threshold.inputValue()), 1.1);
    };
    await page.reload();
    await sameEvent();
    await saved(page, "异常检测", 1);
    await theme(page, "浅色", "light");
    await sameEvent();
    await saved(page, "异常检测", 1);
    await capture("anomaly-light-restored");

    await nav(page, "AI 证据解读").click();
    await heading(page, "AI 分析中心").waitFor();
    await page.locator('.st-key-ai_center_evidence_single input[value="event.vertical.001"]').waitFor();
    await saved(page, "AI 智能分析中心", 1);
    await page.reload();
    await heading(page, "AI 分析中心").waitFor();
    await page.locator('.st-key-ai_center_evidence_single input[value="event.vertical.001"]').waitFor();
    assert(await button(page, "ai_center_generate").isDisabled());
    await capture("ai-evidence-restored");
    await page.getByRole("button", { name: "返回分析页面", exact: true }).click();
    await sameEvent();

    await button(page, "run_analysis_bogie_jump").click();
    await heading(page, "三维诊断工作台").waitFor();
    await status(page).getByText(/09:31:10.*第 71\/120 条/).waitFor();
    await saved(page, "三维转向架仿真", 1);
    await theme(page, "深色", "dark");
    await heading(page, "三维诊断工作台").waitFor();
    await status(page).getByText(/09:31:10.*第 71\/120 条/).waitFor();
    const viewer = page.frameLocator("iframe[title*=bogie_viewer]").first()
      .locator('#workbench[data-status="ready"][data-model="glb"][data-components="54"]');
    await viewer.waitFor();
    await viewer.scrollIntoViewIfNeeded();
    await capture("bogie-dark-restored");
    await button(page, "bogie_return_analysis").click();
    await sameEvent();
    await nav(page, "分析报告").click();
    await heading(page, "分析报告").waitFor();
    await saved(page, "分析报告", 1);
    await page.reload();
    await heading(page, "分析报告").waitFor();
    await page.getByText(/分析样本数：.*76 条/).waitFor();
    await saved(page, "分析报告", 1);
    await capture("report-restored");
    await nav(page, "异常检测").click();
    await sameEvent();
    await saved(page, "异常检测", 1);

    // Inject after the old document goes away, before Streamlit can read this tab's checkpoint.
    await page.addInitScript(() => {
      if (sessionStorage.getItem("rail_test_corrupt_next_checkpoint") !== "1") return;
      sessionStorage.removeItem("rail_test_corrupt_next_checkpoint");
      const key = Object.keys(sessionStorage).find(key => key.startsWith("rail_workspace_v1:"));
      const data = JSON.parse(sessionStorage.getItem(key));
      data.page = "AI 智能分析中心";
      data.event[1] = "0".repeat(64);
      sessionStorage.setItem(key, JSON.stringify(data));
    });
    await page.evaluate(() => sessionStorage.setItem("rail_test_corrupt_next_checkpoint", "1"));
    await page.reload();
    await heading(page, "异常检测").waitFor();
    await page.getByText(/未继续打开旧证据/).waitFor();
    assert.equal(await heading(page, "AI 分析中心").count(), 0);
    // Copying the URL into a separate browser context cannot restore this tab's selection.
    const fresh = await browser.newPage({ viewport: { width, height } });
    try {
      await stayLocal(fresh);
      await fresh.goto(page.url());
      await heading(fresh, "工程工作台").waitFor();
      assert.equal(await fresh.getByTestId("stException").count(), 0);
    } finally {
      await fresh.close();
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({width, height, restoredSamples: 76, event: "event.vertical.001", row: 71, staleRejected: true, freshTabIsolated: true}));
  } catch (error) {
    await page.screenshot({ path: resolve(output, `${width}-failure.png`) }).catch(() => {});
    throw error;
  } finally {
    await page.close();
  }
}

(async () => {
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.RAIL_BROWSER_PATH, headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-dev-shm-usage"] });
  try {
    await audit(browser, 1366, 768);
    await audit(browser, 1920, 1080);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
