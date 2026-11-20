const assert = require("node:assert/strict");
const {mkdir,writeFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const url = new URL(process.argv[2]), output = resolve(process.argv[3]);
assert.equal(url.origin, "http://127.0.0.1:" + url.port);
url.searchParams.set("rail_page", "三维转向架仿真");
async function captureRenderedModel(page, path) {
  const deadline = Date.now()+5000;
  do {
    const png = await page.screenshot();
    const visible = await page.evaluate(async base64 => {
      const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
      const image = await createImageBitmap(new Blob([bytes],{type:"image/png"}));
      const context = new OffscreenCanvas(100,80).getContext("2d");
      // Sample the middle of the viewport, excluding toolbars, controls and the response plot.
      context.drawImage(image,image.width*.2,image.height*.2,image.width*.6,image.height*.45,0,0,100,80);
      image.close();
      const pixels = context.getImageData(0,0,100,80).data;
      let steelPixels = 0;
      for (let i=0; i<pixels.length; i+=4)
        if (pixels[i]>65 && pixels[i+1]>65 && pixels[i+2]>65) steelPixels++;
      return steelPixels > 400;
    },png.toString("base64"));
    if (visible) { await writeFile(path,png); return; }
  } while (Date.now()<deadline);
  throw new Error("全屏截图中未显示转向架");
}
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
    await frame.locator("#response:not([hidden])").waitFor();
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
    await page.locator(".st-key-sim_case input").click();
    await page.getByRole("option",{name:"双轴先后通过局部不平顺",exact:true}).click();
    await frame.locator("#axle-controls").waitFor();
    const signal = frame.locator("#axle-signal");
    const values = frame.locator("#sim-values");
    for (const [crest, expectedSample] of [["front-crest","58"],["rear-crest","102"]]) {
      await frame.locator("#"+crest).click();
      assert.equal(await seek.inputValue(), expectedSample);
      const [,front,rear] = (await values.innerText()).match(/行程\s+B 端 ([-.\d]+) mm\s+A 端 ([-.\d]+) mm/);
      const tilt = (Number(front)-Number(rear))/2600;
      await root.evaluate((node, expected) => new Promise((resolve,reject) => {
        const deadline = performance.now()+3000;
        function check() {
          if (Math.abs(Math.sin(Number(node.dataset.framePitch))-expected) < 5e-6) resolve();
          else if (performance.now() >= deadline) reject(Error("两端行程与三维姿态不一致"));
          else requestAnimationFrame(check);
        }
        check();
      }), tilt);
    }
    const travelPath = await frame.locator("#rear-path").getAttribute("d");
    await signal.selectOption("support");
    assert.equal(await seek.inputValue(),"102");
    assert.match(await values.innerText(),/轨面输入\s+B 端 0\.00 mm\s+A 端 3\.98 mm/);
    assert.notEqual(await frame.locator("#rear-path").getAttribute("d"),travelPath);
    assert.notEqual(await frame.locator("#rear-path").getAttribute("d"),
                    await frame.locator("#response-path").getAttribute("d"));
    await signal.selectOption("travel");
    const frontLabel = frame.locator("#axle-labels .front"), rearLabel = frame.locator("#axle-labels .rear");
    await frontLabel.waitFor();
    await rearLabel.waitFor();
    await frame.locator("#view").selectOption("正视图");
    await rearLabel.waitFor({state:"hidden"});
    assert(await frontLabel.isVisible());
    await frame.locator("#view").selectOption("整体视图");
    await rearLabel.waitFor();
    await full.click();
    await frame.locator("#workbench:fullscreen").waitFor();
    await captureRenderedModel(page,resolve(output,width+"-axle-pass.png"));
    await full.click();
    await frame.locator("#workbench:fullscreen").waitFor({state:"detached"});
    await frame.locator("#sim-rate").selectOption(".5");
    await frame.locator("#sim-play").click();
    const axleFrames = Number(await root.getAttribute("data-frames"));
    const axleStarted = Date.now();
    await page.waitForTimeout(1000);
    const axleFps = Math.round((Number(await root.getAttribute("data-frames"))-axleFrames)*1000/(Date.now()-axleStarted));
    await frame.locator("#sim-play").click();
    const axlePaused = await seek.inputValue();
    assert(Number(axlePaused)>102 && axleFps>0);
    await page.waitForTimeout(150);
    assert.equal(await seek.inputValue(),axlePaused);
    await frame.locator("#sim-reset").click();
    assert.equal(await seek.inputValue(),"0");
    await source.getByText("内置虚构演示",{exact:true}).click();
    await frame.locator("#axle-labels").waitFor({state:"hidden"});
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
    console.log(JSON.stringify({width,height,sample:+sample,paused:+paused,observedRows:[16,120],
      softwareFps:Math.round(fps),axlePassFps:axleFps}));
  } catch (e) { await capture("failure"); throw e; } finally { await page.close(); }
}
(async () => {
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true,
    args:["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try { for (const [w,h] of [[1366,768],[1920,1080]]) await audit(browser,w,h); }
  finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
