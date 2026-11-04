/* Browser smoke for the local E1.27 import workbench; no device or network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => requests.push(request.url()));
  const file = path.resolve(__dirname, '../artifacts/expression-e1-27-runtime-observation-20261027/index.html').replaceAll('\\', '/');
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.body.dataset.ready === 'true', null, {timeout: 20000});
  assert.equal(await page.locator('#e127-records tr').count(), 0);
  assert.equal(await page.locator('#e127-observed-list .e127-semantic').count(), 0);
  assert.equal(await page.locator('#e127-oral-list .e127-semantic').count(), 4);
  assert.match(await page.locator('#e127-oral-count').innerText(), /24 条/);
  assert.match(await page.locator('#e127-oral-list [data-semantic="embarrassed"]').innerText(), /Happy\.eaf/);
  assert.equal(await page.locator('#semantic-list .e127-reported').count(), 4);
  assert.match(await page.locator('#e127-origin').innerText(), /尚未导入设备原始 JSON/);
  assert.equal(await page.locator('#semantic-list .e127-observed').count(), 0);
  await page.locator('#e127-oral-list [data-semantic="embarrassed"]').click();
  assert.match(await page.locator('#semantic-node .node-title').innerText(), /embarrassed/);
  assert.equal(await page.locator('#semantic-list [data-semantic="embarrassed"]').evaluate(node => node.classList.contains('e127-active-reported')), true);
  // Explicit demo mode must remain independent from the oral report.
  await page.locator('#e127-sample').click();
  assert.match(await page.locator('#e127-origin').innerText(), /离线合成样例/);
  assert.equal(await page.locator('#e127-records tr').count(), 7);
  assert.equal(await page.locator('#e127-observed-list .e127-semantic').count(), 6);
  assert.equal(await page.locator('#semantic-list .e127-observed').count(), 5);
  await page.locator('#e127-observed-list [data-semantic="happy"]').click();
  assert.match(await page.locator('#semantic-node .node-title').innerText(), /happy/);
  assert.equal(await page.locator('#semantic-list [data-semantic="happy"]').evaluate(node => node.classList.contains('e127-observed')), true);

  const sample = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../artifacts/expression-e1-27-runtime-observation-20261027/sample-snapshot.json'), 'utf8'));
  await page.locator('#e127-file').setInputFiles({name: 'valid.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(sample))});
  await page.waitForFunction(() => document.getElementById('e127-origin').textContent.includes('valid.json'));
  assert.equal(await page.locator('#e127-records tr').count(), 7);
  const invalid = {...sample, records: [...sample.records, {...sample.records[0], sequence: 1}]};
  await page.locator('#e127-file').setInputFiles({name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(invalid))});
  await page.waitForFunction(() => document.getElementById('e127-message').hidden === false);
  assert.match(await page.locator('#e127-message').innerText(), /拒绝导入/);
  assert.equal(await page.locator('#e127-records tr').count(), 7);
  await page.locator('#e127-sample').click();
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(requests.filter(url => !url.startsWith('file:')), []);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('E1.27 Edge smoke passed: local import, schema rejection, semantic node jump, mobile width, no network/errors');
})().catch(error => { console.error(error); process.exitCode = 1; });
