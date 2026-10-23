/* E1.4d local browser acceptance; no device, network, or firmware access. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE || "playwright");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "artifacts/expression-node-workbench-e1-4d-20261021/index.html");
const OUT = path.join(ROOT, "artifacts/expression-node-workbench-e1-4d-20261021/qa");
const checks = [];
const passed = name => checks.push(name);

(async () => {
  await fs.mkdir(OUT, {recursive:true});
  const browser = await chromium.launch({
    channel: process.env.DESKBOT_BROWSER_CHANNEL || "msedge",
    headless: true,
    ...(process.env.DESKBOT_BROWSER_EXECUTABLE ? {executablePath: process.env.DESKBOT_BROWSER_EXECUTABLE} : {})
  });
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors = [], external = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (/^https?:/i.test(request.url())) external.push(request.url()); });
  try {
    await page.goto(pathToFileURL(PAGE).href);
    await page.locator("#node-layer .node").first().waitFor();
    assert.equal(await page.locator("#layer-select option").count(), 3);
    assert.equal(await page.locator("#layer-select").inputValue(), "official");
    assert.match(await page.locator("#layer-status").innerText(), /官方/);
    passed("three_mapping_layers_are_exposed_with_official_default");

    const resource = page.locator("#node-layer .node[data-kind=resource]").first();
    await resource.click();
    await page.waitForFunction(() => document.querySelector("#frame-label").textContent.includes("第 1 /"));
    assert.match(await page.locator("#preview-caption").innerText(), /Happy\.eaf/);
    assert.match(await page.locator("#preview-caption").innerText(), /官方/);
    assert.match(await page.locator("#node-detail").innerText(), /源码证据/);
    const official = await page.evaluate(() => window.DESKBOT_NODE_WORKBENCH_TEST.state());
    assert.equal(official.output.src, "Happy.eaf");
    assert.deepEqual(official.timeline.timeline.map(event => event.channel), ["render.expression"]);
    const officialPixels = await page.locator("#screen-canvas").evaluate(canvas => {
      const data = canvas.getContext("2d").getImageData(0,0,360,360).data;
      let hash = 2166136261;
      for (const value of data) hash = Math.imul(hash ^ value, 16777619);
      return hash >>> 0;
    });
    passed("official_layer_keeps_pinned_asset_and_visual_only_timeline");

    await page.locator("#layer-select").selectOption("candidate");
    await page.waitForFunction(() => document.querySelector("#preview-caption").textContent.includes("shy_legacy.eaf"));
    const candidate = await page.evaluate(() => window.DESKBOT_NODE_WORKBENCH_TEST.state());
    assert.equal(candidate.selectedLayerId, "candidate");
    assert.equal(candidate.output.src, "shy_legacy.eaf");
    assert.equal(candidate.output.layout, "full_screen");
    assert.deepEqual(candidate.timeline.timeline.map(event => event.channel), ["render.expression"]);
    const candidatePixels = await page.locator("#screen-canvas").evaluate(canvas => {
      const data = canvas.getContext("2d").getImageData(0,0,360,360).data;
      let hash = 2166136261;
      for (const value of data) hash = Math.imul(hash ^ value, 16777619);
      return hash >>> 0;
    });
    assert.notEqual(candidatePixels, officialPixels, "candidate resource/layout must produce a distinct composed frame");
    assert.equal(await page.locator("#frame-range").getAttribute("min"), "0");
    assert.equal(await page.locator("#frame-range").getAttribute("max"), "187");
    assert.match(await page.locator("#layer-detail").innerText(), /embarrassed/);
    assert.match(await page.locator("#node-detail").innerText(), /候选资源\/布局/);
    passed("candidate_layer_switches_only_embarrassed_to_full_screen_eaf");

    await page.locator("#play-toggle").click();
    await page.waitForTimeout(60);
    assert.equal(await page.locator("#play-toggle").innerText(), "暂停");
    await page.locator("#layer-select").selectOption("fallback");
    await page.waitForFunction(() => document.querySelector("#layer-select").value === "fallback");
    assert.equal(await page.locator("#play-toggle").innerText(), "播放");
    await page.waitForFunction(() => document.querySelector("#preview-caption").textContent.includes("Happy.eaf"));
    const fallback = await page.evaluate(() => window.DESKBOT_NODE_WORKBENCH_TEST.state());
    assert.equal(fallback.output.src, "Happy.eaf");
    assert.equal(fallback.output.layout, "eye_anim");
    assert.deepEqual(fallback.timeline.timeline.map(event => event.channel), ["render.expression"]);
    const fallbackPixels = await page.locator("#screen-canvas").evaluate(canvas => {
      const data = canvas.getContext("2d").getImageData(0,0,360,360).data;
      let hash = 2166136261;
      for (const value of data) hash = Math.imul(hash ^ value, 16777619);
      return hash >>> 0;
    });
    assert.equal(fallbackPixels, officialPixels, "exact fallback must restore original composed frame");
    assert.ok(fallback.layerSwitchCount >= 2);
    passed("fast_layer_switch_stops_old_playback_and_restores_exact_fallback");

    const actionNode = page.locator("#node-layer .node[data-kind=action]").first();
    await actionNode.click();
    const actionState = await page.evaluate(() => window.DESKBOT_NODE_WORKBENCH_TEST.state());
    assert.equal(actionState.timeline, null);
    assert.equal(await page.locator("#play-toggle").isDisabled(), true);
    assert.match(await page.locator("#preview-caption").innerText(), /动作请求/);
    passed("action_node_has_no_animation_or_device_side_effect");

    await page.locator("#layer-select").selectOption("candidate");
    await page.locator("#node-layer .node[data-kind=resource]").click();
    await page.waitForFunction(() => document.querySelector("#preview-caption").textContent.includes("shy_legacy.eaf"));

    await page.screenshot({path:path.join(OUT, "desktop.png"), fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.locator("#fit-view").click();
    await page.locator("#fit-view").scrollIntoViewIfNeeded();
    await page.waitForTimeout(50);
    const widths = await page.evaluate(() => ({scroll:document.documentElement.scrollWidth, client:document.documentElement.clientWidth}));
    assert.ok(widths.scroll <= widths.client + 1, JSON.stringify(widths));
    await page.screenshot({path:path.join(OUT, "mobile.png"), fullPage:true});
    passed("layered_workbench_remains_usable_on_narrow_screen");

    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    const result = {result:"passed", checks, viewport_widths:widths, external_requests:external, browser_errors:errors};
    await fs.writeFile(path.join(OUT, "browser-acceptance.json"), JSON.stringify(result,null,2)+"\n", "utf8");
    console.log(JSON.stringify(result,null,2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
