'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');
const root = path.resolve(__dirname, '..');
const edge = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const out = path.join(root, 'tmp/expression-e1-45-browser-20261104');
let browser;

async function smoke(relative, local) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {if (!/^(file:|data:)/.test(request.url())) requests.push(request.url());});
  await page.goto('file:///' + path.join(root, relative).replaceAll('\\', '/'));
  await page.waitForFunction(() => images.current?.complete && images.current.naturalWidth > 0);
  assert.match(await page.locator('h1').innerText(), /单眼镜像版已选定/);
  assert.match(await page.locator('#images').innerText(), /5,981,260/);
  assert.match(await page.locator('#images').innerText(), /2,407,348/);
  assert.match(await page.locator('#image-hash').innerText(), /e5ac78a1/);
  if (local) {
    await page.waitForFunction(() => images.eye?.complete && images.eye.naturalWidth > 0);
    assert.equal(await page.locator('#notice').innerText(), '');
    assert.ok(await page.locator('#eye').evaluate(canvas => canvas.toDataURL().length > 1000));
    await page.locator('#step').click();
    assert.equal(await page.locator('#readout').innerText(), '帧 2 / 188');
    await page.locator('#frame').fill('187');
    await page.evaluate(() => advance());
    assert.match(await page.locator('#readout').innerText(), /末帧保持/);
    await page.evaluate(() => advance());
    assert.equal(await page.locator('#readout').innerText(), '帧 1 / 188');
    await page.locator('#play').click();
    await page.waitForFunction(() => document.querySelector('#readout').textContent !== '帧 1 / 188');
    await page.locator('#play').click();
    await page.locator('#frame').fill('100');
    await page.screenshot({path: path.join(out, 'desktop.png'), fullPage: true});
  } else {
    assert.equal(await page.locator('#play').isDisabled(), true);
    assert.equal(await page.locator('#frame').isDisabled(), true);
    const data = JSON.parse(await page.locator('#e145-data').textContent());
    assert.equal(data.eye.data_url, null);
    assert.match(await page.locator('#notice').innerText(), /本机版本/);
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (local) await page.screenshot({path: path.join(out, 'mobile.png'), fullPage: true});
  assert.deepEqual(errors, []);
  assert.deepEqual(requests, []);
  await page.close();
}

(async () => {
  assert.ok(fs.existsSync(edge));
  fs.mkdirSync(out, {recursive: true});
  browser = await chromium.launch({headless: true, executablePath: edge});
  await smoke('artifacts/expression-e1-45-staging-20261104/index.html', false);
  await smoke('tmp/expression-e1-45-staging-preview-20261104/index.html', true);
  await browser.close();
  const result = {result: 'passed', editions: ['public', 'local'],
    checks: ['selected_eye_resource', 'before_after_preview', 'frame_controls_and_tail', 'package_budget_and_hash',
      'public_media_boundary', 'desktop_mobile', 'no_external_requests', 'no_page_errors']};
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
})().catch(async error => {console.error(error); await browser?.close(); process.exitCode = 1;});
