// Local-only acceptance. The runner creates a separate, fictional SQLite database.
const assert = require("node:assert/strict");
const {mkdir,readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const url = new URL(process.argv[2]), out = resolve(process.argv[3]);
assert.equal(url.origin,"http://127.0.0.1:"+url.port);
url.searchParams.set("embed_options","dark_theme");
async function audit(browser,width,height) {
  const page = await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors = [];
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/*",r=>new URL(r.request().url()).origin===url.origin || /^(data:|blob:)/.test(r.request().url()) ? r.continue():r.abort());
  const nav = label=>page.getByTestId("stSidebar").getByRole("button",{name:new RegExp(label+"$")});
  const capture = name=>page.screenshot({path:resolve(out,width+"-"+name+".png")});
  const ask = async text=>{
    const input = page.getByTestId("stChatInputTextArea");
    await input.fill(text);
    await input.press("Enter");
  };
  try {
    await page.goto(url.href);
    await nav("AI Copilot").click();
    await page.getByRole("heading",{name:"RailVehicle Copilot",exact:true}).waitFor();
    const composer = page.getByTestId("stChatInputTextArea");
    const box = await composer.boundingBox();
    assert(box && box.y >= 0 && box.y + box.height < height);
    await capture("workspace");
    await ask("解释轮轨关系的原理");
    await page.getByText(/本地检索命中 [1-9]/).waitFor();
    await ask("展示蛇行运动示意");
    const teachingFrame = page.frameLocator("iframe[title*=bogie_viewer]").last();
    const teaching = teachingFrame.locator("#workbench");
    await teachingFrame.locator("#workbench[data-model=glb][data-playing=true]").waitFor();
    await teaching.scrollIntoViewIfNeeded();
    // Measure after the server-driven viewer's snapshot timeout has already elapsed.
    await page.waitForTimeout(1400);
    const teachingPhase = Number(await teaching.getAttribute("data-phase"));
    const until = Date.now() + 6000;
    while (Number(await teaching.getAttribute("data-phase")) <= teachingPhase && Date.now() < until) {
      await page.waitForTimeout(100);
    }
    assert(Number(await teaching.getAttribute("data-phase")) > teachingPhase, "教学运动在首条指令后停住");
    await ask("暂停");
    await teachingFrame.locator("#workbench[data-playing=false]").waitFor();
    const pausedPhase = Number(await teaching.getAttribute("data-phase"));
    assert(pausedPhase >= teachingPhase && pausedPhase > 0);
    await page.waitForTimeout(350);
    assert.equal(Number(await teaching.getAttribute("data-phase")), pausedPhase);
    await ask("继续播放");
    await teachingFrame.locator("#workbench[data-playing=true]").waitFor();
    assert(Number(await teaching.getAttribute("data-phase")) >= pausedPhase);
    await ask("重置");
    await teachingFrame.locator('#workbench[data-playing=false][data-phase="0.0000"]').waitFor();
    await page.getByRole("button",{name:"新建会话"}).click();
    await page.getByRole("button",{name:"使用虚构批次体验"}).click();
    await page.getByRole("button",{name:/横向异常与三维回查/}).waitFor();
    const begin = Date.now();
    await page.getByRole("button",{name:/横向异常与三维回查/}).click();
    const event = page.getByRole("button",{name:"在三维时间轴回放此区段"});
    await event.waitFor();
    const latency = Date.now()-begin;
    await page.getByText(/metric.accel.lateral.rms/).first().waitFor();
    const observedModel = page.locator("iframe[title*=bogie_viewer]").first();
    const linked = observedModel.contentFrame();
    await linked.locator("#workbench[data-model=glb][data-status=ready][data-highlighted*=构架]").waitFor();
    // Chat opens at the latest answer. Bring the whole embedded viewer into
    // the viewport before picking a part so the sticky composer stays clear.
    await observedModel.evaluate(node => node.scrollIntoView({block:"center",behavior:"instant"}));
    await linked.locator("#browse-parts").click();
    await linked.locator("#part-query").fill("coil_a_l");
    const coil = linked.locator('[data-part="coil_a_l"]');
    await coil.press("Enter");
    await linked.locator('#workbench[data-selected="coil_a_l"]').waitFor();
    const channelHint = linked.locator("#channel-context");
    await channelHint.filter({hasText:/内置虚构.*当前分析范围第 31 行.*横向.*垂向.*安装点未知/}).waitFor();
    await linked.locator("#browse-parts").click();
    const cursor = page.locator('[class*="_cursor"]').getByRole("slider");
    await cursor.focus();
    await cursor.press("ArrowLeft");
    await linked.locator('#workbench[data-highlighted=""]').waitFor();
    await channelHint.filter({hasText:/当前分析范围第 30 行/}).waitFor();
    await cursor.press("ArrowRight");
    await linked.locator("#workbench[data-highlighted*=构架]").waitFor();
    await cursor.evaluate(n => {
      n.scrollIntoView({block:"start"});
      for (let p=n.parentElement;p;p=p.parentElement) {
        if (p.scrollTop>0) { p.scrollBy(0,-150); break; }
      }
    });
    await capture("evidence");
    await event.click();
    const meter=page.locator(".st-key-bogie_status_card");
    await meter.getByText(/第 31\/120 条/).waitFor();
    await meter.getByText("1.400",{exact:true}).waitFor();
    await nav("AI Copilot").click();
    await ask("将悬挂刚度提高20%，比较振动响应");
    await page.getByText(/450 → 540 kN\/m/).first().waitFor();
    const model = page.frameLocator("iframe[title*=bogie_viewer]").last();
    await model.locator("#sim-time:visible").waitFor();
    const slider = model.locator("#sim-time");
    await slider.evaluate(n => { n.value = "183"; n.dispatchEvent(new Event("input")); });
    await slider.press("ArrowRight");
    await model.locator('#response[data-sample="184"]').waitFor();
    await model.locator("#view").selectOption({label:"侧视图"});
    await model.locator("#surface-mode").selectOption("ghost");
    await ask("暂停");
    await page.getByText("已暂停在当前位置。",{exact:true}).waitFor();
    await model.locator('#response[data-sample="184"]').waitFor();
    await page.waitForTimeout(350);
    assert.equal(await slider.inputValue(),"184");
    assert.equal(await model.locator("#view").inputValue(),"侧视图");
    assert.equal(await model.locator("#surface-mode").inputValue(),"ghost");
    await model.locator("#workbench").scrollIntoViewIfNeeded();
    await capture("paused-cursor");
    await ask("继续播放");
    await page.getByText("已从当前位置播放。",{exact:true}).waitFor();
    await model.locator("#sim-play").filter({hasText:"暂停"}).waitFor();
    assert(Number(await slider.inputValue()) >= 184);
    await ask("暂停在2秒");
    await page.getByText("三维时间轴定位到 2 s。",{exact:true}).waitFor();
    await model.locator('#response[data-sample="200"]').waitFor();
    await model.locator("#response").scrollIntoViewIfNeeded();
    await capture("simulation");
    await ask("根据本次分析生成完整报告");
    const download = page.getByRole("button",{name:"下载工程分析 PDF"});
    await download.waitFor();
    const [pdf] = await Promise.all([page.waitForEvent("download"),download.click()]);
    assert((await readFile(await pdf.path())).subarray(0,5).equals(Buffer.from("%PDF-")));
    await nav("运行分析").click();
    await page.getByRole("button",{name:"在 Copilot 中分析"}).click();
    await page.getByText(/历史导入数据.*DEMO-001/).first().waitFor();
    await page.getByRole("button",{name:/横向异常与三维回查/}).waitFor();
    assert.equal(await page.getByText(/450 → 540 kN\/m/).count(),0);
    if(width===1366){
      await page.getByRole("button",{name:"数据来源与附件"}).click();
      await page.locator(".st-key-cp_source").getByRole("combobox").click();
      await page.getByRole("option",{name:"会话 CSV",exact:true}).click();
      const csv="timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"+
        "2026-01-01T00:00:00Z,FICT-A,12,0.1,0.2\n2026-01-01T00:00:01Z,FICT-A,12,1.8,0.2\n"+
        "2026-01-01T00:00:00Z,FICT-B,8,0.3,0.2";
      const uploader = page.getByTestId("stFileUploader").filter({hasText:"临时分析 CSV"});
      await uploader.waitFor();
      await page.locator('[data-stale="true"]').first().waitFor({state:"detached"});
      const threshold = page.getByRole("spinbutton",{name:/加速度绝对值阈值/});
      assert.equal(Number(await threshold.inputValue()),1);
      await threshold.fill("1.2");
      await threshold.press("Enter");
      const [chooser] = await Promise.all([page.waitForEvent("filechooser"),uploader.locator("button").click()]);
      await chooser.setFiles({
        name:"fictional-upload.csv",mimeType:"text/csv",buffer:Buffer.from(csv)
      });
      await page.locator(".st-key-cp_attachment_vehicle").waitFor();
      await page.getByRole("heading",{name:"RailVehicle Copilot",exact:true}).click();
      await ask("找出所有横向加速度超阈值区段");
      await page.getByText(/峰值 1.8 m\/s²/).first().waitFor();
      assert.equal(await event.count(),0);
      await nav("工作台首页").click();
      await nav("AI Copilot").click();
      await page.getByText(/会话 CSV 附件.*FICT-A.*fictional-upload.csv/).waitFor();
      await page.getByText(/峰值 1.8 m\/s²/).first().waitFor();
      await page.getByRole("button",{name:"数据来源与附件"}).click();
      assert.equal(Number(await threshold.inputValue()),1.2);
    }
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,height,workflowMs:latency,replay:true,teachingPhase,pausedPhase,
      pausedSample:184,explicitSeekSample:200,pdf:true,scopeIsolated:true}));
  } catch(e) {await capture("failure");throw e;} finally {await page.close();}
}
(async()=>{
  await mkdir(out,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try { for (const [w,h] of [[1366,768],[1920,1080]]) await audit(browser,w,h); }
  finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
