const assert = require("node:assert/strict");
const {mkdir, readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const address = new URL(process.argv[2]);
assert.equal(address.hostname, "127.0.0.1");
address.searchParams.set("rail_page", "动力学仿真");
const output = resolve(process.argv[3]);

async function download(page, label) {
  const event = page.waitForEvent("download");
  await page.getByRole("button",{name:label,exact:true}).first().click();
  return readFile(await (await event).path());
}

async function audit(browser, width, height, theme) {
  const page = await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors = [];
  page.on("pageerror", error=>errors.push(error.message));
  await page.route("**/*", route => new URL(route.request().url()).origin === address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture = name => page.screenshot({path:resolve(output,`${width}-${theme}-${name}.png`)});
  const openLab = async () => {
    const url = new URL(address);
    if (theme === "dark") url.searchParams.set("embed_options", "dark_theme");
    else url.searchParams.set("embed_options", "light_theme");
    await page.goto(url.href);
    const choice = page.getByRole("combobox",{name:"计算工况",exact:true});
    await choice.click();
    await page.getByRole("option",{name:"整车虚拟试验台（19 自由度）",exact:true}).click();
    await page.getByRole("heading",{name:"整车虚拟试验台",exact:true}).waitFor();
  };
  try {
    await openLab();
    const frame = page.frameLocator('iframe[title*="bogie_viewer"]').first();
    const viewer = frame.locator('#workbench[data-model="glb"][data-status="ready"][data-vehicle-overview="true"]');
    await viewer.waitFor();
    const slider = frame.locator("#sim-time");
    await frame.locator("#lateral-peak").click();
    const peak = +await slider.inputValue();
    assert(peak > 0);
    await viewer.evaluate(node => new Promise((done,fail)=>{
      const deadline = performance.now()+8000;
      function check() {
        const body = node.dataset.vehicleBody?.split(",").map(Number) ?? [];
        const wheels = node.dataset.vehicleWheel?.split(",").map(Number) ?? [];
        if (body.some(v => Math.abs(v)>1e-6) && new Set(wheels).size > 1) return done();
        if (performance.now() > deadline) return fail(Error("整车运动未更新"));
        requestAnimationFrame(check);
      }
      check();
    }));
    await frame.locator("#sim-next").click();
    assert.equal(+await slider.inputValue(),peak+1);
    const cursor = frame.locator("#response-cursor");
    assert(Math.abs(+await cursor.getAttribute("x1") - 1000*(peak+1)/(+await slider.getAttribute("max"))) < 1e-6);
    const stats = await viewer.evaluate(node => `calls=${node.dataset.calls}, triangles=${node.dataset.triangles}`);
    await viewer.scrollIntoViewIfNeeded();
    await capture("whole-vehicle");
    await frame.locator("#vehicle-shell").click();
    assert.equal(await frame.locator("#vehicle-shell").getAttribute("aria-pressed"),"true");
    await capture("transparent-vehicle");
    await frame.locator("#vehicle-overview").click();
    const detailed = frame.locator('#workbench[data-vehicle-overview="false"]');
    await detailed.waitFor();
    await detailed.evaluate(node => new Promise((done,fail)=>{
      const end = performance.now()+8000;
      function check() {
        if (+node.dataset.triangles>20000 && node.dataset.camera && node.dataset.camera!==node.dataset.target) return done();
        if (performance.now()>end) return fail(Error("详细 GLB 相机或绘制未就绪"));
        requestAnimationFrame(check);
      }
      check();
    }));
    await frame.locator("#browse-parts").click();
    await frame.locator("#part-query").fill("coil_a_l");
    await frame.locator('.part-row[data-part="coil_a_l"]').click();
    await frame.locator("#simulation-part-response").filter({hasText:/一系行程/}).waitFor();
    await frame.locator("#section-toggle").click();
    await frame.locator('#workbench[data-section-axis="x"]').waitFor();
    await frame.locator("#surface").scrollIntoViewIfNeeded();
    await capture("detailed-bogie");
    await frame.locator("#vehicle-overview").click();
    assert.equal(+await slider.inputValue(),peak+1);
    await frame.locator("#sim-reset").click();
    assert.equal(+await slider.inputValue(),0);
    await frame.locator("#sim-play").click();
    await viewer.evaluate(node=>new Promise((done,fail)=>{
      const response = node.querySelector("#response"), end=performance.now()+5000;
      function check() {
        if (+response.dataset.sample>3) return done();
        if (performance.now()>end) return fail(Error("播放未推进计算样本"));
        requestAnimationFrame(check);
      }
      check();
    }));
    await frame.getByRole("button",{name:"暂停",exact:true}).click();
    const paused = +await slider.inputValue();
    await viewer.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
    assert.equal(+await slider.inputValue(),paused);

    await page.getByRole("button",{name:"运行参数扫描",exact:true}).click();
    await page.getByText(/网格最优可行工况/).waitFor({timeout:45000});
    const archived = JSON.parse((await download(page,"试验证据 JSON")).toString("utf8"));
    assert.equal(archived.candidates.length,9);
    assert.equal(archived.recommendation.solver.substeps,10);
    assert.equal((await download(page,"试验报告 PDF")).subarray(0,5).toString(),"%PDF-");
    await page.getByRole("button",{name:"保存到试验历史",exact:true}).click();
    await page.getByText(/试验已保存/).waitFor();
    await capture("trial-scan");
    await openLab();
    await page.getByText("历史试验",{exact:true}).click();
    await page.getByRole("button",{name:"从配置复算历史试验",exact:true}).click();
    await page.getByText(/复算证据摘要与历史记录一致/).waitFor();

    await page.getByRole("button",{name:"将当前工况交给 Copilot",exact:true}).click();
    await page.getByText(/整车工况已绑定/).waitFor();
    const chat = page.getByTestId("stChatInputTextArea");
    await chat.fill("比较不同二系刚度和阻尼，侧滚不超过 5 mrad，行程不超过 20 mm，并生成试验报告");
    await chat.press("Enter");
    await page.getByText(/已生成整车教学试验 PDF/).waitFor({timeout:45000});
    const evidence = JSON.parse((await download(page,"下载计算证据与配置版本")).toString("utf8")).coupled_trial;
    assert.equal(evidence.study_id,archived.study_id);
    assert.equal((await download(page,"下载工程分析 PDF")).subarray(0,5).toString(),"%PDF-");
    const child = page.frameLocator('iframe[title*="bogie_viewer"]').first();
    await child.locator('#workbench[data-vehicle-overview="true"]').waitFor();
    assert.equal(+await child.locator("#sim-time").inputValue(),Math.round(evidence.recommendation.metrics.peak_y_time_s*200));
    await child.locator("#workbench").scrollIntoViewIfNeeded();
    await capture("copilot-refined");
    await page.getByRole("button",{name:"在试验台打开复算工况",exact:true}).click();
    await page.getByRole("heading",{name:"整车虚拟试验台",exact:true}).waitFor();
    await page.getByText(/19 自由度线性教学计算 · 积分步长 0.5 ms/).waitFor();
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(`${width}x${height} ${theme}: synchronized overview and GLB, archival replay, two Copilot tools and PDF; ${stats}.`);
  } catch(error) {
    await capture("failure");
    throw error;
  } finally { await page.close(); }
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    await audit(browser, 1366, 768, "dark");
    await audit(browser, 1920, 1080, "light");
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
