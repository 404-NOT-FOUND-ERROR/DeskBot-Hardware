/* Real Edge smoke test for the E1.19 page; no device or network access. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => requests.push(request.url()));
  const file = path.resolve(__dirname, '../artifacts/expression-node-preview-e1-19-20261024/index.html').replaceAll('\\', '/');
  await page.goto(`file:///${file}`);
  await page.waitForTimeout(600);
  // Canvas previews carry the resource id too; target the actual graph nodes.
  const happy = page.locator('.node[data-resource="Happy.eaf"]');
  const shocked = page.locator('.node[data-resource="shocked.eaf"]');
  assert.equal(await happy.count(), 1);
  assert.equal(await shocked.count(), 1);
  assert.equal(await happy.innerText().then(text => text.includes('Happy.eaf')), true);
  assert.equal(await shocked.innerText().then(text => text.includes('shocked.eaf')), true);
  assert.equal(await page.locator('#action-node canvas.node-motion-preview').count(), 1);
  assert.equal(await page.locator('#draft-badge').innerText(), '草稿 0 项变化');
  assert.ok(await page.locator('#preview-before').evaluate(canvas => [...canvas.getContext('2d').getImageData(0, 0, 360, 360).data].some((value, i) => i % 4 === 3 && value > 0)));
  await page.locator('#semantic-node [data-socket="output"]').click();
  await shocked.locator('[data-socket="input"]').click();
  assert.equal(await page.locator('#draft-badge').innerText(), '草稿 0 项变化');
  await page.locator('#semantic-node [data-socket="output"]').click();
  await happy.locator('[data-socket="input"]').click();
  assert.equal(await page.locator('#draft-badge').innerText(), '草稿 1 项变化');
  assert.equal(await page.locator('#edge-disconnected').count(), 1);
  await page.locator('#undo').click();
  assert.equal(await page.locator('#draft-badge').innerText(), '草稿 0 项变化');
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.locator('body').evaluate(() => document.documentElement.scrollWidth >= 390), true);
  assert.deepEqual(requests.filter(url => !url.startsWith('file:')), []);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('E1.19 browser smoke passed: previews, explicit sockets, undo, narrow viewport, no network/errors');
})().catch(error => { console.error(error); process.exitCode = 1; });
