const assert = require("node:assert/strict");
const {mkdir,readFile,writeFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
const output = resolve(process.argv[3]);
const button = (page,name) => page.getByRole("button",{name,exact:true});
const heading = (page,name) => page.getByRole("heading",{name,exact:true});
const nav = (page,name) => page.getByTestId("stSidebar").getByRole("button",{name,exact:true});
const native = process.env.RAIL_IX_NATIVE==="1";

async function archive(page) {
  const ready=page.waitForEvent("download");
  await button(page,"导出研究证据 JSON").click();
  const data=await ready;
  return JSON.parse((await readFile(await data.path())).toString());
}

async function casePair(page) {
  await button(page,"数据来源与附件").click();
  await page.getByRole("combobox",{name:"数据来源",exact:true}).click();
  await page.getByText("合成频谱双批次",{exact:true}).click();
  const second=page.getByRole("combobox",{name:"对照批次",exact:true});
  await second.click();
  await page.getByText("合成频谱批次2（50 Hz，非实测）",{exact:true}).click();
  await page.keyboard.press("Escape");
  await heading(page,"工程研究工作台").click();
}

async function newTask(page,question) {
  await page.getByRole("textbox",{name:"研究目标",exact:true}).fill(question);
  await button(page,"新建研究任务").click();
  await heading(page,question).waitFor();
}

async function approve(page,round) {
  await heading(page,"待审批结构化计划").waitFor();
  await button(page,"批准并加入本地队列").click();
  await page.getByText(new RegExp("待复核 · "+round+"/4 轮")).waitFor({timeout:140000});
  return archive(page);
}

async function prepare(page,theme) {
  await page.route("**/*",r=>new URL(r.request().url()).origin===address.origin ||
    /^(data:|blob:)/.test(r.request().url())?r.continue():r.abort());
  const url=new URL(address);
  url.searchParams.set("embed_options",theme+"_theme");
  await page.goto(url.href);
  await heading(page,"工程工作台").waitFor();
}

async function screenCaptureMetrics(frame) {
  await frame.locator("#sim-rate").selectOption(".25");
  await frame.locator("#sim-reset").click();
  await frame.locator("#sim-play").click();
  const metrics=await frame.locator("#surface canvas").evaluate(async canvas=>{
    const root=canvas.closest("#workbench"), first=Number(root.dataset.frames);
    const video=document.createElement("video");
    video.style.cssText="position:fixed;left:4px;top:4px;width:42px;height:28px;z-index:99";
    video.muted=true; video.srcObject=canvas.captureStream();
    root.append(video);
    const stamps=[];
    let handle;
    const tick=(now,metadata)=> {
      stamps.push({now,presented:metadata.presentedFrames});
      handle=video.requestVideoFrameCallback(tick);
    };
    try {
      handle=video.requestVideoFrameCallback(tick);
      await video.play();
      const start=performance.now();
      await new Promise(resolve=>setTimeout(resolve,2350));
      const times=stamps.slice(1).map((d,i)=>d.now-stamps[i].now).sort((a,b)=>a-b);
      return {renderer:root.dataset.renderer,load_ms:Number(root.dataset.loadMs),draw_calls:Number(root.dataset.calls),
        triangles:Number(root.dataset.triangles),seconds:(performance.now()-start)/1000,
        render_submissions:Number(root.dataset.frames)-first, capture_video_callbacks:stamps.length,
        capture_frame_interval_p50_ms:times[Math.floor(times.length*.50)]??null,
        capture_frame_interval_p95_ms:times[Math.floor(times.length*.95)]??null,
        capture_presented_frame_delta:stamps.at(-1)?.presented-stamps[0]?.presented || 0};
    } finally {
      video.cancelVideoFrameCallback(handle);
      for(const track of video.srcObject.getTracks()) track.stop();
      video.remove();
    }
  });
  await frame.locator("#sim-reset").click();
  return metrics;
}

async function localRun(browser){
  const page=await browser.newPage({viewport:{width:1366,height:768},acceptDownloads:true});
  try {
    await prepare(page,"dark");
    await page.screenshot({path:resolve(output,"1366-home.png")});
    await button(page,"工程研究").click();
    await heading(page,"工程研究工作台").waitFor();
    if(await page.getByText("创建研究任务后可预览计划、批准运行并查看实际证据。",{exact:true}).isVisible())
      await page.screenshot({path:resolve(output,"1366-empty-research.png")});
    await casePair(page);
    await newTask(page,"横向异常与悬挂优化");
    await button(page,"生成本轮计划").click();
    await heading(page,"待审批结构化计划").waitFor();
    await page.screenshot({path:resolve(output,"1366-approval.png")});
    const base=await approve(page,1);
    const solved=Object.values(base.tools).filter(row=>row.output?.source==="simulation");
    assert.equal(solved.length,2);
    assert(solved.every(row=>row.output.response.solver.method==="DOP853"));
    assert(Object.values(base.tools).some(row=>row.output?.pareto?.length));
    await page.getByText("双视窗比较求解",{exact:true}).click();
    const frame=page.frameLocator('iframe[title*="bogie_viewer"]').first();
    await frame.locator("#twin-panes:not([hidden])").waitFor();
    await frame.locator("#solver-values-toggle").click();
    await frame.locator("#solver-values").getByText(/前轴左 \/ 右轮重/).waitFor();
    await frame.locator("#lateral-peak").click();
    await frame.locator("#workbench[data-wheelrail-glb=true]").scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(output,"1366-solver-workbench.png")});
    const performance=await screenCaptureMetrics(frame);
    assert(performance.draw_calls>0 && performance.triangles>0 && performance.render_submissions>5);
    assert(performance.capture_video_callbacks>0);
    if(native) assert.equal(performance.renderer,"default");
    await writeFile(resolve(output,"render-performance.json"),JSON.stringify(performance,null,2));
    await page.getByText("双视窗比较求解",{exact:true}).click();
    await page.getByRole("textbox",{name:"本轮目标",exact:true}).fill("请用独立积分复算");
    await button(page,"生成本轮计划").click();
    const next=await approve(page,2);
    assert(Object.values(next.tools).some(row=>row.output?.response?.solver.method==="RK45"));
    await writeFile(resolve(output,"local-research.json"),JSON.stringify(next,null,2));
    await button(page,"结束研究并归档").click();
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("Local worker: approved task, real DOP853/Pareto, dual 3D, capture metrics and a separate RK45 round passed.");
  } catch(error) {
    await page.screenshot({path:resolve(output,"local-failure.png")});
    throw error;
  } finally {await page.close();}
}

