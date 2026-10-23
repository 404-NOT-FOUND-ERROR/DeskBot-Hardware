/* E1.9 local browser acceptance; no device or network access. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE || "playwright");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "artifacts/expression-binding-matrix-e1-9-20261022/index.html");
const OUT = path.join(ROOT, "artifacts/expression-binding-matrix-e1-9-20261022/qa");
const passed = [];

(async () => {
  await fs.mkdir(OUT, {recursive:true});
  const browser = await chromium.launch({channel:"msedge", headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors = [], external = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (/^https?:/i.test(request.url())) external.push(request.url()); });
  try {
    await page.goto(pathToFileURL(PAGE).href);
    await page.locator("#body tr").first().waitFor();
    assert.match(await page.locator("body").innerText(), /源码行为入口/);
    assert.match(await page.locator("#count").innerText(), /113 \/ 113/);
    passed.push("initial_matrix_renders_all_review_rows");

    await page.locator("#type").selectOption("event_route");
    assert.match(await page.locator("#count").innerText(), /20 \/ 113/);
    await page.locator("#type").selectOption("semantic_reference");
    assert.match(await page.locator("#count").innerText(), /29 \/ 113/);
    passed.push("behavior_routes_are_distinct_from_semantic_reverse_references");

    await page.locator("#type").selectOption("official_eaf_resource");
    await page.locator("#q").fill("Happy.eaf");
    const happy = page.locator("#body tr");
    assert.equal(await happy.count(), 1);
    assert.match(await happy.innerText(), /embarrassed/);
    assert.match(await happy.innerText(), /surprised/);
    assert.match(await happy.innerText(), /confident/);
    passed.push("shared_happy_resource_lists_all_affected_semantics");

    await page.locator("#type").selectOption("candidate_eaf_resource");
    await page.locator("#q").fill("");
    assert.equal(await page.locator("#body tr").count(), 1);
    assert.match(await page.locator("#body tr").innerText(), /候选不部署/);
    passed.push("candidate_asset_is_separate_from_official_profile");

    await page.locator("#type").selectOption("event_route");
    await page.locator("#q").fill("tool-action");
    const toolAction = page.locator("#body tr");
    assert.equal(await toolAction.count(), 1);
    assert.match(await toolAction.innerText(), /并非同时触发/);
    passed.push("available_action_options_are_not_misreported_as_executed");

    await page.locator("#q").fill("");
    await page.locator("#type").selectOption("");
    await page.screenshot({path:path.join(OUT,"desktop.png")});
    const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(desktopOverflow, false);
    await page.setViewportSize({width:320,height:800});
    await page.screenshot({path:path.join(OUT,"mobile-320.png")});
    const mobile = await page.evaluate(() => ({
      viewport:innerWidth,
      document:document.documentElement.scrollWidth,
      tableContained:document.querySelector(".table-wrap").scrollWidth >= document.querySelector(".table-wrap").clientWidth,
      rows:document.querySelectorAll("#body tr").length,
    }));
    assert.equal(mobile.document > mobile.viewport, false);
    assert.equal(mobile.tableContained, true);
    assert.equal(mobile.rows, 113);
    passed.push("desktop_and_320px_layout_keep_outer_page_in_bounds");

    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    passed.push("no_browser_errors_or_external_requests");
    await fs.writeFile(path.join(OUT,"browser-acceptance.json"), JSON.stringify({result:"pass",checks:passed,errors,external_requests:external,mobile},null,2)+"\n", "utf8");
    process.stdout.write(JSON.stringify({result:"pass",checks:passed,mobile},null,2)+"\n");
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
