/* Browser smoke for the E1.28 offline semantic review; no hardware or network. */
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
  const file = path.resolve(__dirname, '../artifacts/expression-e1-28-semantic-review-20261027/index.html').replaceAll('\\', '/');
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.body.dataset.ready === 'true');
  assert.equal(await page.locator('.semantic-card').count(), 4);
  assert.equal(await page.locator('.graph-col').count(), 4);
  assert.equal(await page.locator('.oral-item').count(), 4);
  assert.match(await page.locator('#graph-title').innerText(), /两条独立支路/);
  assert.equal(await page.locator('.branch-line').count(), 8);
  assert.equal(await page.locator('.flow > .flow-node.resource').count(), 0);
  assert.equal(await page.locator('.flow > .flow-node.action').count(), 0);
  assert.match(await page.locator('.semantic-card[data-semantic="happy"]').innerText(), /Happy\.eaf/);
  assert.match(await page.locator('.semantic-card[data-semantic="embarrassed"]').innerText(), /cat_nuzzle/);
  assert.match(await page.locator('#selected-detail').innerText(), /happy/);

  // Regression for the async atlas first-paint bug: the first visible canvas
  // must receive image pixels after its embedded PNG finishes loading.
  await page.waitForFunction(() => {
    const canvas = document.querySelector('.semantic-card[data-semantic="happy"] canvas');
    if (!canvas) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i] !== 3 || pixels[i + 1] !== 5 || pixels[i + 2] !== 5) return true;
    }
    return false;
  }, null, {timeout: 20000});
  const firstPaintKey = await page.locator('.semantic-card[data-semantic="happy"] canvas').getAttribute('data-paint-key');
  assert.match(firstPaintKey, /^happy\/\d+$/);
  await page.locator('#step').click();
  const steppedPaintKey = await page.locator('.semantic-card[data-semantic="happy"] canvas').getAttribute('data-paint-key');
  assert.notEqual(steppedPaintKey, firstPaintKey);
  await page.locator('.oral-item[data-semantic="sleepy"]').click();
  assert.match(await page.locator('#selected-detail').innerText(), /sleepy/);
  assert.match(await page.locator('#frame-value').innerText(), /2 \/ 118 帧/);
  await page.locator('#play').click();
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'), 'true');
  await page.locator('#play').click();
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'), 'false');

  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(requests.filter(url => !url.startsWith('file:')), []);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('E1.28 Edge smoke passed: semantic mapping, async atlas paint, controls, responsive width, no network/errors');
})().catch(error => { console.error(error); process.exitCode = 1; });
