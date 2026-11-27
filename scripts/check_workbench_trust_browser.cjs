// End-to-end verification against the disposable browser runner database.
const assert = require("node:assert/strict");
const {mkdir} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
const output = resolve(process.argv[3]);
assert.equal(address.hostname,"127.0.0.1");

(async () => {
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH, headless:true,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  let page;
  try {
    const context = await browser.newContext({viewport:{width:1366,height:768}});
    page = await context.newPage();
    const errors = [];
    page.on("pageerror", error=>errors.push(error.message));
    await context.route("**/*", route=>new URL(route.request().url()).origin===address.origin ||
      /^(blob:|data:)/.test(route.request().url()) ? route.continue():route.abort());
    const nav = label=>page.getByTestId("stSidebar").getByRole("button",{name:new RegExp(label+"$")}).click();
    const chart = key=>page.locator(".st-key-"+key+" .js-plotly-plot");
    const testAxis = async key=>{
      const plot = chart(key);
      await plot.waitFor();
      const evidence = await plot.evaluate(node=>({
        sameAxis:node.data.every(trace=>trace.xaxis==="x"),
        hover:node.layout.hovermode, source:node.data[2].text[0],
        values:node.data[2].y, bands:node.layout.shapes.filter(shape=>shape.fillcolor==="#CF8C39").length
      }));
      assert.equal(evidence.sameAxis,true);
      assert.equal(evidence.hover,"x unified");
      assert.match(evidence.source,/采样 ID \d+ · CSV 第 \d+ 行/);
      assert(evidence.values.every(value=>Math.abs(value)>1));
      return evidence;
    };
    const threshold = () => page.locator(".st-key-analysis_threshold input");
    const record = (last = 120) => page.locator("[class*='st-key-home_history_']")
      .filter({hasText:/批次 #2 · 来源未确认.*1\.2 m\/s²/})
      .filter({hasText:`第 1–${last} 条观测`});
    const restore = async filename => {
      await record().waitFor();
      await record().getByRole("button",{name:"恢复分析范围",exact:true}).click();
      await page.getByRole("heading",{name:"运行分析",exact:true}).waitFor();
      assert.equal(Number(await threshold().inputValue()),1.2);
      await testAxis("chart_run_unified");
      await chart("chart_run_unified").scrollIntoViewIfNeeded();
      await page.screenshot({path:resolve(output,filename)});
      assert.deepEqual(errors,[]);
    };
    await page.goto(address.href);
    await page.getByRole("heading",{name:"工程工作台"}).waitFor();
    if (process.env.RAIL_WORKBENCH_RESTART === "1") {
      await record(76).waitFor();
      await restore("analysis-after-service-restart.png");
      console.log("Fresh Edge and restarted Streamlit restored the same SQLite analysis.");
      return;
    }
    await page.getByText(/批次 #2 · 来源未确认 · [\d,]+ 个采样点/).waitFor();
    await testAxis("home_speed");
    const point = chart("home_speed").locator(".scatterlayer .trace").first().locator("path.point").nth(12);
    await point.hover({force:true});
    await page.waitForFunction(()=>new Set(document.querySelector(".st-key-home_speed .js-plotly-plot")
      ?._hoverdata?.map(point=>point.data.yaxis)).size===3);
    const event = page.getByRole("combobox",{name:"高亮区段",exact:true});
    await event.click();
    await page.getByRole("option",{name:"#1",exact:true}).click();
    const locate = page.getByRole("button",{name:"在三维中定位该区段",exact:true});
    await locate.waitFor();
    await page.waitForFunction(()=>document.querySelector(".st-key-home_speed .js-plotly-plot")?.layout?.shapes
      .filter(s=>s.fillcolor==="#CF8C39" || s.line?.color==="#CF8C39" && s.line?.width===2).length===3);
    await page.screenshot({path:resolve(output,"home-event.png")});
    await locate.click();
    await page.getByText("已定位到所选统计证据的异常样本。",{exact:true}).waitFor();
    await nav("工作台首页");
    await page.locator(".st-key-home_latest_analysis button").click();
    await testAxis("chart_run_unified");
    await threshold().fill("1.2");
    await threshold().press("Enter");
    await page.getByText(/用户自定义阈值.*当前批次无已核验标准限值/).waitFor();
    await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    await page.waitForFunction(() => new URL(location.href).searchParams.get("rail_resume") === "1" &&
      Object.keys(sessionStorage).some(key => key.startsWith("rail_workspace_v1:") &&
        JSON.parse(sessionStorage.getItem(key)).threshold === 1.2));
    await page.reload();
    await page.getByRole("heading",{name:"运行分析",exact:true}).waitFor();
    await page.waitForFunction(() => Number(document.querySelector(".st-key-analysis_threshold input")?.value) === 1.2);
    // Prove SQLite recovery independently of the existing tab checkpoint.
    await page.close();
    page = await context.newPage();
    page.on("pageerror", error=>errors.push(error.message));
    await page.goto(address.href);
    await page.getByRole("heading",{name:"工程工作台",exact:true}).waitFor();
    await record().waitFor();
    await page.screenshot({path:resolve(output,"home-restored-history.png")});
    await restore("analysis-restored.png");
    console.log("Source label, exact CSV provenance, shared highlights, replay, and restored SQLite analysis passed.");
    await page.locator(".st-key-analysis_clear_replay_scope button").click();
    await page.getByText("分析时间范围（UTC） · 调整筛选",{exact:true}).click();
    const seconds = page.locator('[class*="st-key-analysis_end_time_"]').getByRole("spinbutton",{name:/second|秒/i});
    await seconds.press("1");
    await seconds.press("5");
    await seconds.press("Tab");
    await page.getByText(/分析样本数：.*76 条/).waitFor();
    await nav("动力学仿真");
    await page.getByRole("heading",{name:"动力学仿真",exact:true}).waitFor();
    await page.locator(".st-key-simulation_return_analysis button").click();
    await page.getByText(/分析样本数：.*76 条/).waitFor();
    await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    await testAxis("chart_run_unified");
    const chartEvent = page.locator("[class*='st-key-analysis_chart_event_']");
    const firstOption = page.getByRole("option",{name:"#1",exact:true});
    await chartEvent.evaluate(node=>node.scrollIntoView({block:"center",behavior:"instant"}));
    await chartEvent.getByRole("button",{name:"Open",exact:true}).click();
    if (!await firstOption.waitFor({timeout:2000}).then(()=>true,()=>false)) {
      await chartEvent.getByRole("combobox").press("ArrowDown");
    }
    await firstOption.click();
    await page.locator(".st-key-analysis_chart_to_bogie button").click();
    await page.getByText("已定位到所选统计证据的异常样本。",{exact:true}).waitFor();
    await page.locator(".st-key-bogie_window_copilot button").click();
    await page.getByRole("heading",{name:"RailVehicle Copilot",exact:true}).waitFor();
    await page.getByText(/当前窗口 · 批次第 1–76 条观测/).waitFor();
    await page.getByText(/demo_run_2\.csv.*76 行.*阈值 1\.2 m\/s²/).waitFor();
    await page.screenshot({path:resolve(output,"copilot-window.png")});
    await page.locator(".st-key-cp_window_report button").click();
    await page.getByText(/报告范围：批次第 1–76 条观测/).waitFor();
    await page.locator(".st-key-generate_analysis_report button").click();
    await page.getByRole("button",{name:"下载 PDF 报告",exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log("76-row scope and 1.2 m/s² threshold survived simulation return, replay, Copilot and PDF generation.");
  } catch (error) {
    await page?.screenshot({path:resolve(output,"failure.png")}).catch(()=>{});
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
