/* E1.10 browser acceptance; local file only, no device or network. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE || "playwright");

const ROOT = path.resolve(__dirname, "..");
const DIR = path.join(ROOT, "artifacts/expression-e1-10-review-20261022");
const PASSED = [];

(async () => {
  const browser = await chromium.launch({channel:"msedge", headless:true});
  const context = await browser.newContext({viewport:{width:1400,height:980}});
  const page = await context.newPage();
  const errors = [], requests = [];
  await fs.mkdir(path.join(DIR,"qa"),{recursive:true});
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (/^https?:/i.test(request.url())) requests.push(request.url()); });
  try {
    await page.goto(pathToFileURL(path.join(DIR, "index.html")).href);
    await page.waitForFunction(() => document.body.dataset.ready === "true", null, {timeout:15000});
    assert.match(await page.locator("body").innerText(), /离线候选 \/ 不可部署/);
    assert.match(await page.locator("#capacity").innerText(), /255,380 B|255380 B/);
    PASSED.push("desktop loads the immutable report and default semantic candidate");

    const parity = await page.evaluate(() => {
      const canvas=document.querySelector("#before"), ctx=canvas.getContext("2d");
      const rgba=ctx.getImageData(0,0,360,360).data;
      let visible=0;
      for(let i=0;i<rgba.length;i+=4) if(rgba[i]||rgba[i+1]||rgba[i+2]) visible++;
      return {visible,cornerAlpha:rgba[3],centerAlpha:ctx.getImageData(180,180,1,1).data[3]};
    });
    assert.ok(parity.visible>20);
    assert.equal(parity.cornerAlpha,0);
    assert.equal(parity.centerAlpha,255);
    PASSED.push("round preview canvas paints bounded circular-mask pixels");

    await page.locator("#plan").selectOption("embarrassed-shy-four-bit");
    assert.match(await page.locator("#capacity").innerText(), /230,564|230564/);
    assert.match(await page.locator("#route").innerText(), /embarrassed/);
    assert.match(await page.locator("#note").innerText(), /4-bit/);
    PASSED.push("single semantic selection keeps the action mapping and labels the layout gate");

    await page.locator("#frame").fill("520");
    const fourBitPixels=await page.locator("#after").evaluate(canvas=>canvas.toDataURL());
    await page.locator("#plan").selectOption("embarrassed-shy-original");
    await page.locator("#frame").fill("520");
    assert.equal(await page.locator("#after").evaluate(canvas=>canvas.toDataURL()),fourBitPixels);
    PASSED.push("4-bit and original shy preview pixels match at the same timeline position");

    await page.locator("#frame").fill("520");
    await page.locator("#play").click();
    assert.equal(await page.locator("#play").getAttribute("aria-pressed"), "true");
    await page.locator("#play").click();
    assert.equal(await page.locator("#play").getAttribute("aria-pressed"), "false");
    await page.locator("#reset").click();
    assert.equal(await page.locator("#frame").inputValue(), "0");
    PASSED.push("play pause scrub and reset controls operate without action side effects");

    await page.screenshot({path:path.join(DIR,"qa/desktop.png"),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({width:320,height:900});
    await page.screenshot({path:path.join(DIR,"qa/mobile-320.png"),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    PASSED.push("desktop and 320px mobile layouts stay within the viewport");

    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    await fs.writeFile(path.join(DIR,"qa/browser-acceptance.json"),JSON.stringify({result:"pass",checks:PASSED,console_errors:errors,external_requests:requests},null,2)+"\n","utf8");
    process.stdout.write(JSON.stringify({result:"pass",checks:PASSED},null,2)+"\n");
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
