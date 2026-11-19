const assert = require("node:assert/strict");
const {mkdir} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const url = new URL(process.argv[2]), output = resolve(process.argv[3]);
assert.equal(url.origin, "http://127.0.0.1:" + url.port);
url.searchParams.set("rail_page", "三维转向架仿真");
async function audit(browser, width, height) {
  const page = await browser.newPage({viewport:{width,height}, locale:"en-US",timezoneId:"UTC"});
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => new URL(route.request().url()).origin === url.origin ||
    /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture = name => page.screenshot({path: resolve(output, width+"-"+name+".png")});
  try {
    await page.goto(url.href);
    await page.getByTestId("stSidebar").getByRole("button",{name:/三维可视化$/}).click();
    const source = page.locator(".st-key-bogie_source");
    await source.getByText("简化动力学计算",{exact:true}).click();
    const frame = page.frameLocator('iframe[title*=bogie_viewer]').first();
    await frame.locator('#workbench[data-status=ready][data-model=glb]').waitFor();
    const full = frame.locator("#full-screen");
    await full.click();
    await frame.locator("#workbench:fullscreen").waitFor();
    await full.click();
    await frame.locator("#workbench:fullscreen").waitFor({state:"detached"});
    await frame.locator("#response-plot").click({position:{x:130,y:25}});
    const sample = await frame.locator("#response").getAttribute("data-sample");
    assert(+sample > 0);
    assert.equal(await frame.locator("#sim-time").inputValue(),sample);
    const seek = frame.locator("#sim-time");
    await seek.focus();
    await seek.press("ArrowRight");
    assert.equal(+(await seek.inputValue()),+sample+1);
    await frame.locator("#sim-play").click();
    const root = frame.locator("#workbench");
    const startFrames = Number(await root.getAttribute("data-frames"));
    const started = Date.now();
    await page.waitForTimeout(1000);
    const fps = (Number(await root.getAttribute("data-frames"))-startFrames)*1000/(Date.now()-started);
    await frame.locator("#sim-play").click();
    assert(+(await seek.inputValue()) > +sample+1);
    const paused = await seek.inputValue();
    await page.waitForTimeout(150);
    assert.equal(await seek.inputValue(),paused);
    const travelMm = Number((await frame.locator("#sim-values").innerText()).match(/行程 ([-.\d]+) mm/)[1]);
    const vertical = Number((await root.getAttribute("data-pose")).split(",")[1]);
    assert(Math.abs(vertical*1000 - travelMm) < .06, "三维行程与当前计算样本不符");
    const modes = [];
    for (const mode of ["wire","ghost","solid"]) {
      await frame.locator("#surface-mode").selectOption(mode);
      modes.push(await frame.locator("canvas").first().screenshot());
    }
    assert(!modes[0].equals(modes[2]) && !modes[1].equals(modes[2]), "显示模式未改变模型画面");
    await frame.locator("#browse-parts").click();
    await frame.locator("#part-query").fill("damper_a_l");
    await frame.locator(".part-row:visible").first().click();
    await frame.locator('#workbench[data-selected="damper_a_l"]').waitFor();
    await frame.locator("#isolate-part").click();
    await frame.locator('#workbench[data-isolated="damper_a_l"]').waitFor();
    await frame.locator("#isolate-part").click();
    await frame.locator("#browse-parts").click();
    await frame.locator("#reset").click();
    await capture("simulation");
    await frame.locator("#sim-reset").click();
    assert.equal(await seek.inputValue(),"0");
    await page.locator(".st-key-sim_case input").click();
    await page.getByRole("option",{name:"悬挂刚度降低",exact:true}).click();
    await frame.getByText(/虚线：名义刚度/).waitFor();
    assert((await frame.locator("#nominal-path").getAttribute("d")).startsWith("M"));
    await source.getByText("内置虚构演示",{exact:true}).click();
    const plot = page.locator(".st-key-bogie_curves .js-plotly-plot");
    const point = plot.locator(".scatterlayer .trace").first().locator("path.point").nth(15);
    await point.scrollIntoViewIfNeeded();
    const box = await point.boundingBox();
    await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
    await page.locator(".st-key-bogie_status_card").getByText(/第 16\/120 条/).waitFor();
    await frame.locator("#browse-parts").click();
    await frame.locator("#part-query").fill("coil_a_l");
    await frame.locator(".part-row:visible").first().click();
    await frame.locator("#channel-context").filter({hasText:/第 16 行.*垂向.*安装点未知/}).waitFor();
    await frame.locator("#part-query").fill("motor_a");
    await frame.locator(".part-row:visible").first().click();
    await frame.getByText("此批次无该部件专用通道",{exact:true}).waitFor();
    await frame.locator("#browse-parts").click();
    await page.locator(".st-key-bogie_seek").getByRole("slider").focus();
    await page.keyboard.press("End");
    await page.locator(".st-key-bogie_status_card").getByText(/第 120\/120 条/).waitFor();
    await page.waitForFunction(() => {
      const series = document.querySelector(".st-key-bogie_curves .js-plotly-plot")?.data?.[0];
      return series?.customdata?.at(-1) === 119;
    });
    await plot.evaluate(node => node.scrollIntoView({block:"center"}));
    await capture("observations");
    await page.getByTestId("stSidebar").getByRole("button",{name:/振动与频谱$/}).click();
    await page.locator(".st-key-vibration_source").getByText("内置高频虚构信号",{exact:true}).click();
    await page.locator(".st-key-vibration_calculate button").click();
    await page.locator(".st-key-vibration_spectrum").getByText("单边 FFT 幅值谱",{exact:true}).click();
    await page.locator(".st-key-vibration_fft .main-svg").first().evaluate(node => node.scrollIntoView({block:"center"}));
    await capture("fft");
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,height,sample:+sample,paused:+paused,observedRows:[16,120],softwareFps:Math.round(fps)}));
  } catch (e) { await capture("failure"); throw e; } finally { await page.close(); }
}
(async () => {
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true,
    args:["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try { for (const [w,h] of [[1366,768],[1920,1080]]) await audit(browser,w,h); }
  finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
