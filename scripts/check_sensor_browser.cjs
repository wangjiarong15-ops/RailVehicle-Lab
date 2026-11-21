// This suite writes a fictional calibration only in the runner's disposable SQLite database.
const assert = require("node:assert/strict");
const {mkdir,readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const base = new URL(process.argv[2]), out = resolve(process.argv[3]);
assert.equal(base.hostname,"127.0.0.1");
base.searchParams.set("rail_page","数据导入");
base.searchParams.set("embed_options","dark_theme");

(async()=>{
  await mkdir(out,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  const page=await browser.newPage({viewport:{width:1366,height:768},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/*",r=>new URL(r.request().url()).origin===base.origin || /^(data:|blob:)/.test(r.request().url())
    ? r.continue():r.abort());
  const input = label=>page.getByRole("textbox",{name:label,exact:true});
  async function choose(label,value) {
    const combo=page.getByRole("combobox",{name:label,exact:true});
    const idle=page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]');
    const option=page.getByRole("option",{name:value,exact:true});
    const part=label==="安装部件";
    for (let attempt=0; attempt<3; attempt++) {
      await idle.waitFor();
      await combo.evaluate(n=>n.scrollIntoView({block:"center",behavior:"instant"}));
      await combo.click();
      await combo.fill(part ? value.split(" · ").at(-1) : value);
      try {
        await option.click({timeout:2500});
        if (!part) return;
        // Remounting the dropdown can discard a click. Confirm the committed label before saving.
        const id=await combo.getAttribute("id");
        await page.waitForFunction(([control,expected])=>document.getElementById(control)?.value===expected,
          [id,value],{timeout:2000});
        return;
      } catch(error) {
        if (attempt===2) throw error;
      }
    }
  }
  const click=label=>page.getByRole("button",{name:label,exact:true}).click();
  async function openSensors(scope) {
    const heading=page.locator(".st-key-sensor_"+scope+"_1_1").getByText("传感器配置与数据溯源",{exact:true});
    await heading.waitFor();
    if (!await heading.evaluate(el=>el.closest("details").open)) await heading.click();
  }
  async function downloadJson(label, savedName) {
    const button=page.getByRole("button",{name:label,exact:true});
    await button.waitFor();
    const [file]=await Promise.all([page.waitForEvent("download"),button.click()]);
    await file.saveAs(resolve(out,savedName || file.suggestedFilename()));
    return JSON.parse(await readFile(await file.path(),"utf8"));
  }
  try {
    await page.goto(base.href);
    await choose("打开历史批次","#1 · demo_run_1.csv");
    await openSensors("history");
    await input("传感器唯一编号").fill("LAB-A-01");
    await input("来源凭据或记录编号").fill("虚构安装记录 QA-01");
    await choose("安装部件","A 轴 左侧 轴箱 · axlebox_a_l");
    await choose("配置性质","虚构教学示意");
    await choose("安装位置坐标系","模型");
    await choose("安装点核验状态","已核验");
    await choose("传感器局部测量正方向","+Y");
    await choose("CSV 通道单位","m/s²");
    await choose("轴向标定状态","已核验");
    await choose("记录时间基准","UTC / 显式时区");
    await choose("时钟核验状态","已核验");
    await input("安装位置 [x,y,z]（m）").fill("[-1.30,-1.20,0.50]");
    for (const label of ["传感器到车辆旋转矩阵（3×3）","车辆到模型旋转矩阵（3×3）"])
      await input(label).fill("[[1,0,0],[0,1,0],[0,0,1]]");
    await input("增益").fill("1");
    await input("偏置（通道单位）").fill("0");
    await click("保存配置版本");
    await page.getByText("查看修改记录",{exact:true}).waitFor();
    await click("回放历史批次 →");
    await choose("传感器与波形","LAB-A-01 · 横向加速度");
    const model=page.frameLocator("iframe[title*=bogie_viewer]").first();
    await model.locator('#workbench[data-selected="axlebox_a_l"][data-model=glb]').waitFor();
    await model.locator("#sensor-overlay text").filter({hasText:"LAB-A-01 · 示意"}).waitFor();
    const arrow=model.locator("#sensor-overlay line");
    assert(Math.hypot(Number(await arrow.getAttribute("x2")),Number(await arrow.getAttribute("y2")))>1);
    await model.locator("#workbench").scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"sensor-mount.png")});
    await model.locator("#hide").click();
    await model.locator("#sensor-overlay").waitFor({state:"hidden"});
    await model.locator("#restore").click();
    await model.locator("#sensor-overlay text").waitFor();
    const plot=page.locator(".st-key-bogie_curves .js-plotly-plot");
    await plot.waitFor();
    const traces=await plot.evaluate(n=>n.data.map(t=>t.name));
    assert(traces.length===2 && traces.every(t=>t.includes("横向")));
    await page.getByText(/CSV 原始起始行 2 · lateral_accel/).waitFor();
    const point=plot.locator(".scatterlayer .trace").first().locator("path.point").nth(30);
    await point.scrollIntoViewIfNeeded();
    const box=await point.boundingBox();
    await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
    await page.getByText(/CSV 原始起始行 32 · lateral_accel/).waitFor();
    // Return through the analysis workbench so Copilot receives the exact historical batch.
    await page.goto(base.href);
    await choose("打开历史批次","#1 · demo_run_1.csv");
    await click("分析历史批次 →");
    await openSensors("analysis");
    const packet=await downloadJson("下载本通道分析证据");
    assert.equal(packet.configuration.serial,"LAB-A-01");
    assert.equal(packet.configuration.version,1);
    assert.equal(packet.csv_lines_in_analysis_order.length,120);
    assert.equal(packet.sha256.length,64);
    await page.getByText("用独立参考数据复核标定",{exact:true}).click();
    await input("独立参考记录编号").fill("虚构台架时钟与激励记录");
    const reference = [
      ["source_sha256","csv_line","reference_utc","model_ax_m_s2","model_ay_m_s2","model_az_m_s2"],
      [packet.sha256,32,"2026-10-08T09:30:30Z",0,1.4,0],
      [packet.sha256,33,"2026-10-08T09:30:31Z",0,1.8,0],
      [packet.sha256,34,"2026-10-08T09:30:32Z",0,-1.5,0],
    ].map(row=>row.join(",")).join("\n");
    const upload = page.locator('input[type="file"]').last();
    await upload.setInputFiles({name:"fictional-reference.csv",mimeType:"text/csv",buffer:Buffer.from(reference)});
    for (const [name,value] of [["最大加速度误差（m/s²）","0.05"],["最大时间误差（ms）","5"],["每个方向的最小激励（m/s²）","1"]])
      await page.getByRole("spinbutton",{name,exact:true}).fill(value);
    await click("复核当前标定");
    await page.getByText(/所选参考点在设定误差内/).waitFor();
    const check = await downloadJson("下载参考复核证据");
    assert.equal(check.configuration.version,1);
    assert.equal(check.status,"within_limits");
    assert.deepEqual(check.pairs.map(pair=>pair.csv_line),[32,33,34]);
    assert.equal(check.metrics.max_time_error_ms,0);
    await page.getByText("下载参考复核证据",{exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"calibration-reference.png")});
    await upload.setInputFiles({name:"fictional-offset-reference.csv",mimeType:"text/csv",
      buffer:Buffer.from(reference.replace("09:30:32Z","09:30:32.020Z"))});
    await click("复核当前标定");
    await page.getByText(/至少一个配对点超出设定误差/).waitFor();
    const failedCheck=await downloadJson("下载参考复核证据","fictional-reference-20ms-mismatch.json");
    assert.equal(failedCheck.metrics.max_time_error_ms,20);
    await click("在 Copilot 中分析");
    const chat=page.getByTestId("stChatInputTextArea");
    await chat.fill("查看传感器坐标标定和安装位置");
    await chat.press("Enter");
    await page.getByText(/已读取当前车辆和批次的传感器版本/).waitFor();
    await page.frameLocator("iframe[title*=bogie_viewer]").last().locator("#sensor-overlay text")
      .filter({hasText:"LAB-A-01"}).waitFor();
    // A revised fictional frame mount enables only applicable rigid-frame projections.
    await page.goto(base.href);
    await choose("打开历史批次","#1 · demo_run_1.csv");
    await openSensors("history");
    await choose("安装部件","左侧 侧梁 · side_beam_l");
    await input("安装位置 [x,y,z]（m）").fill("[0,-1.16,0.98]");
    await click("保存配置版本");
    await page.getByText("已保存传感器配置版本",{exact:true}).waitFor();
    await page.getByText("查看修改记录",{exact:true}).waitFor();
    await click("分析历史批次 →");
    await openSensors("analysis");
    await click("按此测量轴查看仿真响应");
    await page.getByText("沿登记测量轴的计算响应",{exact:true}).waitFor();
    const projection=await downloadJson("下载投影计算依据");
    assert.equal(projection.configuration.version,2);
    assert.equal(projection.time_basis,"relative_seconds");
    assert.equal(projection.axis_accel_m_s2.length,601);
    assert(projection.axis_accel_m_s2.every(v=>v===0)); // +Y is orthogonal to the calculated +Z acceleration.
    await page.getByText("沿登记测量轴的计算响应",{exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"sensor-projection.png")});
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log("Fictional reference and 20 ms mismatch, GLB point and visibility, row 32, evidence, offline Copilot and 601 calculated projections passed.");
  } catch(e) {await page.screenshot({path:resolve(out,"failure.png")});throw e;}
  finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
