'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const html = path.join(root, 'artifacts/grok-round-transitions-20261111/index.html');
const qa = path.join(root, 'tmp/grok-round-transitions-20261111/qa');
let browser;
(async () => {
  const fs = require('node:fs'); fs.mkdirSync(qa, {recursive:true});
  browser = await chromium.launch({headless:true, executablePath:process.env.DESKBOT_BROWSER_EXE ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page = await browser.newPage({viewport:{width:1200,height:1100}});
  const errors=[], external=[];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {if (/^https?:/.test(request.url())) external.push(request.url())});
  await page.goto(pathToFileURL(html).href);
  await page.waitForFunction(() => document.querySelector('#play').disabled === false);
  await page.locator('#play').click();
  await page.waitForTimeout(350);
  assert(await page.evaluate(() => document.querySelector('#isolated').dataset.frame === document.querySelector('#continuous').dataset.frame));
  const seekTo = value => page.locator('#seek').evaluate((node, frame) => {
    node.value=String(frame); node.dispatchEvent(new Event('input', {bubbles:true}));
  }, value);
  await seekTo(40);
  assert.equal(await page.locator('#isolated').getAttribute('data-frame'), '40');
  assert.equal(await page.locator('#continuous').getAttribute('data-frame'), '40');
  assert.match(await page.locator('#state').innerText(), /开心/);
  await page.locator('#next').click();
  assert.equal(await page.locator('#continuous').getAttribute('data-frame'), '41');
  await page.locator('#previous').click();
  assert.equal(await page.locator('#continuous').getAttribute('data-frame'), '40');
  await seekTo(80);
  assert.match(await page.locator('#state').innerText(), /难过/);
  await seekTo(159);
  assert.match(await page.locator('#time').innerText(), /7\.95/);
  await page.screenshot({path:path.join(qa, 'comparison.png'), fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path:path.join(qa, 'mobile.png'), fullPage:true});
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  const report={result:'passed',scope:'synchronized EAF comparison, seeking, state labels, frame step, mobile and offline media',
    browser_errors:errors, external_requests:external};
  fs.writeFileSync(path.join(qa, 'browser-report.json'), JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report)); await browser.close();
})().catch(async error => {console.error(error); await browser?.close(); process.exitCode=1});
