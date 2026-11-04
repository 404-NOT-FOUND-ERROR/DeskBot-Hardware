/* Real Edge smoke test for E1.22; local file only, no device or network access. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => requests.push(request.url()));
  const file = path.resolve(__dirname, '../artifacts/expression-e1-22-candidate-preview-20261026/index.html').replaceAll('\\', '/');
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.body.dataset.ready === 'true');
  const pixels = await page.locator('#before,#candidate').evaluateAll(canvases =>
    canvases.map(canvas => [...canvas.getContext('2d').getImageData(0, 0, 360, 360).data]
      .some((value, index) => index % 4 !== 3 && value > 8)));
  assert.deepEqual(pixels, [true, true]);
  assert.match(await page.locator('#before-frame').innerText(), /1 \/ 34 帧/);
  assert.match(await page.locator('#candidate-frame').innerText(), /1 \/ 42 帧/);
  assert.equal(await page.getByText('断开旧边', {exact: true}).count(), 1);
  assert.equal(await page.getByText('新建连接', {exact: true}).count(), 1);
  await page.locator('#step-forward').click();
  assert.match(await page.locator('#before-frame').innerText(), /2 \/ 34 帧/);
  assert.match(await page.locator('#candidate-frame').innerText(), /2 \/ 42 帧/);
  await page.locator('#step-back').click();
  assert.match(await page.locator('#before-frame').innerText(), /1 \/ 34 帧/);
  await page.locator('#play').click();
  await page.waitForTimeout(180);
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'), 'true');
  await page.locator('#speed').selectOption('1.5');
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'), 'true');
  await page.locator('#play').click();
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'), 'false');
  if (process.env.E1_22_SCREENSHOT_DESKTOP) {
    await page.screenshot({path: process.env.E1_22_SCREENSHOT_DESKTOP, fullPage: true});
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (process.env.E1_22_SCREENSHOT_MOBILE) {
    await page.screenshot({path: process.env.E1_22_SCREENSHOT_MOBILE, fullPage: true});
  }
  assert.deepEqual(requests.filter(url => !url.startsWith('file:')), []);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('E1.22 Edge smoke passed: source atlases, dual frame rendering, step/play/speed controls, mobile width, no network/errors');
})().catch(error => { console.error(error); process.exitCode = 1; });
