/* Offline browser smoke for the E1.37 capacity comparison. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const root = path.resolve(__dirname, '..');
const file = path.join(root, 'artifacts/expression-capacity-build-e1-37b-20261030/index.html');
const qa = path.join(root, 'tmp/e137b-qa');

(async () => {
  fs.mkdirSync(qa, {recursive: true});
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.DESKBOT_BROWSER_EXECUTABLE ||
      (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('request', request => {
        if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) external.push(request.url());
      });
      await page.goto(pathToFileURL(file).href);
      assert.match(await page.title(), /E1\.37/);
      assert.match(await page.locator('h1').innerText(), /N32R16/);
      assert.equal(await page.locator('table').nth(0).locator('tbody tr').count(), 6);
      assert.equal(await page.locator('table').nth(1).locator('tbody tr').count(), 2);
      const data = JSON.parse(await page.locator('#e137-data').textContent());
      assert.equal(data.physical_flash_bytes, 32 * 1024 * 1024);
      assert.equal(data.psram_bytes, 16 * 1024 * 1024);
      assert.equal(data.upper_half_cache.upper_assets_deployment_ready, false);
      assert.equal(data.upper_half_cache.quad_cache_enabled, false);
      for (const key of ['deployment_eligible', 'flash_authorized', 'runtime_verified', 'hardware_accepted']) {
        assert.equal(data[key], false);
      }
      for (const result of Object.values(data.image_checks)) {
        assert.equal(result.only_header_capacity_and_digest_changed, true);
        assert.equal(result.segment_payloads_equal, true);
        assert.equal(result.differing_bytes_in_shared_range, 33);
      }
      const text = await page.locator('body').innerText();
      assert.match(text, /没有刷写/);
      assert.match(text, /实验功能/);
      assert.match(text, /精确现场回退|精确回退/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      await page.screenshot({path: path.join(qa, viewport.width === 1440 ? 'desktop.png' : 'mobile.png'), fullPage: true});
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write('E1.37 Edge smoke passed at desktop/mobile widths; offline status and cache limits visible; no overflow, errors or external requests.\n');
})().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n');
  process.exitCode = 1;
});
