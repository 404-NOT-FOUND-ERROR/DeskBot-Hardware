/* Read-only Edge smoke for the E1.35a storage preflight page. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const edgePath = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const file = path.resolve(__dirname, '../artifacts/expression-storage-preflight-e1-35a-20261029/index.html').replaceAll('\\', '/');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: edgePath});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [], externalRequests = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('request', request => {
        if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) externalRequests.push(request.url());
      });
      await page.goto(`file:///${file}`);
      assert.match(await page.title(), /E1\.35a/);
      assert.match(await page.locator('h1').innerText(), /扩容通过离线预检/);
      assert.equal(await page.locator('tbody tr').count(), 20);
      assert.match(await page.locator('body').innerText(), /1,359,872 B/);
      assert.match(await page.locator('body').innerText(), /没有原子资源 OTA/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      assert.deepEqual(externalRequests, []);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write('E1.35a browser smoke passed at desktop/mobile sizes; no script errors or network requests.\n');
})().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n');
  process.exitCode = 1;
});
