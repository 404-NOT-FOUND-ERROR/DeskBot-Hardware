/* Read-only headless Edge checks for the E1.35b migration review. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const root = path.resolve(__dirname, '..');
const file = path.join(root, 'artifacts/expression-storage-migration-e1-35b-20261029/index.html');
const qa = path.join(root, 'tmp/e135b-qa-20261029');

(async () => {
  fs.mkdirSync(qa, {recursive: true});
  const browser = await chromium.launch({headless: true,
    executablePath: (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('request', request => {
        if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) external.push(request.url());
      });
      await page.goto(pathToFileURL(file).href);
      assert.match(await page.title(), /E1\.35b/);
      assert.match(await page.locator('h1').innerText(), /实机回退待备份/);
      assert.equal(await page.locator('#segment-table tbody tr').count(), 6);
      assert.match(await page.locator('#segment-table').innerText(), /保护；只作参考/);
      await page.locator('#backup-ranges summary').click();
      assert.equal(await page.locator('#backup-ranges tbody tr').count(), 9);
      assert.match(await page.locator('#backup-ranges tbody tr').last().innerText(), /0xEB4000/);
      assert.match(await page.locator('#backup-ranges tbody tr').last().innerText(), /2,244,608/);
      await page.locator('#member-inventory summary').click();
      const data = JSON.parse(await page.locator('#e135b-data').textContent());
      assert.equal(await page.locator('#member-inventory tbody tr').count(), data.manifest.source_inputs.asset_member_count);
      assert.equal(data.manifest.deployment_eligible, false);
      assert.equal(data.manifest.flash_authorized, false);
      assert.equal(data.manifest.rollback.device_complete, false);
      assert.deepEqual(data.manifest.write_plan.map(row => row.segment), ['model', 'assets', 'partition_table']);
      assert.match(await page.locator('body').innerText(), /不等于断电原子更新/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      await page.locator('#backup-ranges summary').click();
      await page.locator('#member-inventory summary').click();
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path: path.join(qa, viewport.width === 1440 ? 'desktop.png' : 'mobile.png'), fullPage: true});
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write('E1.35b browser checks passed at desktop/mobile sizes; backup/member tables expand, no overflow, errors or network requests.\n');
})().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n');
  process.exitCode = 1;
});
