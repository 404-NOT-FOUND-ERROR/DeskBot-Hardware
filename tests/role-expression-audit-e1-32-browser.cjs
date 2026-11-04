/* Edge smoke for the offline E1.32 role/expression contract audit. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const htmlPath = path.resolve(__dirname, '../artifacts/role-expression-audit-e1-32-20261029/index.html').replaceAll('\\', '/');
const pageUrl = `file:///${htmlPath}`;
const edgePath = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
let browser;

(async () => {
  assert.ok(fs.existsSync(edgePath), `Edge executable is missing: ${edgePath}`);
  browser = await chromium.launch({headless: true, executablePath: edgePath});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  const externalRequests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) externalRequests.push(request.url());
  });
  await page.goto(pageUrl);
  await page.waitForFunction(() => document.querySelectorAll('#checks .check').length === 11 &&
    document.querySelectorAll('#identities tr').length === 2 &&
    document.querySelectorAll('#negative li').length === 10);

  assert.match(await page.locator('h1').innerText(), /E1\.32/);
  assert.match(await page.locator('body').innerText(), /E1\.18 基线独立重算/);
  assert.match(await page.locator('body').innerText(), /官方 E1\.18 语义\/动作事实/);
  assert.match(await page.locator('body').innerText(), /角色表情分支/);
  assert.match(await page.locator('body').innerText(), /角色动作意图分支/);
  assert.match(await page.locator('body').innerText(), /审计通过/);
  assert.equal(await page.locator('#checks .pass').count(), 11);
  assert.equal(await page.locator('#identities tr').count(), 2);
  assert.equal(await page.locator('#branches .role').count(), 2);
  assert.equal(await page.locator('#negative li .ok').count(), 10);
  assert.equal(await page.locator('#shared tr').count(), 18);
  assert.match(await page.locator('#negative').innerText(), /baseline_hash_mismatch/);
  assert.match(await page.locator('.notice').innerText(), /下一闸门是新动画导入/);

  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'the audit page must not overflow horizontally on a narrow viewport');
  assert.deepEqual(externalRequests, [], 'the offline page must not issue network requests');
  assert.deepEqual(errors, [], 'the browser page must not raise runtime errors');

  await browser.close();
  browser = null;
  console.log('E1.32 Edge smoke passed: baseline, 11 checks, 2 identities, 2 roles, 18 shared resources, 10 negative cases, narrow viewport, no network/errors');
})().catch(async error => {
  console.error(error);
  await browser?.close();
  process.exitCode = 1;
});
