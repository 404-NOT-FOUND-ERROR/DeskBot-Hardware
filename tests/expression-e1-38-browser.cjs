/* Offline browser QA for the E1.38 addressing and rollback audit. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');
const root = path.resolve(__dirname, '..');
const file = path.join(root, 'artifacts/expression-addressing-e1-38-20261030/index.html');
const qa = path.join(root, 'tmp/e138-addressing/qa');

(async () => {
  fs.mkdirSync(qa, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.DESKBOT_BROWSER_EXECUTABLE ||
    (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [], external = [];
      page.on('pageerror', err => errors.push(String(err)));
      page.on('request', request => {
        if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) external.push(request.url());
      });
      await page.goto(pathToFileURL(file).href);
      assert.match(await page.title(), /E1\.38/);
      assert.match(await page.locator('h1').innerText(), /N32R16/);
      assert.equal(await page.locator('#config tbody tr').count(), 6);
      assert.equal(await page.locator('#capacity tbody tr').count(), 3);
      assert.equal(await page.locator('#mmap tbody tr').count(), 3);
      assert.equal(await page.locator('#rollback tbody tr').count(), 2);
      const data = JSON.parse(await page.locator('#e138-data').textContent());
      assert.equal(data.hardware.runtime_io, 'opi_str');
      assert.equal(data.hardware.efuse_flash_type, 'octal');
      assert.equal(data.hardware.commercial_part_number, null);
      assert.equal(data.capacity_cases[1].expected_driver_limit_bytes, 16 * 1024 * 1024);
      assert.equal(data.mmap_checks[2].rejected_by_24bit_guard, true);
      assert.equal(data.rollback.ranges[0].bytes, 16384);
      assert.equal(data.rollback.ranges[1].bytes, 4481024);
      for (const key of ['deployment_eligible', 'flash_authorized', 'runtime_verified', 'hardware_accepted', 'live_snapshot_current']) {
        assert.equal(data[key], false);
      }
      assert.ok(Object.values(data.safety).every(value => value === false));
      assert.match(await page.locator('body').innerText(), /没有刷写或重启/);
      assert.match(await page.locator('body').innerText(), /只刷 App 不会解除/);
      assert.match(await page.locator('body').innerText(), /新 Octal 构建须重算扇区范围/);
      const details = page.locator('details');
      assert.equal(await details.count(), 11);
      await details.nth(0).locator('summary').click();
      assert.equal(await details.nth(0).getAttribute('open'), '');
      await details.nth(0).locator('summary').click();
      await details.nth(2).locator('summary').click();
      assert.match(await details.nth(2).innerText(), /ESP_ERR_INVALID_ARG/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      await page.screenshot({path: path.join(qa, viewport.width === 1440 ? 'desktop.png' : 'mobile.png'), fullPage: true});
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write('E1.38 Edge QA passed: desktop/mobile, capacity/guard/sector rows, expandable source evidence, no overflow/errors/network.\n');
})().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n');
  process.exitCode = 1;
});
