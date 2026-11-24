// Read-only RC1 checks against a loopback static server. Screenshots use fictional data.
const assert = require("node:assert/strict");
const { mkdir, readFile } = require("node:fs/promises");
const { createServer } = require("node:http");
const { extname, resolve, sep } = require("node:path");
const { once } = require("node:events");
const { chromium } = require(process.env.RAIL_PLAYWRIGHT_MODULE || "playwright");

const root = resolve(__dirname, "..", "qoder-sites-demo");
const output = resolve(process.argv[2] || "tmp/phase8-rc1-browser");
const mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".glb": "model/gltf-binary", ".csv": "text/csv"};
const server = createServer(async (request, response) => {
  try {
    const path = new URL(request.url, "http://127.0.0.1").pathname;
    const file = resolve(root, "." + (path.endsWith("/") ? path + "index.html" : path));
    if (!file.startsWith(root + sep)) throw Error("path outside demo");
    const data = await readFile(file);
    response.writeHead(200, {"Content-Type": mime[extname(file)] || "application/octet-stream"});
    response.end(data);
  } catch {
    response.writeHead(404).end();
  }
});

async function run() {
  await mkdir(output, {recursive: true});
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = "http://127.0.0.1:" + server.address().port;
  const browser = await chromium.launch({executablePath: process.env.RAIL_BROWSER_PATH, headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]});
  try {
    for (const [width, height] of [[1366, 768], [1920, 1080], [390, 844]]) {
      const page = await browser.newPage({viewport: {width, height}});
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.route("**/*", route => route.request().url().startsWith(base + "/") ? route.continue() : route.abort());
      if (width === 1366) {
        await page.goto(base + "/tests/");
        await page.waitForFunction(() => window.__tests?.done);
        const result = await page.evaluate(() => window.__tests);
        assert(result.total >= 56, "RC1 unit-test coverage count");
        assert.deepEqual(result.failed, []);
        console.log(`RC1 browser unit tests ${result.passed}/${result.total}`);
      }
      const started = Date.now();
      const glb = page.waitForResponse(response => response.url() === base + "/bogie.glb");
      await page.goto(base);
      await page.locator('[data-ws="inspect"]').click();
      assert.equal((await glb).status(), 200);
      await page.locator("#loading").waitFor({state: "hidden"});
      await page.locator("#scene").waitFor();
      const glbReadyMs = Date.now() - started;
      assert.equal(await page.locator("#error").isVisible(), false);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path: resolve(output, `rc1-${width}-model.png`)});
      await page.locator('[data-ws="home"]').click();
      await page.locator("#home-demo").click();
      await page.locator("#timeline").waitFor();
      assert.deepEqual(errors, []);
      await page.screenshot({path: resolve(output, `rc1-${width}-replay.png`)});
      console.log(JSON.stringify({width, height, glbReadyMs, replay: true}));
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

run().catch(error => {console.error(error); process.exitCode = 1;}).finally(() => server.close());
