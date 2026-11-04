/* Browser smoke for the E1.29 capture path and E1.27 snapshot handoff. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

let browser;
(async () => {
  browser = await chromium.launch({headless: true, executablePath: (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => requests.push(request.url()));
  const file = path.resolve(__dirname, '../artifacts/expression-e1-29-mcp-capture-20261028/index.html').replaceAll('\\', '/');
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.body.dataset.ready === 'true', null, {timeout: 20000});
  const payload = page.locator('#payload');
  assert.match(await page.locator('.provenance').innerText(), /合成样例/);
  assert.match(await payload.innerText(), /"type": "mcp"/);
  assert.match(await payload.innerText(), /"response_message"/);

  const nodes = page.locator('.flow-node');
  assert.equal(await nodes.count(), 5);
  for (const step of ['host', 'mcp', 'rpc', 'text', 'snapshot']) {
    await page.locator(`.flow-node[data-step="${step}"]`).click();
    assert.equal(await page.locator(`.flow-node[data-step="${step}"]`).getAttribute('aria-pressed'), 'true');
    assert.ok((await payload.innerText()).length > 0, `empty payload at ${step}`);
  }
  await page.locator('.flow-node[data-step="snapshot"]').click();
  assert.match(await payload.innerText(), /"claim_scope": "set_emotion_call_only"/);
  await page.locator('.switcher [data-view="provenance"]').click();
  assert.match(await payload.innerText(), /"capture_file_sha256"/);
  assert.equal(await page.locator('.switcher [data-view="request"]').isDisabled(), true);

  await page.locator('.flow-node[data-step="host"]').click();
  await page.locator('.switcher [data-view="request"]').click();
  assert.match(await payload.innerText(), /"method": "tools\/call"/);
  await page.locator('.switcher [data-view="response"]').click();
  assert.match(await payload.innerText(), /"result"/);

  await page.locator('.flow-node[data-step="text"]').click();
  await page.locator('.switcher [data-view="response"]').click();
  assert.match(await payload.innerText(), /"type": "text"/);
  assert.match(await payload.innerText(), /set_emotion_call_entered/);
  await page.locator('.switcher [data-view="request"]').click();
  assert.match(await payload.innerText(), /"name": "self\.expression\.observation"/);

  const evidence = path.resolve(__dirname, '../tmp/expression-e1-29-browser');
  fs.mkdirSync(evidence, {recursive: true});
  await page.screenshot({path: path.join(evidence, 'desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: path.join(evidence, 'mobile.png'), fullPage: true});

  const e127 = await browser.newPage({viewport: {width: 1440, height: 1000}});
  e127.on('pageerror', error => errors.push(String(error)));
  e127.on('request', request => requests.push(request.url()));
  const importPage = path.resolve(__dirname, '../artifacts/expression-e1-27-runtime-observation-20261027/index.html').replaceAll('\\', '/');
  await e127.goto(`file:///${importPage}`);
  await e127.waitForFunction(() => document.body.dataset.ready === 'true', null, {timeout: 20000});
  await e127.locator('#e127-file').setInputFiles({
    name: 'observation-snapshot.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(require('../artifacts/expression-e1-29-mcp-capture-20261028/sample-snapshot.json'))),
  });
  await e127.waitForFunction(() => document.getElementById('e127-origin').textContent.includes('observation-snapshot.json'));
  assert.equal(await e127.locator('#e127-records tr').count(), 2);
  assert.deepEqual(requests.filter(url => !url.startsWith('file:')), []);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('E1.29 Edge smoke passed: five nodes, request/response/import views, nested text layer, mobile width, E1.27 snapshot import, no network/errors');
})().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
