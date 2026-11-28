const assert = require("node:assert/strict");
const {writeFile} = require("node:fs/promises");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
assert.equal(address.hostname, "127.0.0.1");
const output = process.argv[3];
const duration = Number(process.argv[4] || 25) * 1000;
assert(duration >= 5000 && duration <= 60000);
const viewer = "iframe[title*=bogie_viewer]";
const nav = (page, name) => page.getByTestId("stSidebar").getByRole("button", {name, exact:true});

async function heap(session) {
  await session.send("HeapProfiler.collectGarbage");
  const counters = await session.send("Memory.getDOMCounters");
  const {metrics} = await session.send("Performance.getMetrics");
  return {heap_used_bytes:metrics.find(({name}) => name==="JSHeapUsedSize").value,...counters};
}

async function trial(mode) {
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true,
    args:mode==="software"?["--use-angle=swiftshader","--enable-unsafe-swiftshader"]:[]});
  try {
    const page = await browser.newPage({viewport:{width:1366,height:768}});
    page.setDefaultTimeout(30000);
    const session = await page.context().newCDPSession(page);
    await session.send("Performance.enable");
    await page.goto(address.href);
    await page.getByRole("heading",{name:"工程工作台",exact:true}).waitFor();
    const before=await heap(session);
    await nav(page,"三维转向架").click();
    const frame=page.frameLocator(viewer).first();
    const root=frame.locator("#workbench");
    await frame.locator("#workbench[data-status=ready]").waitFor();
    await page.getByRole("button",{name:"播放",exact:true}).click();
    await frame.locator('#workbench[data-playing="true"]').waitFor();
    const samples=await frame.locator("canvas").evaluate(async (canvas,duration) => {
      const root=canvas.closest("#workbench"), stamps=[];
      const video=document.createElement("video");
      video.muted=true;
      video.style.cssText="position:fixed;left:4px;top:4px;width:42px;height:28px;z-index:99";
      const stream=canvas.captureStream();
      video.srcObject=stream;
      root.append(video);
      let handle;
      const callback=(time)=>{stamps.push(time);handle=video.requestVideoFrameCallback(callback);};
      const gl=canvas.getContext("webgl2");
      const debug=gl.getExtension("WEBGL_debug_renderer_info");
      const gpu=debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):"unavailable";
      let started, first;
      try {
        handle=video.requestVideoFrameCallback(callback);
        await video.play();
        started=performance.now(); first=Number(root.dataset.frames);
        await new Promise(resolve=>setTimeout(resolve,duration));
        const gaps=stamps.slice(1).map((time,i)=>time-stamps[i]).sort((a,b)=>a-b);
        const quantile=(p)=>gaps.length?gaps[Math.floor((gaps.length-1)*p)]:null;
        return {renderer:root.dataset.renderer, gpu, seconds:(performance.now()-started)/1000,
          submitted:Number(root.dataset.frames)-first, captured:stamps.length,
          p50_ms:quantile(.5),p95_ms:quantile(.95), triangles:Number(root.dataset.triangles),
          draw_calls:Number(root.dataset.calls),load_ms:Number(root.dataset.loadMs)};
      } finally {
        video.cancelVideoFrameCallback(handle);
        for(const track of stream.getTracks())track.stop();
        video.remove();
      }
    },duration);
    assert(samples.submitted>0 && samples.captured>0);
    await page.getByRole("button",{name:"暂停",exact:true}).click();
    const observed=[];
    for(let index=0;index<4;index++){
      await nav(page,"工作台首页").click();
      await page.locator(viewer).first().waitFor({state:"detached"});
      await page.getByRole("heading",{name:"工程工作台",exact:true}).waitFor();
      observed.push(await heap(session));
      if(index<3){
        await nav(page,"三维转向架").click();
        await frame.locator("#workbench[data-status=ready]").waitFor();
      }
    }
    return {mode,before,samples,after_each_unmount:observed};
  } finally { await browser.close(); }
}

(async()=> {
  const native=await trial("native");
  const software=await trial("software");
  const report={scope:"headless Edge, sustained 1366x768 teaching playback, four mount/unmount samples, JS heap only",
    tests:[native,software]};
  await writeFile(output,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
})().catch(error=>{console.error(error);process.exitCode=1;});
