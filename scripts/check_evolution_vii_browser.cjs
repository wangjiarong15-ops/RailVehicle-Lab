const assert=require("node:assert/strict");
const {mkdir,readFile}=require("node:fs/promises");
const {resolve}=require("node:path");
const {chromium}=require(process.env.RAIL_PLAYWRIGHT_MODULE||"playwright");

const address=new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
const output=resolve(process.argv[3]);
const button=(p,name)=>p.getByRole("button",{name,exact:true});
const heading=(p,name)=>p.getByRole("heading",{name,exact:true});
const frame=p=>p.frameLocator('iframe[title*="bogie_viewer"]').first();
const viewport={width:1366,height:768};

async function ask(page,text) {
  await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
  const input=page.locator('[data-testid="stChatInputTextArea"]:visible').last();
  await input.fill(text);
  await input.press("Enter");
}

function url(route,theme="dark") {
  const target=new URL(address);
  target.searchParams.set("rail_page",route);
  target.searchParams.set("embed_options",theme+"_theme");
  return target.href;
}

async function local(page) {
  await page.route("**/*",route=>new URL(route.request().url()).origin===address.origin ||
    /^(blob:|data:)/.test(route.request().url())?route.continue():route.abort());
}

async function download(page,name) {
  const ready=page.waitForEvent("download");
  await button(page,name).click();
  const artifact=await ready;
  await artifact.saveAs(resolve(output,artifact.suggestedFilename()));
  return readFile(await artifact.path());
}

async function runTrial(browser) {
  const page=await browser.newPage({viewport,acceptDownloads:true});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  const screenshot=name=>page.screenshot({path:resolve(output,"1366-"+name+".png")});
  try {
    await local(page);
    await page.goto(url("AI Copilot"));
    await heading(page,"RailVehicle Copilot").waitFor();
    await button(page,"使用虚构批次体验").click();
    await button(page,"为当前横向异常设计三组对照").waitFor();
    await ask(page,"分析当前车辆横向振动异常，判断可能与哪些悬挂参数有关，设计三组对照仿真，并在三维模型中展示相关结构。");
    await heading(page,"工程任务待审批").waitFor();
    await page.getByText(/event.lateral.001.*样本 31–33/).waitFor();
    assert.equal(await heading(page,"参数对照结果").count(),0);
    await ask(page,"将二系垂向行程上限改为 40 mm");
    await page.getByText(/二系垂向行程上限 40 mm · 最多 6 次积分/).waitFor();
    await screenshot("approval");
    await button(page,"批准并运行本地参数试验").click();
    await heading(page,"参数对照结果").waitFor({timeout:170000});
    const report=JSON.parse((await download(page,"下载参数试验证据 JSON")).toString());
    assert.equal(report.solves,6);
    assert.equal(report.request.secondary_travel_mm,40);
    assert.equal(report.request.observation.event_id,"event.lateral.001");
    assert.deepEqual(report.request.observation.index_1based,[31,33]);
    assert.equal(report.request.observation.source,"demo");
    assert.deepEqual(report.cases.map(row=>row.parameter),["primary_k","secondary_k","secondary_c"]);
    assert(report.cases.every(row=>row.metrics&&row.response_id));
    assert.equal(report.comparison.candidate.solver.method,"RK45");
    assert.equal((await download(page,"下载参数试验报告 PDF")).subarray(0,5).toString(),"%PDF-");
    await page.getByText("打开参数对照曲线和三维",{exact:true}).click();
    await page.getByRole("combobox",{name:"选择计算工况"}).click();
    await page.getByText("二系横向阻尼比例 0.75 倍 · DOP853",{exact:true}).click();
    const view=frame(page);
    const focus=view.locator('#workbench[data-wheelrail="true"][data-wheelrail-focus="front_secondary"]');
    await focus.waitFor();
    await view.locator("#wheelrail-shell").click();
    assert.equal(await view.locator("#wheelrail-shell").getAttribute("aria-pressed"),"true");
    await view.locator("#lateral-peak").click();
    const selected=Number(await view.locator("#sim-time").inputValue());
    assert(Math.abs(selected/100-report.cases[2].peak_time_s)<.011);
    await view.locator("#sim-next").click();
    await view.locator("#wheelrail-target").selectOption("front_primary");
    await view.locator('#workbench[data-wheelrail-focus="front_primary"]').waitFor();
    assert((await view.locator("#sim-values").innerText()).includes("前一系左/右"));
    await view.locator("#wheelrail-target").selectOption("front_secondary");
    await view.locator("#surface").scrollIntoViewIfNeeded();
    await screenshot("secondary-3d");
    await page.getByText("定位当前试验的原始异常波形",{exact:true}).click();
    await page.getByText(/样本 31 · 2026-10-08 09:30:30\+00:00/).waitFor();
    await screenshot("bound-observation");
    assert.deepEqual(errors,[]);
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("Copilot: observation digest, approval, six physical solves, independent RK45, three-dimensional localization and exports passed.");
  } catch(error) {
    await screenshot("failure");
    throw error;
  } finally {await page.close();}
}

async function historyAndMatrix(browser) {
  const page=await browser.newPage({viewport:{width:1920,height:1080}});
  try {
    await local(page);
    await page.goto(url("AI Copilot","light"));
    await page.getByText("工程试验与历史",{exact:true}).click();
    await button(page,"打开试验历史").click();
    await heading(page,"参数对照结果").waitFor();
    await page.getByText(/event.lateral.001.*样本 31–33/).waitFor();
    await page.screenshot({path:resolve(output,"1920-history.png")});
    await button(page,"动力学仿真").click();
    await page.getByRole("combobox",{name:"计算工况"}).click();
    await page.getByText("高级轮轨动力学（31 自由度）",{exact:true}).click();
    await page.getByTestId("stRadio").getByText("参数矩阵",{exact:true}).click();
    await heading(page,"31 自由度参数试验").waitFor();
    await button(page,"生成参数试验计划").click();
    await heading(page,"工程任务待审批").waitFor();
    await page.getByText(/最多 5 次积分/).waitFor();
    await page.screenshot({path:resolve(output,"1920-matrix.png")});
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("Parameter matrix and durable report reopened in an independent Edge context.");
  } finally {await page.close();}
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    await runTrial(browser);
    await historyAndMatrix(browser);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
