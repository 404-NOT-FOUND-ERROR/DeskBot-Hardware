/* Edge smoke for the offline E1.33 import workbench. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const edgePath = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const publicHtml = path.resolve(__dirname, '../artifacts/expression-e1-33-import-20261029/index.html').replaceAll('\\', '/');
const localHtml = path.resolve(__dirname, '../tmp/expression-e1-33-import-20261029-preview/index.html').replaceAll('\\', '/');
let browser;

async function smoke(file, local) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) external.push(request.url());
  });
  await page.goto(`file:///${file}`);
  await page.waitForFunction(() => document.querySelectorAll('#audit-table tr').length === 7);
  assert.match(await page.locator('h1').innerText(), /E1\.33/);
  assert.match(await page.locator('.banner').innerText(), /部署仍被阻断/);
  assert.equal(await page.locator('#audit-table .no').count(), 3);
  assert.equal(await page.locator('#negative-list li').count(), 7);
  if(local){
    await page.waitForFunction(() => document.querySelector('#candidate-readout').textContent.includes('帧 1/188'));
    await page.locator('#candidate-step').click();
    assert.match(await page.locator('#candidate-readout').innerText(), /帧 2\/188/);
    await page.locator('#candidate-range').selectOption('loop');
    assert.match(await page.locator('#candidate-readout').innerText(), /局部 \[50,150\)/);
    await page.locator('#candidate-play').click();
    await page.waitForFunction(() => document.querySelector('#candidate-readout').textContent !== '帧 51/188 · 25 FPS · 局部 [50,150)');
    await page.locator('#candidate-play').click();
  }else{
    assert.equal(await page.locator('#candidate-play').isDisabled(),true);
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'E1.33 must not overflow horizontally on a narrow viewport');
  assert.deepEqual(external, [], 'E1.33 must not issue network requests');
  assert.deepEqual(errors, [], 'E1.33 must not raise browser errors');
  if (local) {
    assert.equal(await page.locator('#candidate-canvas').evaluate(canvas => canvas.toDataURL().length > 1000), true);
  }
  await page.close();
}

(async () => {
  assert.ok(fs.existsSync(edgePath), `Edge executable is missing: ${edgePath}`);
  assert.ok(fs.existsSync(publicHtml), 'public E1.33 page is missing');
  assert.ok(fs.existsSync(localHtml), 'local E1.33 preview page is missing; run --local');
  browser = await chromium.launch({headless: true, executablePath: edgePath});
  await smoke(publicHtml, false);
  await smoke(localHtml, true);
  await browser.close();
  console.log('E1.33 Edge smoke passed: public/local pages, controls, loop range, narrow viewport, no network/errors');
})().catch(async error => {
  console.error(error);
  await browser?.close();
  process.exitCode = 1;
});
