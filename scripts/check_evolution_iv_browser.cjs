const assert = require("node:assert/strict");
const {mkdir, readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const address=new URL(process.argv[2]);
assert.equal(address.hostname,"127.0.0.1");
address.searchParams.set("rail_page","数字孪生工作台");
const output=resolve(process.argv[3]);

async function download(page,name) {
  const ready=page.waitForEvent("download");
  await page.getByRole("button",{name,exact:true}).click();
  return readFile(await (await ready).path());
}

async function audit(browser,width,height,theme) {
  const page=await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/*",route=>new URL(route.request().url()).origin===address.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture=name=>page.screenshot({path:resolve(output,`${width}-${theme}-${name}.png`)});
  const url=new URL(address);
  url.searchParams.set("embed_options",theme+"_theme");
  try {
    await page.goto(url.href);
    await page.getByText(/离线虚构演示/).waitFor();
    const frame=()=>page.frameLocator('iframe[title*="bogie_viewer"]').first();
    const svg=frame().locator('#observation-path[d^="M"]');
    await svg.waitFor({state:"attached"});
    assert((await svg.getAttribute("d")).length>300);
    await frame().locator("#lateral-peak").click();
    assert(+await frame().locator("#sim-time").inputValue()>1);
    await frame().locator("#response").scrollIntoViewIfNeeded();
    await capture("paired-observations");
    if (width===1366) {
      await page.getByText("下载虚构原始记录",{exact:true}).click();
      const fields=[["tw_train_csv","辨识观测 CSV","train.csv"],["tw_train_meta","辨识元数据 JSON","train.json"],
        ["tw_train_case","辨识动力学工况 JSON","train-case.json"],["tw_val_csv","验证观测 CSV","valid.csv"],
        ["tw_val_meta","验证元数据 JSON","valid.json"],["tw_val_case","验证动力学工况 JSON","valid-case.json"]];
      for (const field of fields) field.push(await download(page,field[1]));
      await page.getByText("工程记录",{exact:true}).click();
      for (const [key,,name,buffer] of fields) {
        await page.locator(`.st-key-${key} input[type=file]`).setInputFiles({name,mimeType:name.endsWith(".csv")?"text/csv":"application/json",buffer});
      }
      await page.getByRole("button",{name:"核对并绑定观测",exact:true}).click();
      await page.getByRole("button",{name:"解除配对记录绑定",exact:true}).waitFor();
      await page.getByRole("heading",{name:"观测与计算回放",exact:true}).waitFor();
      await page.getByText("虚构试验",{exact:true}).click();
    }

    await page.getByRole("button",{name:"将当前观测交给 Copilot",exact:true}).click();
    await page.getByText(/已连接数字孪生工作台/).waitFor();
    const chat=page.getByTestId("stChatInputTextArea");
    await chat.fill("请校准当前车辆动力学模型，二系横向刚度 85–260 kN/m，侧滚不超过 4 mrad，完成参数优化、复算和报告。");
    await chat.press("Enter");
    await page.getByRole("button",{name:"生成可审查计划",exact:true}).click();
    await page.getByText(/等待批准/).waitFor();
    await page.getByText(/侧滚上限 4 mrad/).waitFor();
    assert.equal(await page.getByRole("heading",{name:"工程任务结果",exact:true}).count(),0);
    await page.getByRole("button",{name:"批准并执行工程任务",exact:true}).click();
    await page.getByRole("heading",{name:"工程任务结果",exact:true}).waitFor({timeout:90000});
    await page.getByText(/独立验证归一化 RMSE/).waitFor();
    const archive=JSON.parse((await download(page,"下载工程证据 JSON")).toString());
    assert.equal(archive.identification.rank,3);
    assert(archive.identification.validation_normalized_rmse<.05);
    assert.equal(archive.optimization.plan.max_roll_mrad,4);
    assert.deepEqual(archive.optimization.plan.bounds.secondary_y_k_n_m,[85000,260000]);
    assert(archive.optimization.pareto.length>0);
    assert(archive.optimization.pareto.every(row=>row.substeps===10 && !row.violations.length));
    assert.equal((await download(page,"下载工程任务 PDF")).subarray(0,5).toString(),"%PDF-");
    const view=frame().locator('#workbench[data-vehicle-overview="true"]');
    await view.waitFor();
    await frame().locator("#twin-panes:not([hidden])").waitFor();
    await frame().locator("#lateral-peak").click();
    const currentTime=+await frame().locator("#sim-time").inputValue();
    await view.evaluate(node=>new Promise((done,fail)=>{
      const end=performance.now()+10000;
      function check() {
        const body=Number(node.dataset.vehicleBody?.split(",")[2]);
        const other=Number(node.dataset.twinAlternative);
        if (Number.isFinite(body) && Number.isFinite(other) && Math.abs(body-other)>1e-8 && +node.dataset.triangles>1000) return done();
        if (performance.now()>end) return fail(Error("比较两侧未绘制所对应的不同姿态"));
        requestAnimationFrame(check);
      }
      check();
    }));
    const cursor=frame().locator("#response-cursor");
    assert(Math.abs(+await cursor.getAttribute("x1")-1000*currentTime/(+await frame().locator("#sim-time").getAttribute("max")))<1e-6);
    await frame().locator("#vehicle-shell").click();
    await frame().locator("#vehicle-heat").click();
    await frame().locator("#twin-heat-note:not([hidden])").waitFor();
    await frame().locator("#surface").scrollIntoViewIfNeeded();
    await capture("pareto-3d");
    await page.getByRole("button",{name:"归档工程任务",exact:true}).click();
    await page.getByText("工程任务已归档。",{exact:true}).waitFor();
    await capture("archived-task");
    if (width===1366) {
      // Restore on a completely fresh WebSocket session.
      const fresh=await browser.newPage({viewport:{width,height}});
      try {
        await fresh.goto(url.href);
        await fresh.getByText("审批与任务",{exact:true}).click();
        await fresh.getByText("历史工程任务",{exact:true}).click();
        await fresh.getByRole("button",{name:"恢复归档并准备复算",exact:true}).click();
        await fresh.getByRole("button",{name:"批准并执行工程任务",exact:true}).click();
        await fresh.getByText("归档复算摘要一致。",{exact:true}).waitFor({timeout:90000});
      } finally { await fresh.close(); }
    }
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(`${width}x${height} ${theme}: observation timestamps, Copilot approval, independent fit, Pareto split, evidence/PDF, archival passed.`);
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
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
