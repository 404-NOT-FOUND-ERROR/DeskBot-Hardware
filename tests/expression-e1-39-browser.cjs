/* Offline browser QA for the explicit-Octal build comparison. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');
const root = path.resolve(__dirname, '..');
const file = path.join(root, 'artifacts/expression-octal-e1-39-20261030/index.html');
const qa = path.join(root, 'tmp/e139-octal-20261030/qa');

(async () => {
  fs.mkdirSync(qa, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.DESKBOT_BROWSER_EXECUTABLE ||
    (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('request', request => {
        if (!/^(file:|data:)/.test(request.url())) external.push(request.url());
      });
      await page.goto(pathToFileURL(file).href);
      assert.match(await page.title(), /E1\.39/);
      assert.match(await page.locator('h1').innerText(), /Octal 16\/32 MiB/);
      const data = JSON.parse(await page.locator('#e139-data').textContent());
      assert.deepEqual(data, JSON.parse(fs.readFileSync(path.join(path.dirname(file), 'report.json'), 'utf8')));
      assert.equal(data.hardware.runtime_io, 'opi_str');
      assert.equal(data.hardware.physical_flash, 'octal');
      const tables = page.locator('table');
      assert.equal(await tables.count(), 4);
      for (const [index, count] of [2, Object.keys(data.config_diffs_vs_e137_32m).length, 2, 2].entries()) {
        assert.equal(await tables.nth(index).locator('tbody tr').count(), count);
      }
      for (const [index, row] of data.builds.entries()) {
        const cfg = row.effective_app_config;
        assert.equal(cfg.ESPTOOLPY_OCT_FLASH, true);
        assert.equal(cfg.ESPTOOLPY_FLASHMODE, 'dout');
        assert.equal(cfg.ESPTOOLPY_FLASHMODE_OPI, true);
        assert.equal(cfg.ESPTOOLPY_FLASH_SAMPLE_MODE_STR, true);
        assert.equal(cfg.BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH, index === 1);
        assert.equal(row.artifacts['partition_table/partition-table.bin'].sha256,
          data.builds[0].artifacts['partition_table/partition-table.bin'].sha256);
      }
      for (const key of ['deployment_eligible', 'flash_authorized', 'runtime_verified', 'hardware_accepted', 'live_snapshot_current']) {
        assert.equal(data[key], false);
      }
      assert.ok(Object.values(data.safety).every(value => value === false));
      assert.equal(data.rollback.prewrite_identity_and_digest_refresh_required, true);
      assert.equal(data.cache_preflight.mmap_32m_upper.rejected_by_24bit_guard, false);
      assert.equal(data.cache_preflight.upper_half_runtime_verified, false);
      for (const row of data.rollback.ranges) {
        assert.equal(row.bytes, row.end_exclusive - row.offset);
        assert.equal(row.end_exclusive % 4096, 0);
      }
      assert.match(await page.locator('body').innerText(), /构建成功不等于启动成功/);
      assert.match(await page.locator('body').innerText(), /未启用 Quad cache32/);
      const details = page.locator('details');
      assert.equal(await details.count(), 1);
      await details.locator('summary').click();
      assert.equal(await details.getAttribute('open'), '');
      assert.deepEqual(JSON.parse(await page.locator('#e139-json').textContent()), data);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await details.locator('summary').click();
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      await page.screenshot({path: path.join(qa, viewport.width === 1440 ? 'desktop.png' : 'mobile.png'), fullPage: true});
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write('E1.39 Edge QA passed: desktop/mobile, paired build/config/image/rollback, expandable JSON, no overflow/errors/network.\n');
})().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n');
  process.exitCode = 1;
});
