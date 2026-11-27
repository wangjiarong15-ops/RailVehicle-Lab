const assert=require("node:assert/strict");
const {mkdir,readFile}=require("node:fs/promises");
const {resolve}=require("node:path");
const {chromium}=require(process.env.RAIL_PLAYWRIGHT_MODULE||"playwright");

const address=new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
const output=resolve(process.argv[3]);
const button=(page,name)=>page.getByRole("button",{name,exact:true});

async function download(page,name) {
  const ready=page.waitForEvent("download");
  await button(page,name).click();
  const artifact=await ready;
  const content=await readFile(await artifact.path());
  await artifact.saveAs(resolve(output,artifact.suggestedFilename()));
  return content;
}

function pageUrl(route,theme) {
  const url=new URL(address);
  url.searchParams.set("rail_page",route);
  url.searchParams.set("embed_options",theme+"_theme");
  return url.href;
}

async function restoreRecording(browser,width,height,theme,raw) {
  const page=await browser.newPage({viewport:{width,height},acceptDownloads:true});
  try {
    await page.route("**/*",route=>new URL(route.request().url()).origin===address.origin ||
      /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
    await page.goto(pageUrl("数字孪生工作台",theme));
    await page.getByTestId("stRadio").getByText("在线采集与状态估计",{exact:true}).click();
    await page.getByText("离线设备记录及登记表",{exact:true}).click();
    await page.locator(".st-key-vi_record input[type=file]").setInputFiles({
      name:"restored-acquisition.jsonl",mimeType:"application/json",buffer:raw,
    });
    await button(page,"读取离线设备记录").click();
    await page.getByText(/6\.38 s · 滤波位移/).waitFor();
    assert.equal(await page.getByTestId("stMetricValue").first().innerText(),"953");
    assert.equal(await page.getByText("虚构真值",{exact:true}).count(),0);
    const rerecorded=await download(page,"导出原始 JSONL");
    assert.deepEqual(rerecorded,raw);
    await page.getByRole("slider",{name:"回放采样点"}).press("Home");
    await page.getByText(/0\.00 s · 滤波位移/).waitFor();
    assert.equal(await page.getByTestId("stException").count(),0);
    await page.getByTestId("stPlotlyChart").first().scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,`${width}-${theme}-recording-restored.png`)});
  } finally {
    await page.close();
  }
}

