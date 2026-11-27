// First-screen visual acceptance using the runner's isolated, fictional database.
const assert = require("node:assert/strict");
const {mkdir} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const [address, output, revision] = process.argv.slice(2);
const baseline = revision === "baseline";
const routes = [
  ["home","平台首页"], ["vehicles","车辆参数"], ["data","CSV 数据导入"],
  ["analysis","运行分析"], ["events","异常检测"], ["compare","批次对比"],
  ["studio","三维转向架仿真"], ["simulation","动力学仿真"], ["linked","联动分析"],
  ["copilot","RailVehicle Copilot"], ["report","分析报告"], ["evidence","AI 智能分析中心"],
  ["spectrum","振动与平稳性分析"], ["fleet","多车辆运行对比"], ["ml","机器学习异常检测"],
  ["cockpit","动态运行驾驶舱"], ["demo","演示中心"], ["demo-analysis","统计与异常演示"],
];
async function checkColdStart(browser, colorScheme) {
  const page = await browser.newPage({colorScheme});
  const base = new URL(address);
  assert.equal(base.hostname,"127.0.0.1");
  const entries = [];
  page.on("request",r => {
    if (r.isNavigationRequest() && r.frame() === page.mainFrame()) entries.push(r.url());
  });
  try {
    await page.goto(base.href);
    await page.getByTestId("stSidebar").getByRole("button",{name:/AI Copilot$/}).click();
    await page.getByRole("heading",{name:"RailVehicle Copilot",exact:true}).waitFor();
    await page.waitForFunction(() => document.documentElement.style.getPropertyValue("--rv-surface") === "#1C293C" &&
      getComputedStyle(document.querySelector('[data-testid="stApp"]')).backgroundColor === "rgb(11, 18, 32)");
    if (colorScheme === "dark") assert.equal(entries.length,1,"The active dark palette unnecessarily reloaded");
    else assert(entries.length <= 2,"The initial palette caused a reload loop");
    const light = new URL(base);
    light.searchParams.set("embed_options","light_theme");
    await page.goto(light.href);
    await page.waitForFunction(() => localStorage.getItem("rail_vehicle_theme") === "light");
    const vehicle = new URL(base);
    vehicle.searchParams.set("rail_page","车辆管理");
    await page.goto(vehicle.href);
    await page.getByRole("heading",{name:"车辆参数",exact:true}).waitFor();
    await page.waitForFunction(() => document.documentElement.style.getPropertyValue("--rv-surface") === "#FFFFFF" &&
      getComputedStyle(document.querySelector('[data-testid="stApp"]')).backgroundColor === "rgb(241, 245, 250)");
    console.log(colorScheme+" desktop cold start retained the first navigation and restored light on a deep link.");
  } catch (error) {
    await page.screenshot({path:resolve(output,colorScheme+"-cold-start-failure.png")});
    throw error;
  } finally {await page.close();}
}
async function capture(browser,width,height,theme) {
  const page = await browser.newPage({viewport:{width,height}});
  const errors = [];
  page.on("pageerror", e=>errors.push(e.message));
  let current;
  try {
    const url = new URL(address);
    assert.equal(url.hostname,"127.0.0.1");
    url.searchParams.set("embed_options",theme+"_theme");
    await page.goto(url.href);
    const names = {"平台首页":"工作台","车辆参数":"车辆管理","CSV 数据导入":"数据管理",
      "三维转向架仿真":"三维可视化","RailVehicle Copilot":"工程 Copilot","AI 智能分析中心":"AI 智能分析",
      "统计与异常演示":"虚构数据分析","振动与平稳性分析":"振动与频谱"};
    const sidebar = page.getByTestId("stSidebar");
    if (!baseline) Object.assign(names, {"平台首页":"工作台首页","CSV 数据导入":"数据导入",
      "三维转向架仿真":"三维转向架","RailVehicle Copilot":"AI Copilot","AI 智能分析中心":"AI 证据解读"});
    for (const [name,route] of routes) {
      current=name;
      const oldMode = baseline && ["linked","simulation"].includes(name);
      const main = page.getByTestId("stMainBlockContainer");
      const label = oldMode ? "三维可视化" : names[route] || route;
      const nav = sidebar.getByRole("button",{name:new RegExp(label+"$")});
      if (["fleet","ml","cockpit","demo","demo-analysis"].includes(name) && !await nav.isVisible())
        await sidebar.getByText("专项工具",{exact:true}).click();
      // Enter chat from the overview to compare the default, unbound entry.
      if (name === "copilot") {
        await sidebar.getByRole("button",{name:new RegExp(names["平台首页"]+"$")}).click();
        await main.getByRole("heading",{name:"工程工作台",exact:true}).waitFor();
      }
      await nav.click();
      await nav.and(page.getByTestId("stBaseButton-primary")).waitFor();
      const titles={home:"工程工作台",studio:"三维诊断工作台",evidence:"AI 分析中心","demo-analysis":"虚构数据分析"};
      await main.getByRole("heading",{name:oldMode?"三维诊断工作台":titles[name] || route,exact:true}).first().waitFor();
      if (oldMode) await main.getByText(name==="linked"?"历史运行批次":"简化动力学计算",{exact:true}).first().click();
      if (name==="copilot") {
        const input=page.getByTestId("stChatInputTextArea");
        await input.waitFor();
        if (!baseline) {
          const box=await input.boundingBox();
          assert(box && box.y>=0 && box.y+box.height<=height,"Composer outside first screen");
        }
      }
      await page.getByRole("button",{name:"Stop",exact:true}).waitFor({state:"hidden"});
      await main.locator('[data-stale="true"]').first().waitFor({state:"detached"});
      if (!baseline && ["home","analysis","events"].includes(name)) {
        const key = {home:"home_speed",analysis:"chart_run_unified",events:"anomaly_review_chart"}[name];
        await page.waitForFunction(chartKey => {
          const chart = document.querySelector(`.st-key-${chartKey} .js-plotly-plot`);
          return chart?.data?.length && chart.querySelector(".main-svg") && !chart.closest('[data-stale="true"]');
        }, key);
      }
      if (!baseline) {
        const active = sidebar.locator('[class*="st-key-nav_"] button[data-testid="stBaseButton-primary"]');
        assert.equal(await active.count(),1,`Expected one active route in ${name}`);
      }
      assert.equal(await page.getByTestId("stException").count(),0,name);
      assert(await main.evaluate(n=>n.scrollWidth<=n.clientWidth+1),name);
      await main.evaluate(n=>{ for(let x=n;x;x=x.parentElement) if(x.scrollTop) x.scrollTop=0; });
      // Streamlit can briefly retain the preceding iframe during a route change.
      if (["studio","simulation","linked"].includes(name)) {
        const model=page.frameLocator("iframe[title*=bogie_viewer]").first();
        if (name==="simulation") await model.locator("#response:not([hidden])").waitFor();
        if (name==="linked") await main.getByRole("heading",{name:"观测读数",exact:true}).waitFor();
        await model.locator("#workbench[data-model=glb][data-status=ready][data-frames] #message[hidden]").waitFor({state:"attached"});
      }
      await main.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);});
      await page.screenshot({path:resolve(output,width+"-"+theme+"-"+name+".png"),animations:"disabled"});
    }
    await sidebar.getByRole("button",{name:/设置$/}).click();
    const themeLink=page.getByRole("link",{name:/浅色/});
    await themeLink.waitFor();
    assert((await themeLink.getAttribute("href")).includes("light_theme"));
    await page.screenshot({path:resolve(output,width+"-"+theme+"-settings.png"),animations:"disabled"});
    assert.deepEqual(errors,[]);
    console.log(width,theme,routes.length,"routes passed");
  } catch(error) {
    await page.screenshot({path:resolve(output,width+"-"+theme+"-"+current+"-failure.png")});
    throw new Error(width+"/"+theme+"/"+current+" "+error.message);
  } finally {await page.close();}
}
async function checkMobile(browser, theme) {
  const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const main = page.getByTestId("stMainBlockContainer");
  const expand = page.getByTestId("stExpandSidebarButton");
  const url = new URL(address);
  url.searchParams.set("embed_options",theme+"_theme");
  const expected = new Map([
    ["home","工程工作台"],["vehicles","车辆参数"],["data","CSV 数据导入"],
    ["analysis","运行分析"],["events","异常检测"],["studio","三维诊断工作台"],
    ["simulation","动力学仿真"],["copilot","RailVehicle Copilot"],["report","分析报告"],
  ]);
  let current = "home";
  try {
    for (const [name,route] of routes.filter(([name])=>expected.has(name))) {
      current = name;
      url.searchParams.set("rail_page",route);
      await page.goto(url.href);
      await main.getByRole("heading",{name:expected.get(name),exact:true}).first().waitFor();
      await expand.waitFor();
      await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
      assert.equal(await page.getByTestId("stException").count(),0,name);
      assert(await main.evaluate(node=>node.scrollWidth<=node.clientWidth+1),`${name} overflows`);
      if (name === "home") {
        const metrics = main.locator(".st-key-rv-home-kpis [data-testid='stColumn']");
        const boxes = await metrics.evaluateAll(items=>items.map(node=>node.getBoundingClientRect().toJSON()));
        assert.equal(boxes.length,4);
        assert(boxes[0].x < boxes[1].x && boxes[0].top === boxes[1].top && boxes[2].top > boxes[0].top);
      }
      if (name === "copilot") {
        const box = await main.getByTestId("stChatInputTextArea").boundingBox();
        assert(box && box.y+box.height < 844,"Mobile Copilot composer outside first screen");
      }
      if (["studio","simulation"].includes(name)) {
        const model=page.frameLocator("iframe[title*=bogie_viewer]").first();
        await model.locator('#workbench[data-model="glb"][data-status="ready"]').waitFor();
      }
      await page.screenshot({path:resolve(output,`390-${theme}-${name}.png`),animations:"disabled"});
    }
    await expand.click();
    const sidebar = page.getByTestId("stSidebar");
    await sidebar.getByRole("button",{name:/设置$/}).click();
    await page.getByRole("link",{name:/浅色/}).waitFor();
    await page.screenshot({path:resolve(output,`390-${theme}-settings.png`),animations:"disabled"});
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log(390,theme,expected.size,"mobile routes and settings passed");
  } catch(error) {
    await page.screenshot({path:resolve(output,`390-${theme}-${current}-failure.png`)});
    throw error;
  } finally {await page.close();}
}
(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    if (!baseline) {
      await checkColdStart(browser,"dark");
      await checkColdStart(browser,"light");
    }
    for (const theme of ["dark","light"]) {
      const results=await Promise.allSettled([[1366,768],[1920,1080]].map(([w,h])=>capture(browser,w,h,theme)));
      for(const result of results) if(result.status==="rejected") throw result.reason;
      if (!baseline) await checkMobile(browser,theme);
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
