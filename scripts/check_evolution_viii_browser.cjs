const assert = require("node:assert/strict");
const {mkdir,readFile,writeFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
const output = resolve(process.argv[3]);
const button = (page,name) => page.getByRole("button",{name,exact:true});
const heading = (page,name) => page.getByRole("heading",{name,exact:true});
const frame = page => page.frameLocator('iframe[title*="bogie_viewer"]').first();
const route = (page,theme) => {
  const url = new URL(address);
  url.searchParams.set("rail_page",page);
  url.searchParams.set("embed_options",theme+"_theme");
  return url.href;
};

async function local(page) {
  await page.route("**/*",r=>new URL(r.request().url()).origin===address.origin ||
    /^(blob:|data:)/.test(r.request().url())?r.continue():r.abort());
}

async function download(page,name) {
  const ready=page.waitForEvent("download");
  await button(page,name).click();
  const artifact=await ready;
  await artifact.saveAs(resolve(output,artifact.suggestedFilename()));
  return readFile(await artifact.path());
}

async function sampleRender(view,workbench,mode) {
  await view.locator("#render-quality").selectOption(mode);
  await view.locator("#sim-reset").click();
  await view.locator("#sim-play").click();
  const result=await workbench.evaluate(async (node, mode) => {
    const start=performance.now(), first=Number(node.dataset.frames);
    const frames=[];
    for(let i=0;i<4;i++){
      await new Promise(resolve=>setTimeout(resolve,900));
      frames.push({timeMs:performance.now()-start,count:Number(node.dataset.frames)-first,
        dpr:Number(node.dataset.renderDpr),triangles:Number(node.dataset.triangles)});
    }
    return {mode,renderer:node.dataset.renderer,frames,fps:frames.at(-1).count*1000/frames.at(-1).timeMs};
  },mode);
  await view.locator("#sim-reset").click();
  return result;
}
async function runResearch(browser) {
  const page=await browser.newPage({viewport:{width:1366,height:768},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  try {
    await local(page);
    await page.goto(route("AI Copilot","dark"));
    await heading(page,"RailVehicle Copilot").waitFor();
    await button(page,"使用虚构批次体验").click();
    await button(page,"为当前横向异常设计三组对照").waitFor();
    await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    const input=page.locator('[data-testid="stChatInputTextArea"]:visible').last();
    await input.fill("分析当前车辆横向振动异常，自主设计多组对照试验，轮重减载率不超过 40%，二系垂向行程不超过 35 mm，寻找改善候选并独立复算，回放原始方案与优化方案，生成报告。");
    await input.press("Enter");
    await heading(page,"待审批工程任务").waitFor();
    await page.getByText(/event.lateral.001 · 样本 \[31, 33\]/).waitFor();
    await page.getByText(/最多 9 次积分 · 减载率 ≤ 40%/).waitFor();
    await page.screenshot({path:resolve(output,"1366-agent-approval.png")});
    await button(page,"批准并执行工程研究").click();
    await heading(page,"Pareto 与独立复算").waitFor({timeout:210000});
    const report=JSON.parse((await download(page,"下载工程研究证据 JSON")).toString());
    assert.equal(report.evidence.event.event_id,"event.lateral.001");
    assert.equal(report.plan.budget,9);
    assert.equal(report.plan.wheel_unloading,.4);
    assert(report.hypotheses.references.length>0);
    assert.equal(report.tools.filter(row=>row.response_id).length,9);
    assert(report.ranking.pareto.includes(report.ranking.choice));
    assert(report.difference.supported);
    assert.equal(report.checks.verify_choice.metrics.wheel_unloading<.4,true);
    assert(report.speed_probe.within_declared_constraints);
    assert.equal((await download(page,"下载工程研究报告 PDF")).subarray(0,5).toString(),"%PDF-");
    await heading(page,"Pareto 与独立复算").scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,"1366-pareto.png")});
    await page.getByText("同步回放原始方案与候选方案",{exact:true}).click();
    const view=frame(page);
    await view.locator("#twin-panes:not([hidden])").waitFor({timeout:60000});
    const workbench=view.locator('#workbench[data-wheelrail="true"]');
    await workbench.waitFor();
    await view.locator("#wheelrail-forces").click();
    const timeline=view.locator("#sim-time");
    const before=Number(await timeline.inputValue());
    await view.locator("#sim-next").click();
    assert(Number(await timeline.inputValue())>before);
    assert((await workbench.getAttribute("data-wheelrail-other")).length>0);
    await view.locator("#render-quality").selectOption("low");
    await view.locator('#workbench[data-render-quality="low"]').waitFor();
    await view.locator("#surface").scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,"1366-comparison.png")});
    await view.locator("#wheelrail-forces").click();
    await view.locator("#sim-rate").selectOption(".25");
    const rendering=[];
    for(const mode of ["balanced","auto","low"]) rendering.push(await sampleRender(view,workbench,mode));
    assert(rendering.every(row=>row.fps>5 && row.frames.every(frame=>frame.triangles===row.frames[0].triangles)));
    assert.equal(rendering[0].frames.at(-1).triangles,rendering[2].frames.at(-1).triangles);
    await writeFile(resolve(output,"render-performance.json"),JSON.stringify(rendering,null,2));
    assert.deepEqual(errors,[]);
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("Copilot, event evidence, 9 actual solver responses, RK45, constraints, force overlays, twin playback and exports passed");
  } catch(error) {
    await page.screenshot({path:resolve(output,"1366-failure.png")});
    throw error;
  } finally {await page.close();}
}

async function lightReview(browser) {
  const page=await browser.newPage({viewport:{width:1920,height:1080},acceptDownloads:true});
  try {
    await local(page);
    await page.goto(route("工程验证中心","light"));
    await heading(page,"工程验证中心").waitFor();
    await page.getByTestId("stRadio").getByText("物理校准",{exact:true}).click();
    await heading(page,"轮轨有效柔度校准").waitFor();
    await button(page,"运行分组校准与留出检验").click();
    await page.getByText("97.33",{exact:true}).waitFor();
    const report=JSON.parse((await download(page,"下载 CONTACT 校准证据")).toString());
    assert.equal(report.reference.manchester.length,42);
    assert.equal(report.reference.module3.length,2);
    assert.deepEqual(report.fit.errors.held_out.cases,[1,4,7,10]);
    assert(report.fit.errors.held_out.fitted_rmse_relative[1]<report.fit.errors.held_out.initial_rmse_relative[1]);
    await page.screenshot({path:resolve(output,"1920-contact-calibration.png")});
    await button(page,"AI Copilot").click();
    await page.getByText("工程研究任务",{exact:true}).click();
    await button(page,"打开研究归档").click();
    await heading(page,"Pareto 与独立复算").waitFor();
    await page.getByText(/独立 RK45 复算减少量/).waitFor();
    await page.screenshot({path:resolve(output,"1920-resumed-research.png")});
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("CONTACT published-data calibration and fresh Edge archive recovery passed");
  } finally {await page.close();}
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    await runResearch(browser);
    await lightReview(browser);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