async function audit(browser,width,height,theme) {
  const page=await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/*",route=>new URL(route.request().url()).origin===address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture=name=>page.screenshot({path:resolve(output,`${width}-${theme}-${name}.png`)});
  try {
    await page.goto(pageUrl("工程验证中心",theme));
    await page.getByRole("heading",{name:"工程验证中心",exact:true}).waitFor();
    await button(page,"计算与核对基准").click();
    await page.getByText("在本次声明的误差门槛内。",{exact:true}).waitFor();
    const reference=JSON.parse((await download(page,"下载基准证据 JSON")).toString());
    assert.equal(reference.source.level,"numerical");
    assert(reference.rmse<.01);
    assert.equal((await download(page,"下载基准报告 PDF")).subarray(0,5).toString(),"%PDF-");
    await page.getByTestId("stPlotlyChart").scrollIntoViewIfNeeded();
    await capture("validation");
    await page.getByTestId("stRadio").getByText("轮轨型面",{exact:true}).click();
    await page.getByText(/候选为踏面分区/).waitFor();
    await button(page,"使用中心拟合锥度复算独立轮对").click();
    await page.getByText(/中心拟合锥度 0.10000/).waitFor();
    assert.equal(JSON.parse((await download(page,"导出几何证据 JSON")).toString()).results.length,61);
    await page.getByTestId("stPlotlyChart").first().scrollIntoViewIfNeeded();
    await capture("profiles");
    if(width===1366) {
      await page.getByTestId("stRadio").getByText("不确定性",{exact:true}).click();
      await button(page,"运行配对抽样").click();
      await page.getByText(/经验 5／50／95 分位数/).waitFor();
      const sweep=JSON.parse((await download(page,"下载不确定性证据 JSON")).toString());
      assert.equal(sweep.draws,12);
      assert.equal(sweep.candidate_failure_rate,0);
      assert.equal(sweep.independent.solver,"Radau");
      if(process.env.RAIL_CONTACT_FIXTURES) {
        await page.getByTestId("stRadio").getByText("外部资料",{exact:true}).click();
        await page.getByRole("heading",{name:"Manchester 公开截面复核",exact:true}).waitFor();
        const uploads=page.getByTestId("stFileUploader");
        await uploads.nth(0).locator("input").setInputFiles(resolve(process.env.RAIL_CONTACT_FIXTURES,"MBench_S1002_v3.prw"));
        await page.getByText("MBench_S1002_v3.prw",{exact:true}).last().waitFor();
        await page.getByTestId("stStatusWidget").waitFor({state:"hidden"});
        await uploads.nth(1).locator("input").setInputFiles(resolve(process.env.RAIL_CONTACT_FIXTURES,"MBench_UIC60_v3.prr"));
        await button(page,"复算 Manchester 刚性首触").click();
        await page.getByText(/刚性首触差/).waitFor();
        const report=JSON.parse((await download(page,"下载公开基准证据 JSON")).toString());
        assert.equal(report.level,"external");
        assert(report.rigid_touch_error_mm<.00005 && report.lateral_difference_mm>1);
        await page.getByRole("heading",{name:"Manchester 公开截面复核",exact:true}).scrollIntoViewIfNeeded();
        await capture("public-benchmark");
      }
      await button(page,"数字孪生").click();
      await page.getByTestId("stRadio").getByText("在线采集与状态估计",{exact:true}).click();
      await button(page,"建立本地模拟采集").click();
      await button(page,"连接或恢复").click();
      await page.getByText("已连接",{exact:true}).waitFor();
      await button(page,"模拟断线").click();
      await page.getByText("连接暂停",{exact:true}).waitFor();
      const progress=page.getByTestId("stMetricValue").first();
      const paused=await progress.innerText();
      await page.waitForTimeout(850);
      assert.equal(await progress.innerText(),paused);
      await button(page,"连接或恢复").click();
      await page.getByText("已结束",{exact:true}).waitFor({timeout:80000});
      assert.equal(await progress.innerText(),"320 / 320");
      await page.getByText(/滤波位移/).waitFor();
      const seek=page.getByRole("slider",{name:"回放采样点"});
      await seek.focus();
      await seek.press("Home");
      await page.getByText(/0.00 s · 滤波位移/).waitFor();
      await seek.press("End");
      await page.getByText(/6.38 s · 滤波位移/).waitFor();
      await page.getByTestId("stPlotlyChart").first().scrollIntoViewIfNeeded();
      await capture("live-state");
      const raw=await download(page,"导出原始 JSONL");
      assert.equal(JSON.parse(raw.toString().split("\n")[0]).source,"simulated");
      assert.equal(raw.toString().trim().split("\n").length,954);
      await button(page,"将当前采集记录交给 Copilot").click();
      await page.getByText(/已绑定当前采集证据/).waitFor();
      const input=page.getByTestId("stChatInputTextArea");
      await input.fill("检查当前数据流异常，核查时钟和测量有效性并比较悬挂退化假设");
      await input.press("Enter");
      await page.getByRole("heading",{name:"监测证据与替代解释",exact:true}).waitFor();
      const diagnosis=JSON.parse((await download(page,"下载监测证据 JSON")).toString());
      assert.equal(diagnosis.quality.missing_sequence,7);
      assert.equal(diagnosis.quality.unsynchronized,0);
      assert.equal(diagnosis.attempts[0].hypothesis,"等效阻尼降低 38%");
      assert.equal(diagnosis.attempts.length,5);
      await page.getByRole("heading",{name:"监测证据与替代解释",exact:true}).scrollIntoViewIfNeeded();
      await capture("copilot-evidence");
      await restoreRecording(browser,width,height,theme,raw);
    }
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(`${width}x${height} ${theme}: evidence, profile geometry and reports passed.`);
  } catch(error) {
    await capture("failure");
    throw error;
  } finally { await page.close(); }
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    await audit(browser,1366,768,"dark");
    await audit(browser,1920,1080,"light");
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
