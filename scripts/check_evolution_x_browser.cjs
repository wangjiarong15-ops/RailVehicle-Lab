const assert = require("node:assert/strict");
const {mkdir, readFile, writeFile} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const address = new URL(process.argv[2]);
assert.equal(address.hostname, "127.0.0.1");
const output = resolve(process.argv[3]);
const button = (page, name) => page.getByRole("button", {name, exact:true});
const heading = (page, name) => page.getByRole("heading", {name, exact:true});
const nav = (page, name) => button(page.getByTestId("stSidebar"), name);

async function download(page, name) {
  const event = page.waitForEvent("download");
  await button(page, name).click();
  return readFile(await (await event).path());
}

async function configureProject(page) {
  await button(page, "工程项目").click();
  await page.getByRole("textbox", {name:"新工程项目名称"}).fill("Evolution X 合成验收");
  await button(page, "创建项目").click();
  await page.getByText("Evolution X 合成验收", {exact:true}).first().waitFor();
  const panel = page.getByRole("heading", {name:"工程工作台", exact:true});
  await panel.click();
  await button(page, "工程项目").click();
  await page.getByText("工况约束、版本与资料", {exact:true}).click();
  const upload = page.locator('input[type="file"]').first();
  await upload.setInputFiles({name:"suspension.txt", mimeType:"text/plain",
    buffer:Buffer.from("二系悬挂公式 F=kx。此处为合成验收资料。")});
  await page.getByRole("textbox", {name:"资料来源及授权说明"}).fill("演示数据 · 本地测试");
  await button(page, "索引到当前项目").click();
  await page.getByText(/资料版本 v1 已保存/).waitFor();
  await page.getByRole("textbox", {name:"检索原文、工程公式或结构术语"}).fill("悬挂公式");
  await page.getByRole("textbox", {name:"检索原文、工程公式或结构术语"}).press("Enter");
  await page.getByText(/演示数据 · 本地测试 · 行 1/).waitFor();
  await page.screenshot({path:resolve(output, "1366-project-knowledge.png")});
  await panel.click();
}

async function approve(page) {
  await heading(page, "待审批结构化计划").waitFor();
  await button(page, "批准并加入本地队列").click();
  await page.getByText(/待复核 · 1\/4 轮/).waitFor({timeout:120000});
  return JSON.parse((await download(page, "导出研究证据 JSON")).toString());
}

(async () => {
  await mkdir(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true,
    args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]});
  try {
    const page = await browser.newPage({viewport:{width:1366,height:768},acceptDownloads:true});
    page.setDefaultTimeout(25000);
    const url = new URL(address);
    url.searchParams.set("embed_options","dark_theme");
    await page.goto(url.href);
    await heading(page, "工程工作台").waitFor();
    if (process.argv.includes("--verify-restart")) {
      await button(page, "工程研究").click();
      await heading(page, "历史复算 · 检索二系悬挂公式资料").waitFor();
      const restored = JSON.parse((await download(page, "导出研究证据 JSON")).toString());
      assert.equal(restored.task.rounds[0].origin, "replay");
      assert(restored.tools.r0_reference.output.citations.some(item => item.id.startsWith("library.")));
      await page.screenshot({path:resolve(output,"1366-exe-restart.png")});
      console.log("EXE restart restored the original local reference and archived replay results.");
      return;
    }
    await page.screenshot({path:resolve(output, "1366-home-after.png")});
    await configureProject(page);
    await button(page, "工程研究").click();
    await heading(page, "工程研究工作台").waitFor();
    await page.getByRole("textbox", {name:"研究目标",exact:true}).fill("检索二系悬挂公式资料");
    await button(page, "新建研究任务").click();
    await heading(page, "检索二系悬挂公式资料").waitFor();
    await button(page, "生成本轮计划").click();
    const initial = await approve(page);
    assert(initial.task.engineering_context.project);
    assert(initial.tools.r0_reference.output.citations.some(c => c.id.startsWith("library.") && c.source.includes("行 1")));
    await button(page, "从归档新建复算").click();
    await button(page, "查看归档复算计划").click();
    const replay = await approve(page);
    assert.notEqual(replay.task.id, initial.task.id);
    assert.equal(replay.task.input_sha256, initial.task.input_sha256);
    assert.equal(replay.tools.r0_reference.proof, initial.tools.r0_reference.proof);
    assert.equal(replay.task.rounds[0].origin, "replay");
    await writeFile(resolve(output,"knowledge-replay.json"),JSON.stringify({initial,replay},null,2));
    await page.screenshot({path:resolve(output,"1366-archived-replay.png")});
    assert.equal(await page.getByTestId("stException").count(), 0);

    const light = await browser.newPage({viewport:{width:1920,height:1080}});
    url.searchParams.set("embed_options","light_theme");
    await light.goto(url.href);
    await heading(light, "工程工作台").waitFor();
    const cdp = await light.context().newCDPSession(light);
    await cdp.send("Emulation.setPageScaleFactor",{pageScaleFactor:1.15});
    await button(light, "工程项目").click();
    await light.getByRole("combobox", {name:"当前工程项目"}).click();
    await light.getByRole("option", {name:"Evolution X 合成验收"}).click();
    await light.getByText("Evolution X 合成验收",{exact:true}).first().waitFor();
    await light.screenshot({path:resolve(output, "1920-project-light-115.png")});
    await light.keyboard.press("Escape");
    await cdp.send("Emulation.setPageScaleFactor",{pageScaleFactor:1});
    await nav(light, "数字孪生").click();
    await light.getByTestId("stRadio").getByText("在线采集与状态估计",{exact:true}).click();
    await heading(light, "在线采集与状态估计").waitFor();
    await button(light, "建立本地模拟采集").click();
    await button(light, "连接或恢复").click();
    await light.getByText(/原始包 [1-9]/).waitFor();
    await button(light, "模拟断线").click();
    await light.getByText("持久化采集与恢复",{exact:true}).click();
    await button(light, "将当前原始记录保存为新档案").click();
    await light.getByText("原始包已写入独立采集档案。",{exact:true}).waitFor();
    await light.reload();
    await nav(light, "数字孪生").click();
    await light.getByTestId("stRadio").getByText("在线采集与状态估计",{exact:true}).click();
    await heading(light, "在线采集与状态估计").waitFor();
    await light.getByText("持久化采集与恢复",{exact:true}).click();
    await button(light, "恢复历史采集").click();
    const recording = await download(light, "导出原始 JSONL");
    assert.equal(JSON.parse(recording.toString().split("\n")[0]).source, "simulated");
    assert(recording.toString().split("\n").length > 3);
    await writeFile(resolve(output, "restored-synthetic.jsonl"), recording);
    await light.screenshot({path:resolve(output, "1920-acquisition-restore.png")});
    assert.equal(await light.getByTestId("stException").count(), 0);
    console.log("Edge verified local library, scoped task replay and synthetic acquisition after refresh.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
