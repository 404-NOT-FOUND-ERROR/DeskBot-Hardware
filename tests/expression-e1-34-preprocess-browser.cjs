/* Edge smoke for the offline E1.34 candidate preprocessing workbench. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const edgePath = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const publicHtml = path.resolve(__dirname, '../artifacts/expression-e1-34-preprocess-20261029/index.html').replaceAll('\\', '/');
const localHtml = path.resolve(__dirname, '../tmp/expression-e1-34-preprocess-20261029-preview/index.html').replaceAll('\\', '/');
let browser;

async function smoke(file, local) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) external.push(request.url());
  });
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.querySelectorAll('#budget-table tr').length === 15);
  assert.match(await page.locator('h1').innerText(), /E1\.34/);
  assert.equal(await page.locator('#fps-table tr').count(), 4);
  assert.match(await page.locator('#remaining').innerText(), /162,615/);
  if (local) {
    await page.waitForFunction(() => Object.keys(images).length === 3 &&
      Object.values(images).every(image => image.complete && image.naturalWidth > 0));
    assert.equal(await page.locator('#notice').innerText(), '');
    assert.ok(await page.locator('#eye-canvas').evaluate(canvas => canvas.toDataURL().length > 1000));
    assert.match(await page.locator('#readout').innerText(), /帧 1 \/ 188/);
    await page.locator('#step').click();
    assert.match(await page.locator('#readout').innerText(), /帧 2 \/ 188/);
    await page.locator('#range').selectOption('segment');
    assert.match(await page.locator('#readout').innerText(), /帧 51 \/ 188/);
    await page.locator('#fps').selectOption('30');
    assert.match(await page.locator('#timing-readout').innerText(), /30 FPS/);
    const mirrored = await page.locator('#eye-canvas').evaluate(canvas => canvas.toDataURL());
    await page.locator('#eye-layout').selectOption('raw');
    assert.notEqual(await page.locator('#eye-canvas').evaluate(canvas => canvas.toDataURL()), mirrored);
    await page.locator('#play').click();
    assert.equal(await page.locator('#play').innerText(), '暂停');
    await page.waitForFunction(() => document.querySelector('#readout').textContent !== '帧 51 / 188');
    await page.locator('#play').click();
    assert.equal(await page.locator('#play').innerText(), '播放');
    await page.locator('#range').selectOption('product');
    assert.equal(await page.locator('#frame').getAttribute('max'), '186');
    await page.locator('#range').selectOption('full');
    assert.equal(await page.locator('#frame').getAttribute('max'), '187');
    await page.locator('#frame').fill('187');
    assert.equal(await page.locator('#readout').innerText(), '帧 188 / 188');
    await page.locator('#step').click();
    assert.equal(await page.locator('#readout').innerText(), '帧 1 / 188');
    await page.locator('#eye-layout').selectOption('mirror');
    const screenshots = path.resolve(__dirname, '../tmp/expression-e1-34-browser-20261029');
    fs.mkdirSync(screenshots, {recursive: true});
    await page.screenshot({path: path.join(screenshots, 'desktop.png'), fullPage: true});
  } else {
    await page.waitForFunction(() => images.current?.complete && images.current.naturalWidth > 0);
    assert.equal(await page.locator('#play').isDisabled(), true);
    assert.equal(await page.locator('#eye-layout').isDisabled(), true);
    assert.match(await page.locator('#notice').innerText(), /候选素材未嵌入公开副本/);
    assert.equal(await page.locator('#full-placeholder').isVisible(), true);
    assert.equal(await page.locator('#eye-placeholder').isVisible(), true);
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'E1.34 must not overflow horizontally on a narrow viewport');
  if (local) await page.screenshot({path: path.resolve(__dirname, '../tmp/expression-e1-34-browser-20261029/mobile.png'), fullPage: true});
  assert.deepEqual(external, [], 'E1.34 must not issue network requests');
  assert.deepEqual(errors, [], 'E1.34 must not raise browser errors');
  await page.close();
}

(async () => {
  assert.ok(fs.existsSync(edgePath), `Edge executable is missing: ${edgePath}`);
  assert.ok(fs.existsSync(publicHtml), 'public E1.34 page is missing');
  assert.ok(fs.existsSync(localHtml), 'local E1.34 page is missing; run builder with --local');
  browser = await chromium.launch({headless: true, executablePath: edgePath});
  await smoke(publicHtml, false);
  await smoke(localHtml, true);
  await browser.close();
  console.log('E1.34 Edge smoke passed: public/local media boundary, frame/loop/FPS/layout controls, narrow viewport, no network/errors');
})().catch(async error => {
  console.error(error);
  await browser?.close();
  process.exitCode = 1;
});
