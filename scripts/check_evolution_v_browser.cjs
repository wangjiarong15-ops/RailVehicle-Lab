const assert = require("node:assert/strict");
const {mkdir,readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const address=new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
const output=resolve(process.argv[3]);

async function download(page,name) {
  const ready=page.waitForEvent("download");
  await page.getByRole("button",{name,exact:true}).click();
  return readFile(await (await ready).path());
}

function iframe(page) {
  return page.frameLocator('iframe[title*="bogie_viewer"]').first();
}

async function verifyTrack(frame) {
  const viewport=frame.locator('#workbench[data-wheelrail="true"]');
  await viewport.waitFor({timeout:45000});
  assert.equal(await viewport.getAttribute("data-wheelrail-glb"),"true");
  const slider=frame.locator("#sim-time");
  await slider.evaluate(node=>{
    node.value="225";
    node.dispatchEvent(new Event("input",{bubbles:true}));
  });
  await viewport.evaluate(node=>new Promise((done,fail)=>{
    const end=performance.now()+10000;
    function check() {
      if (Math.abs(+node.dataset.wheelrailStation-35.2)<1e-8
          && +node.dataset.wheelrailHeading>0 && +node.dataset.triangles>1000) return done();
      if (performance.now()>end) return fail(Error("线路里程、姿态或网格未跟随求解帧更新 "+JSON.stringify({...node.dataset})));
      requestAnimationFrame(check);
    }
    check();
  }));
  assert(Math.abs(+await frame.locator("#response-cursor").getAttribute("x1")-750)<.01);
  const normal=(await viewport.getAttribute("data-wheelrail-normal")).split(",").map(Number);
  assert.equal(normal.length,2);
  assert(normal.every(x=>x>1000));
  await frame.locator("#sim-next").click();
  assert.equal(+await slider.inputValue(),226);
  await frame.locator("#sim-reset").click();
  assert.equal(+await slider.inputValue(),0);
  await frame.locator("#sim-play").click();
  await slider.evaluate(node=>new Promise((done,fail)=>{
    const end=performance.now()+8000;
    function check() {
      if (+node.value>3) return done();
      if (performance.now()>end) return fail(Error("播放未推进求解帧"));
      requestAnimationFrame(check);
    }
    check();
  }));
  await frame.locator("#sim-play").click();
  await slider.evaluate(node=>{
    node.value="225";
    node.dispatchEvent(new Event("input"));
  });
  return viewport;
}

async function audit(browser,width,height,theme) {
  const page=await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/*",route=>new URL(route.request().url()).origin===address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture=name=>page.screenshot({path:resolve(output,`${width}-${theme}-${name}.png`)});
  const url=new URL(address);
  url.searchParams.set("rail_page","动力学仿真");
  url.searchParams.set("embed_options",theme+"_theme");
  try {
    await page.goto(url.href);
    await page.getByRole("combobox",{name:"计算工况"}).click();
    await page.getByText("高级轮轨动力学（31 自由度）",{exact:true}).click();
    await page.getByRole("heading",{name:"高级轮轨动力学",exact:true}).waitFor();
    const frame=iframe(page);
    await verifyTrack(frame);
    await frame.locator("#surface").scrollIntoViewIfNeeded();
    await capture("curve-3d");
    await frame.locator("#wheelrail-forces").click();
    await frame.locator("#wheelrail-focus").click();
    assert.equal(await frame.locator("#wheelrail-focus").getAttribute("aria-pressed"),"true");
    await frame.locator("#wheelrail-vector-note:not([hidden])").waitFor();
    await capture("wheel-rail-contact");
    if(width===1366) {
      await page.getByRole("button",{name:"将轮轨工况交给 Copilot",exact:true}).click();
      const chat=page.getByTestId("stChatInputTextArea");
      await chat.fill("建立曲线通过试验，速度 20 m/s，曲线半径 450 m，比较左右悬挂不对称，二系垂向行程不超过 35 mm，预算 6 次，独立复算并生成报告。");
      await chat.press("Enter");
      await page.getByRole("button",{name:"生成轮轨试验审批计划",exact:true}).click();
      await page.getByText(/待审批 · 1 个速度与半径组合 · 至多 6 次求解/).waitFor();
      assert.equal(await page.getByRole("heading",{name:"曲线试验证据",exact:true}).count(),0);
      await page.getByRole("button",{name:"批准执行轮轨试验",exact:true}).click();
      await page.getByRole("heading",{name:"曲线试验证据",exact:true}).waitFor({timeout:150000});
      const archive=JSON.parse((await download(page,"下载轮轨试验证据 JSON")).toString());
      assert.equal(archive.solves,6);
      assert.equal(archive.plan.max_travel_mm,35);
      assert(archive.comparison.qualified);
      assert(archive.comparison.candidate.rows.every(row=>row.solver.method==="RK45"));
      assert.equal((await download(page,"下载轮轨试验 PDF")).subarray(0,5).toString(),"%PDF-");
      await page.getByText("打开复算双视窗",{exact:true}).click();
      await frame.locator("#twin-panes:not([hidden])").waitFor({timeout:55000});
      await verifyTrack(frame);
      const comparison=frame.locator('#workbench[data-wheelrail="true"]');
      assert.notEqual(await comparison.getAttribute("data-wheelrail-other"),"");
      await frame.locator("#surface").scrollIntoViewIfNeeded();
      await capture("verified-comparison");
    }
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(`${width}x${height} ${theme}: wheel-rail station, frame cursor, playback, vectors and close-up passed.`);
  } catch(error) {
    await capture("failure");
    throw error;
  } finally { await page.close(); }
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try{
    await audit(browser,1366,768,"dark");
    await audit(browser,1920,1080,"light");
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