async function lightRun(browser, cloud=false){
  const page=await browser.newPage({viewport:{width:1920,height:1080},acceptDownloads:true});
  try {
    await prepare(page,"light");
    await button(page,"工程研究").click();
    await heading(page,"工程研究工作台").waitFor();
    if(!cloud) {
      await page.screenshot({path:resolve(output,"1920-archive.png")});
      const desktop=await page.context().newCDPSession(page);
      await desktop.send("Emulation.setPageScaleFactor",{pageScaleFactor:1.15});
      await heading(page,"工程研究工作台").waitFor();
      await page.screenshot({path:resolve(output,"1920-archive-115percent.png")});
      await desktop.send("Emulation.setPageScaleFactor",{pageScaleFactor:1});
      await nav(page,"三维转向架").click();
      await heading(page,"三维诊断工作台").waitFor();
      await page.screenshot({path:resolve(output,"1920-studio.png")});
      return;
    }
    await casePair(page);
    await newTask(page,"复核横向异常并在下一轮分析频谱");
    await page.getByText("云端结构化规划",{exact:true}).click();
    await page.getByRole("checkbox",{name:"同意发送本轮目标与压缩状态至已配置服务"}).waitFor();
    assert(await button(page,"生成本轮计划").isDisabled());
    await page.getByText("同意发送本轮目标与压缩状态至已配置服务",{exact:true}).click();
    await button(page,"生成本轮计划").click();
    const first=await approve(page,1);
    assert.equal(first.task.rounds[0].origin,"cloud");
    assert(first.tools.event.output.events.length);
    await page.getByRole("textbox",{name:"本轮目标",exact:true}).fill("依据上一轮事件存在状态选择频谱工具");
    await page.getByText("云端结构化规划",{exact:true}).click();
    const consent=page.getByRole("checkbox",{name:"同意发送本轮目标与压缩状态至已配置服务"});
    await consent.waitFor();
    assert(!(await consent.isChecked()));
    await page.getByText("同意发送本轮目标与压缩状态至已配置服务",{exact:true}).click();
    await button(page,"生成本轮计划").click();
    const next=await approve(page,2);
    assert.equal(next.tools.spectrum.output.figure.xlabel,"频率（Hz）");
    assert(next.task.rounds[1].steps[0].depends.includes("event"));
    await writeFile(resolve(output,"mock-cloud-research.json"),JSON.stringify(next,null,2));
    await page.screenshot({path:resolve(output,"1920-mock-replanning.png")});
    console.log("Loopback model selected different tools in two rounds using observed status; schema, approval, and real FFT passed.");
  } catch(error){
    await page.screenshot({path:resolve(output,"light-failure.png")});
    throw error;
  } finally {await page.close();}
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:native?[]:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    if(process.env.RAIL_IX_MOCK==="1") await lightRun(browser,true);
    else {await localRun(browser); await lightRun(browser);}
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
