/* Edge smoke for the offline E1.31 role-expression draft editor. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');

const htmlPath = path.resolve(__dirname, '../artifacts/role-expression-workbench-e1-31-20261029/index.html').replaceAll('\\', '/');
const pageUrl = `file:///${htmlPath}`;
const edgePath = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const temporaryRoot = path.resolve(__dirname, '../tmp/e1-31-browser-tests');
let browser;
let temporaryDirectory;

function cleanTemporaryDirectory() {
  if (!temporaryDirectory) return;
  const relative = path.relative(fs.realpathSync(temporaryRoot), fs.realpathSync(temporaryDirectory));
  assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative),
    'test cleanup must remain within the workspace temporary root');
  fs.rmSync(temporaryDirectory, {recursive: true, force: true});
  temporaryDirectory = null;
}

(async () => {
  assert.ok(fs.existsSync(edgePath), `Edge executable is missing: ${edgePath}`);
  fs.mkdirSync(temporaryRoot, {recursive: true});
  temporaryDirectory = fs.mkdtempSync(path.join(temporaryRoot, 'smoke-'));
  browser = await chromium.launch({headless: true, executablePath: edgePath});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  const externalRequests = [];
  await page.addInitScript(() => {
    window.__e131DrawCalls = [];
    const original = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (...args) {
      if (args.length === 9 && args[0] instanceof HTMLImageElement) {
        window.__e131DrawCalls.push({sw: args[3], sh: args[4], dx: args[5], dy: args[6], dw: args[7], dh: args[8]});
      }
      return original.apply(this, args);
    };
  });
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) externalRequests.push(request.url());
  });
  await page.goto(pageUrl);
  await page.waitForFunction(() => document.querySelectorAll('.semantic-row').length === 29 &&
    document.getElementById('role-count')?.textContent === '2', null, {timeout: 15000});
  assert.match(await page.locator('#status').innerText(), /E1\.31 已加载/);
  assert.equal(await page.locator('.semantic-row').count(), 29);
  assert.equal(await page.locator('#graph').innerText().then(text => text.includes('EAF 资源')), true);
  assert.equal(await page.locator('.action-intent').count(), 3);
  assert.match(await page.locator('#action-grid').innerText(), /仅为角色 profile 意图/);
  assert.match(await page.locator('#graph').innerText(), /E1\.18 基线伴随动作/);
  assert.match(await page.locator('#profile').innerText(), /合成草稿/);
  await page.waitForFunction(() => window.__e131DrawCalls.length >= 2);
  const nativeEyeDraws = await page.evaluate(() => window.__e131DrawCalls.slice(0, 2));
  assert.equal(nativeEyeDraws.length, 2, 'eye layout must draw two reflected image regions');
  assert.deepEqual(nativeEyeDraws.map(call => call.dx), [10, 0], 'eye layout must preserve the E1.19 dual-eye anchors');
  assert.ok(nativeEyeDraws.every(call => call.sw === call.dw && call.sh === call.dh),
    'eye previews must preserve native pixels without rescaling');

  const listenOptions = await page.locator('select[data-semantic="listen"] option').allTextContents();
  assert.deepEqual(listenOptions, ['未接线', 'listen.eaf']);

  await page.locator('#clone').click();
  assert.equal(await page.locator('#role-count').innerText(), '3');
  await page.locator('select[data-semantic="happy"]').selectOption('');
  assert.match(await page.locator('#graph').innerText(), /未接线/);
  assert.match(await page.locator('#preview').getAttribute('aria-label'), /圆屏/);
  assert.match(await page.locator('#status').innerText(), /仅本地草稿/);

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('#export').click(),
  ]);
  const validDraftPath = path.join(temporaryDirectory, 'roundtrip.json');
  await download.saveAs(validDraftPath);
  const validDraft = JSON.parse(fs.readFileSync(validDraftPath, 'utf8'));
  assert.equal(validDraft.firmware_deployable, false);
  assert.equal(validDraft.roles.find(row => row.role_profile.role_id === validDraft.selected_role_id)
    .semantic_resources.happy, null);
  await page.locator('#import-file').setInputFiles(validDraftPath);
  await page.waitForFunction(() => document.getElementById('status')?.textContent.includes('草稿已通过'));
  assert.equal(await page.locator('#role-count').innerText(), '3');
  assert.match(await page.locator('#graph').innerText(), /未接线/);

  const baselineResource = await page.locator('#e131-data').evaluate(element =>
    JSON.parse(element.textContent).semantics.happy.resource_id);
  await page.locator('select[data-semantic="happy"]').selectOption(baselineResource);
  assert.doesNotMatch(await page.locator('#graph').innerText(), /未接线/);
  await page.locator('#undo').click();
  assert.match(await page.locator('#graph').innerText(), /未接线/);
  await page.locator('#redo').click();
  assert.doesNotMatch(await page.locator('#graph').innerText(), /未接线/);
  await page.locator('#restore').click();
  assert.match(await page.locator('#graph').innerText(), new RegExp(baselineResource.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  await page.locator('#role-select').selectOption('astronaut-cat');
  assert.equal(await page.locator('.action-intent').count(), 3);
  assert.match(await page.locator('#action-grid').innerText(), /look_around/);
  assert.match(await page.locator('#profile').innerText(), /合成草稿/);

  const data = await page.locator('#e131-data').evaluate(element => JSON.parse(element.textContent));
  const invalidDraft = structuredClone(data.initial);
  invalidDraft.roles[0].semantic_resources.listen = 'Happy.eaf';
  await page.locator('#import-file').setInputFiles({
    name: 'incompatible-layout.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(invalidDraft)),
  });
  await page.waitForFunction(() => document.getElementById('status')?.textContent.includes('导入被拒绝'));
  assert.equal(await page.locator('#role-count').innerText(), '3', 'rejected import must preserve the current role set');

  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'the workbench must not overflow horizontally on a narrow viewport');
  assert.deepEqual(externalRequests, [], 'the offline page must not issue network requests');
  assert.deepEqual(errors, [], 'the browser page must not raise runtime errors');

  await browser.close();
  browser = null;
  cleanTemporaryDirectory();
  console.log('E1.31 Edge smoke passed: 29 semantics, layout whitelist, clone/disconnect, draft export/import, undo/redo, baseline restore, invalid import rejection, narrow viewport, no network/errors');
})().catch(async error => {
  console.error(error);
  await browser?.close();
  cleanTemporaryDirectory();
  process.exitCode = 1;
});
