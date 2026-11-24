// This suite writes a fictional calibration only in the runner's disposable SQLite database.
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
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
  const continueTo=label=>page.getByTestId("stMain").getByRole("button",{name:label,exact:true}).click();
  let batch, batchLabel;
  const nav = name=>page.getByTestId("stSidebar").getByRole("button",{name:new RegExp(name+"$")}).click();
  async function openSensors(scope) {
    const heading=page.locator(".st-key-sensor_"+scope+"_1_"+batch).getByText("传感器配置与数据溯源",{exact:true});
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
    const runUpload=page.getByTestId("stFileUploader").locator("input");
    const csvHeader="timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel";
    await runUpload.setInputFiles({name:"invalid-run.csv",mimeType:"text/csv",
      buffer:Buffer.from(csvHeader+"\n2026-10-08T09:30:00Z,DEMO-001,72,,0")});
    await page.getByText(/CSV 校验未通过/).waitFor();
    await page.getByText(/必填字段缺值计数.*lateral_accel 1/).waitFor();
    assert.equal(await page.getByRole("button",{name:"导入到 SQLite",exact:true}).count(),0);
    const csv = ["timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel",
      ...Array.from({length:120},(_,i)=>[
        new Date(Date.UTC(2026,9,8,9,30,i,40)).toISOString(),"DEMO-001",72,
        [1.4,1.8,-1.5][i-30] ?? 0, i===70 ? 1.6 : 0,
      ].join(","))].join("\n");
    const sourceHash=createHash("sha256").update(csv).digest("hex");
    await runUpload.setInputFiles({name:"fictional-integrated.csv",mimeType:"text/csv",buffer:Buffer.from(csv)});
    await page.getByText("按车辆检查采样质量（待导入）",{exact:true}).waitFor();
    await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    const intakeHeading=page.getByText("工程数据接入（可选）",{exact:true});
    await intakeHeading.click();
    await page.getByText("本次导入附带工程元数据",{exact:true}).click();
    const engineeringUpload=page.getByTestId("stFileUploader").filter({hasText:"工程元数据 JSON"}).locator("input");
    const manifest={
      schema:"rail.engineering-intake.v1", source_kind:"synthetic", source_record:"虚构工程验收 QA-01",
      source_sha256:sourceHash, vehicle_code:"DEMO-001", sample_rate_hz:1, time_basis:"device_offset", clock_offset_s:-.04,
      units:{speed_kmh:"km/h",lateral_accel:"m/s²",vertical_accel:"m/s²"},
      sensors:{
        lateral_accel:{sensor_id:"LAB-A-01",part_id:"axlebox_a_l",position_frame:"model",position_m:[-1.30,-1.20,.50],axis:"+X"},
        vertical_accel:{sensor_id:"LAB-V-01",part_id:"side_beam_r",position_frame:"model",position_m:[0,1.16,.98],axis:"+Z"},
      },
    };
    const importButton=page.getByRole("button",{name:"导入到 SQLite",exact:true});
    async function showIntakeMessage(message) {
      const expected=page.getByText(message);
      await expected.waitFor({state:"attached"});
      if (!await intakeHeading.evaluate(node=>node.closest("details").open)) await intakeHeading.click();
      await expected.waitFor();
    }
    await showIntakeMessage(/^请上传工程元数据 JSON。$/);
    assert(await importButton.isDisabled());
    async function loadManifest(name, data) {
      await engineeringUpload.setInputFiles({name,mimeType:"application/json",buffer:Buffer.from(JSON.stringify(data))});
    }
    await loadManifest("fictional-wrong-unit.json",{...manifest,units:{...manifest.units,lateral_accel:"g"}});
    await showIntakeMessage(/CSV 单位须为速度 km\/h/);
    assert(await importButton.isDisabled());
    await loadManifest("fictional-engineering.json",manifest);
    await showIntakeMessage(/工程接入元数据已核对/);
    await page.getByText(/时钟漂移证据不足/).waitFor();
    await page.screenshot({path:resolve(out,"engineering-intake.png")});
    await click("导入到 SQLite");
    const receipt=page.getByText(/导入成功：批次 #\d+，共保存 120 行。/);
    await receipt.waitFor();
    batch=Number((await receipt.textContent()).match(/#(\d+)/)[1]);
    batchLabel=`#${batch} · fictional-integrated.csv`;
    await page.locator(".st-key-import_analyze_latest button").click();
    await openSensors("analysis");
    await page.getByText("用独立参考数据复核标定",{exact:true}).click();
    await page.getByText(/请在数据导入页登记并核验安装点/).waitFor();
    assert.equal(await page.getByRole("button",{name:"下载参考复核证据",exact:true}).count(),0);
    await page.goto(base.href);
    await choose("打开历史批次",batchLabel);
    await openSensors("history");
    await input("传感器唯一编号").fill("LAB-A-01");
    await input("来源凭据或记录编号").fill("虚构安装记录 QA-01");
    await choose("安装部件","A 轴 左侧 轴箱 · axlebox_a_l");
    await choose("配置性质","虚构教学示意");
    await choose("安装位置坐标系","模型");
    await choose("安装点核验状态","已核验");
    await choose("传感器局部测量正方向","+X");
    await choose("CSV 通道单位","m/s²");
    await choose("轴向标定状态","已核验");
    await choose("记录时间基准","设备时间加固定偏移至 UTC");
    await choose("时钟核验状态","已核验");
    await input("安装位置 [x,y,z]（m）").fill("[-1.30,-1.20,0.50]");
    await input("传感器到车辆旋转矩阵（3×3）").fill("[[0,-1,0],[1,0,0],[0,0,1]]");
    await input("车辆到模型旋转矩阵（3×3）").fill("[[1,0,0],[0,1,0],[0,0,1]]");
    await input("增益").fill("2");
    await input("偏置（通道单位）").fill("0.1");
    await input("时钟偏移（s）").fill("-0.04");
    await input("采样率（Hz）").fill("1");
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
    await choose("打开历史批次",batchLabel);
    await click("分析历史批次 →");
    await openSensors("analysis");
    const packet=await downloadJson("下载本通道分析证据");
    assert.equal(packet.configuration.serial,"LAB-A-01");
    assert.equal(packet.configuration.version,1);
    assert.equal(packet.csv_lines_in_analysis_order.length,120);
    assert.equal(packet.sha256,sourceHash);
    assert.equal(packet.data_origin,"虚构工程演练");
    assert.deepEqual(packet.registration.direction,[0,1,0]);
    await page.getByText("用独立参考数据复核标定",{exact:true}).click();
    const reference = [
      ["source_sha256","csv_line","reference_utc","model_ax_m_s2","model_ay_m_s2","model_az_m_s2"],
      [sourceHash,32,"2026-10-08T09:30:30Z",0,2.9,0],
      [sourceHash,33,"2026-10-08T09:30:31Z",0,3.7,0],
      [sourceHash,34,"2026-10-08T09:30:32Z",0,-2.9,0],
    ].map(row=>row.join(",")).join("\n");
    const upload = page.locator('input[type="file"]').last();
    async function loadReference(name, csvText) {
      const [response]=await Promise.all([
        page.waitForResponse(r=>r.request().method()==="PUT" && r.url().includes("/_stcore/upload_file/")),
        upload.setInputFiles({name,mimeType:"text/csv",buffer:Buffer.from(csvText)}),
      ]);
      assert(response.ok(),"Reference fixture upload failed");
      await page.getByText("参考文件已接收 · "+name,{exact:true}).waitFor();
    }
    async function submitReference() {
      await loadReference("fictional-reference.csv",reference);
      await input("独立参考记录编号").fill("虚构台架时钟与激励记录");
      await input("参考不确定度依据编号（可选）").fill("虚构计量依据 QA-U");
      for (const [name,value] of [["最大加速度误差（m/s²）","0.05"],["最大时间误差（ms）","5"],["每个方向的最小激励（m/s²）","1"],
        ["轴向参考扩展不确定度（m/s²）","0.06"],["参考计时扩展不确定度（ms）","1"]])
        await page.getByRole("spinbutton",{name,exact:true}).fill(value);
      await click("复核当前标定");
    }
    await submitReference();
    await page.getByText(/所选参考点在设定误差内/).waitFor();
    const check = await downloadJson("下载参考复核证据");
    assert.equal(check.configuration.version,1);
    assert.equal(check.status,"within_limits");
    assert.deepEqual(check.pairs.map(pair=>pair.csv_line),[32,33,34]);
    assert.equal(check.metrics.max_time_error_ms,0);
    assert.equal(check.reference_uncertainty.acceleration_status,"overlap");
    assert.equal(check.reference_uncertainty.time_status,"within");
    assert.equal(check.pairs[0].raw_m_s2,1.4);
    assert.ok(Math.abs(check.pairs[0].measured_m_s2-2.9)<1e-12);
    assert.equal(check.reference_sha256,createHash("sha256").update(reference).digest("hex"));
    await page.getByText("下载参考复核证据",{exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"calibration-reference.png")});
    await loadReference("fictional-offset-reference.csv",reference.replace("09:30:32Z","09:30:32.020Z"));
    await click("复核当前标定");
    await page.getByText(/至少一个配对点超出设定误差/).waitFor();
    const failedCheck=await downloadJson("下载参考复核证据","fictional-reference-20ms-mismatch.json");
    assert.equal(failedCheck.metrics.max_time_error_ms,20);
    assert.equal(failedCheck.reference_uncertainty.time_status,"outside");
    await loadReference("fictional-wrong-source.csv",reference.replaceAll(sourceHash,"0".repeat(64)));
    await click("复核当前标定");
    await page.getByText(/源文件 SHA-256 不匹配/).waitFor();
    assert.equal(await page.getByRole("button",{name:"下载参考复核证据",exact:true}).count(),0);
    await loadReference("fictional-reference.csv",reference);
    await click("复核当前标定");
    await page.getByText(/所选参考点在设定误差内/).waitFor();
    await continueTo("分析报告");
    await page.getByText(/标定复核将附入 PDF.*配置 v1/).waitFor();
    await page.getByText(/工程证据.*虚构工程演练.*独立实测验收待完成/).waitFor();
    await click("生成 PDF 分析报告");
    const [pdf]=await Promise.all([page.waitForEvent("download"),click("下载 PDF 报告")]);
    await pdf.saveAs(resolve(out,"fictional-integrated-report.pdf"));
    await page.waitForFunction(()=>{
      const key=Object.keys(sessionStorage).find(key=>key.startsWith("rail_workspace_v1:"));
      return key && JSON.parse(sessionStorage.getItem(key)).page==="分析报告" &&
        new URL(location.href).searchParams.get("rail_resume")==="1";
    });
    await page.reload();
    await page.getByRole("heading",{name:"分析报告",exact:true}).waitFor();
    await page.getByText(new RegExp(`历史导入 · 车辆 DEMO-001 · 批次 #${batch}$`)).waitFor();
    assert.equal(await page.getByText(/标定复核将附入 PDF/).count(),0);
    await continueTo("异常检测");
    await page.locator(".st-key-run_analysis_bogie_jump button").click();
    await page.getByText("已定位到所选统计证据的异常样本。",{exact:true}).waitFor();
    await page.getByText(/CSV 原始起始行 32 · lateral_accel/).waitFor();
    await page.locator(".st-key-bogie_return_analysis button").click();
    await click("在 Copilot 中分析");
    const chat=page.getByTestId("stChatInputTextArea");
    await chat.fill("查看传感器坐标标定和安装位置");
    await chat.press("Enter");
    await page.getByText(/已读取当前车辆和批次的传感器版本/).waitFor();
    await page.frameLocator("iframe[title*=bogie_viewer]").last().locator("#sensor-overlay text")
      .filter({hasText:"LAB-A-01"}).waitFor();
    // A revised fictional frame mount enables only applicable rigid-frame projections.
    await nav("运行分析");
    await openSensors("analysis");
    if (!await input("独立参考记录编号").isVisible())
      await page.getByText("用独立参考数据复核标定",{exact:true}).click();
    await submitReference();
    await page.getByText(/所选参考点在设定误差内/).waitFor();
    await nav("数据导入");
    await choose("打开历史批次",batchLabel);
    await openSensors("history");
    await choose("安装部件","左侧 侧梁 · side_beam_l");
    await input("安装位置 [x,y,z]（m）").fill("[0,-1.16,0.98]");
    await click("保存配置版本");
    await page.getByText("已保存传感器配置版本",{exact:true}).waitFor();
    await page.getByText("查看修改记录",{exact:true}).waitFor();
    await click("分析历史批次 →");
    await continueTo("分析报告");
    await page.getByText(/最近一次标定复核与当前批次、范围或配置版本不一致/).waitFor();
    assert.equal(await page.getByText(/标定复核将附入 PDF/).count(),0);
    assert.equal(await page.getByRole("button",{name:"下载 PDF 报告",exact:true}).count(),0);
    await nav("运行分析");
    await openSensors("analysis");
    await click("按此测量轴查看仿真响应");
    await page.getByText("沿登记测量轴的计算响应",{exact:true}).waitFor();
    const projection=await downloadJson("下载投影计算依据");
    assert.equal(projection.configuration.version,2);
    assert.equal(projection.source_sha256,sourceHash);
    assert.equal(projection.data_layer,"simulation");
    assert.equal(projection.time_basis,"relative_seconds");
    assert.equal(projection.axis_accel_m_s2.length,601);
    assert(projection.axis_accel_m_s2.every(v=>v===0)); // +Y is orthogonal to the calculated +Z acceleration.
    await page.getByText("沿登记测量轴的计算响应",{exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"sensor-projection.png")});
    const residual = page.getByText("参考轴向数据与仿真残差",{exact:true});
    await residual.click();
    await choose("参考来源类别","虚构参考信号");
    await input("参考记录编号或说明").fill("合成参考测点");
    await input("相对时间零点与配对依据").fill("共同虚构时间零点");
    await input("相对时间零点与配对依据").press("Tab");
    const referenceFile = page.getByTestId("stFileUploader").filter({hasText:"已配对的参考 CSV"}).locator("input");
    const header = "model_time_s,reference_axis_m_s2\n";
    await referenceFile.setInputFiles({name:"invalid-pairs.csv",mimeType:"text/csv",
      buffer:Buffer.from(header+"0,1\n0.005,1\n0.02,1\n")});
    await page.getByText("invalid-pairs.csv",{exact:true}).waitFor();
    await click("计算残差");
    await page.getByText(/第 3 行需要输出网格/).waitFor();
    await referenceFile.setInputFiles({name:"synthetic-pairs.csv",mimeType:"text/csv",
      buffer:Buffer.from(header+"0,1\n0.01,1\n0.02,1\n")});
    await page.getByText("synthetic-pairs.csv",{exact:true}).waitFor();
    await click("计算残差");
    const comparison = await downloadJson("导出验证记录 JSON");
    assert.equal(comparison.batch_id,batch);
    assert.equal(comparison.configuration_version,2);
    assert.equal(comparison.metrics.pairs,3);
    assert.equal(comparison.metrics.rmse_m_s2,1);
    assert.equal(comparison.independent_measurement,"pending");
    await page.getByRole("button",{name:"导出验证记录 JSON",exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,"phase8-residual-validation.png")});
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log("CSV import and rejection, missing evidence, wrong digest, v1/v2 mismatch, 20 ms rejection, PDF, refresh, exact replay, offline Copilot and 601 calculated projections passed.");
  } catch(e) {await page.screenshot({path:resolve(out,"failure.png")});throw e;}
  finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
