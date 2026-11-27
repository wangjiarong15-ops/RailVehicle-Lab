// The isolated runner supplies a loopback Streamlit and an offline Copilot.
const assert = require("node:assert/strict");
const {mkdir, readFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const address = new URL(process.argv[2]);
assert.equal(address.hostname, "127.0.0.1");
address.searchParams.set("rail_page", "动力学仿真");
address.searchParams.set("embed_options", "dark_theme");
const output = resolve(process.argv[3]);

async function audit(browser, width, height) {
  const page = await browser.newPage({viewport:{width,height},acceptDownloads:true});
  const errors = [];
  page.on("pageerror", error=>errors.push(error.message));
  await page.route("**/*", route=>new URL(route.request().url()).origin===address.origin ||
      /^(blob:|data:)/.test(route.request().url()) ? route.continue() : route.abort());
  const capture = name=>page.screenshot({path:resolve(output,`${width}-${name}.png`)});
  try {
    await page.goto(address.href);
    await page.getByRole("combobox",{name:"计算工况",exact:true}).click();
    await page.getByRole("option",{name:"车体横移与侧滚（2 自由度）",exact:true}).click();
    await page.getByRole("heading",{name:"车体横移与侧滚试验",exact:true}).waitFor();
    const frame = page.frameLocator("iframe[title*=bogie_viewer]").first();
    const viewer = frame.locator('#workbench[data-model="glb"][data-status="ready"]');
    await viewer.waitFor();
    await frame.locator("#lateral-controls:not([hidden])").waitFor();
    const seek = frame.locator("#sim-time");
    await frame.locator("#lateral-signal").selectOption("roll_rad");
    await frame.locator("#lateral-peak").click();
    const peak = Number(await seek.inputValue());
    assert(peak > 0);
    await viewer.evaluate(node=>new Promise((resolve,reject)=>{
      const deadline = performance.now()+5000;
      function painted() {
        const [left,right] = node.dataset.secondaryScale.split(",").map(Number);
        if (Math.abs(Number(node.dataset.bodyRoll)) > .0001 && (left-1)*(right-1)<0) return resolve();
        if (performance.now() > deadline) return reject(Error("侧滚峰值未驱动左右空气弹簧产生相反变化"));
        requestAnimationFrame(painted);
      }
      painted();
    }));
    assert.equal(await frame.locator("#response").getAttribute("data-sample"), String(peak));
    await frame.locator("#sim-next").click();
    assert.equal(+await seek.inputValue(), peak+1);
    await frame.locator("#browse-parts").click();
    await frame.locator("#part-query").fill("air_spring_l");
    await frame.locator('.part-row[data-part="air_spring_l"]').click();
    await frame.locator("#simulation-part-response").filter({hasText:/二系垂向相对变化/}).waitFor();
    await frame.locator("#browse-parts").click();
    await frame.locator("#surface-mode").selectOption("selected");
    await frame.locator('#workbench[data-selected="air_spring_l"][data-selected-transparent="true"]').waitFor();
    await frame.locator("#section-toggle").click();
    await frame.locator('#workbench[data-section-axis="x"]').waitFor();
    await frame.locator("#section-toggle").click();
    await frame.locator("#surface-mode").selectOption("solid");
    await viewer.scrollIntoViewIfNeeded();
    await capture("lateral-glb");
    const [input] = await Promise.all([page.waitForEvent("download"),
      page.getByRole("button",{name:"保存横向工况 JSON",exact:true}).click()]);
    const config = JSON.parse(await readFile(await input.path(), "utf8"));
    assert.equal(config.model,"rail.carbody-lateral-roll.v1");
    await page.getByRole("button",{name:"将此工况带入 Copilot",exact:true}).click();
    await page.getByText(/当前仿真 · 横移与侧滚 · 20 m\/s/).waitFor();
    const chat = page.getByTestId("stChatInputTextArea");
    await chat.fill("计算侧滚，抗侧滚刚度提高20%");
    await chat.press("Enter");
    await page.getByText(/横向—侧滚教学模型.*仿真台已绑定工况/).waitFor();
    const [comparison] = await Promise.all([page.waitForEvent("download"),
      page.getByRole("button",{name:"保存此对照工况",exact:true}).click()]);
    const config2 = JSON.parse(await readFile(await comparison.path(), "utf8"));
    assert.equal(config2.case.anti_roll_k_nm_rad, 1.2*config.case.anti_roll_k_nm_rad);
    assert.deepEqual({...config2.case, anti_roll_k_nm_rad:config.case.anti_roll_k_nm_rad},config.case);
    await capture("copilot-comparison");
    await page.getByRole("button",{name:"返回横移与侧滚实验",exact:true}).click();
    await page.getByRole("heading",{name:"车体横移与侧滚试验",exact:true}).waitFor();
    const sidebar = page.getByTestId("stSidebar");
    await sidebar.getByRole("button",{name:/三维转向架$/}).click();
    await page.getByRole("heading",{name:"三维诊断工作台",exact:true}).waitFor();
    const studio = page.frameLocator("iframe[title*=bogie_viewer]").first();
    await studio.locator("#copilot-inspection:not([hidden])").waitFor();
    await studio.locator("#browse-parts").click();
    await studio.locator("#part-query").fill("coil_a_l");
    await studio.locator('.part-row[data-part="coil_a_l"]').click();
    await studio.locator("#browse-parts").click();
    await studio.locator("#section-toggle").click();
    await studio.locator('#workbench[data-section-axis="x"]').waitFor();
    await studio.locator("#copilot-inspection").click();
    await page.getByText(/已在本会话记录三维检查/).waitFor();
    await sidebar.getByRole("button",{name:/AI Copilot$/}).click();
    await chat.fill("查看当前三维检查状态");
    await chat.press("Enter");
    await page.getByText(/已记录的三维检查.*A 轴 左侧 一系螺旋弹簧.*剖切 X = 0 m/).waitFor();
    await capture("copilot-inspection");
    if (width === 1366) {
      await page.getByRole("button",{name:/会话记录/}).click();
      const [archive] = await Promise.all([page.waitForEvent("download"),
        page.getByRole("button",{name:"保存对话记录",exact:true}).click()]);
      await restoreArchive(browser, await archive.path());
    }
    assert.equal(await page.getByTestId("stException").count(),0);
    assert.deepEqual(errors,[]);
    console.log(`${width}x${height}: roll/springs, transparency, section, computed exports, Copilot return and explicit inspection capture passed.`);
  } catch (error) {
    await capture("failure");
    throw error;
  } finally { await page.close(); }
}

async function restoreArchive(browser, path) {
  const page = await browser.newPage({viewport:{width:1366,height:768}});
  try {
    const url = new URL(address);
    url.searchParams.set("rail_page","RailVehicle Copilot");
    await page.goto(url.href);
    const history = page.getByRole("button",{name:/会话记录/});
    await history.click();
    await page.locator(".st-key-cp_archive input[type=file]").setInputFiles({name:"railvehicle-conversation.json",
      mimeType:"application/json", buffer:await readFile(path)});
    await page.getByRole("button",{name:"恢复归档中的工程上下文"}).click();
    await page.getByText(/当前仿真 · 横移与侧滚 · 20 m\/s/).waitFor();
    await page.getByText("此会话可查询本机已记录的三维检查状态。",{exact:true}).waitFor();
    // Restoring an archive is inert until a new prompt or a user-requested replay.
    assert.equal(await page.getByTestId("stChatMessage").count(),0);
    console.log("Fresh session restored the archived simulation and inspection without running a tool automatically.");
  } finally { await page.close(); }
}
(async()=>{
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try { for (const [width,height] of [[1366,768],[1920,1080]]) await audit(browser,width,height); }
  finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
