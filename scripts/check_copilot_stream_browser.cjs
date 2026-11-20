// The runner injects only a loopback model fixture and synthetic data.
const assert = require("node:assert/strict");
const {mkdir} = require("node:fs/promises");
const {resolve} = require("node:path");
const {chromium} = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");
const url = new URL(process.argv[2]), out = resolve(process.argv[3]);
assert.equal(url.origin,"http://127.0.0.1:"+url.port);
(async()=>{
  await mkdir(out,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.RAIL_BROWSER_PATH,headless:true});
  const page = await browser.newPage({viewport:{width:1366,height:768}});
  await page.route("**/*",r=>new URL(r.request().url()).origin===url.origin || /^(data:|blob:)/.test(r.request().url()) ? r.continue():r.abort());
  const input=page.getByTestId("stChatInputTextArea");
  const options=page.getByRole("button",{name:"模型与权限"});
  async function authorize(first=false) {
    if (first) {
      await options.click();
      await page.getByText("在线模型",{exact:true}).click();
      await page.getByRole("heading",{name:"RailVehicle Copilot",exact:true}).click();
    }
    const plan=page.getByRole("checkbox",{name:/授权本轮在线规划/});
    const insight=page.getByRole("checkbox",{name:/同时允许发送匿名/});
    assert.equal(await plan.isChecked(),false);
    await page.getByText(/授权本轮在线规划（可能产生/).click();
    await page.getByText(/同时允许发送匿名聚合指标/).click();
    assert(await plan.isChecked() && await insight.isChecked());
  }
  async function ask(text) { await input.fill(text); await input.press("Enter"); }
  try {
    await page.goto(url.href);
    await page.getByTestId("stSidebar").getByRole("button",{name:/工程 Copilot$/}).click();
    await authorize(true);
    await ask("分析横向振动");
    await page.getByText("AI 服务当前限流，请稍后重试。",{exact:true}).waitFor();
    await authorize();
    await page.getByRole("button",{name:"重试上一轮"}).click();
    await page.getByText(/横向加速度 RMS为.*\[metric.accel.lateral.rms\]/).waitFor();
    const verified = page.getByText("AI 辅助解读 · 已核对证据范围",{exact:true});
    await verified.waitFor();
    await verified.evaluate(n => n.scrollIntoView({block:"center"}));
    await page.screenshot({path:resolve(out,"1366-verified-mock.png")});
    await authorize();
    await ask("复查横向加速度");
    await page.getByText(/横向加速度 RMS为.*\[metric.accel.lateral.rms\]/).waitFor();
    await page.getByRole("button",{name:"停止生成"}).click();
    await page.getByText("任务已停止。",{exact:true}).waitFor();
    assert.equal(await page.getByTestId("stException").count(),0);
    console.log("Loopback simulated provider: per-turn consent, rate limit, retry, streamed evidence, validated completion and cancellation passed.");
  } catch(e) { await page.screenshot({path:resolve(out,"failure.png")}); throw e; }
  finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
