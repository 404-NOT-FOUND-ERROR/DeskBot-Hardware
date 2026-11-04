'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.DESKBOT_PLAYWRIGHT_MODULE ||
  'playwright');
const edge = (process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
const root = path.resolve(__dirname, '..');
const shots = path.join(root, 'tmp/expression-e1-44-browser-20261104');
let browser;

async function check(relative, local) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('request', r => {if (!/^(file:|data:)/.test(r.url())) requests.push(r.url());});
  await page.goto('file:///' + path.join(root, relative).replaceAll('\\', '/'));
  await page.waitForFunction(() => images.current?.complete && images.current.naturalWidth > 0);
  assert.equal(await page.locator('#budget tr').count(), 3);
  assert.match(await page.locator('#budget').innerText(), /2,407,348/);
  if (local) {
    await page.waitForFunction(() => Object.keys(images).length === 3 &&
      Object.values(images).every(im => im.complete && im.naturalWidth));
    assert.equal(await page.locator('#notice').innerText(), '');
    const first = await page.locator('#eye-canvas').evaluate(c => c.toDataURL());
    assert.ok(first.length > 1000);
    await page.locator('#step').click();
    assert.match(await page.locator('#readout').innerText(), /帧 2 \/ 188/);
    await page.locator('#frame').fill('100');
    assert.notEqual(await page.locator('#eye-canvas').evaluate(c => c.toDataURL()), first);
    const mirrored = await page.locator('#eye-canvas').evaluate(c => c.toDataURL());
    await page.locator('#layout').selectOption('raw');
    assert.notEqual(await page.locator('#eye-canvas').evaluate(c => c.toDataURL()), mirrored);
    await page.locator('#layout').selectOption('mirror');
    await page.locator('#pick-eye').click();
    assert.match(await page.locator('#draft-wire').innerText(), /单眼镜像/);
    assert.equal(await page.locator('#current-wire').getAttribute('class'), 'wire old');
    assert.equal(await page.locator('#draft-wire').getAttribute('class'), 'wire new');
    await page.locator('#pick-full').click();
    assert.match(await page.locator('#selection').innerText(), /独立布局适配/);
    await page.locator('#reset').click();
    assert.equal(await page.locator('#current-wire').getAttribute('class'), 'wire');
    await page.locator('#frame').fill('187');
    assert.equal(await page.locator('#readout').innerText(), '帧 188 / 188');
    // Deterministic state steps exercise the same completion branch as RAF.
    await page.evaluate(() => advance());
    assert.match(await page.locator('#readout').innerText(), /保持末帧/);
    await page.evaluate(() => advance());
    assert.equal(await page.locator('#readout').innerText(), '帧 1 / 188');
    await page.locator('#mode').selectOption('once');
    await page.locator('#frame').fill('186');
    await page.locator('#play').click();
    await page.waitForFunction(() => document.querySelector('#readout').textContent === '帧 188 / 188' &&
      document.querySelector('#play').textContent === '播放');
    await page.locator('#mode').selectOption('loop');
    await page.locator('#frame').fill('100');
    await page.locator('#pick-eye').click();
    await page.screenshot({path: path.join(shots, 'desktop.png'), fullPage: true});
  } else {
    assert.equal(await page.locator('#play').isDisabled(), true);
    assert.equal(await page.locator('#pick-eye').isDisabled(), true);
    assert.equal(await page.locator('#full-placeholder').isVisible(), true);
    assert.equal(await page.locator('#eye-placeholder').isVisible(), true);
    assert.match(await page.locator('#notice').innerText(), /本机 tmp/);
    const payload = await page.locator('#e144-data').textContent();
    assert.ok(JSON.parse(payload).variants.every(v => v.atlas.data_url === null));
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (local) await page.screenshot({path: path.join(shots, 'mobile.png'), fullPage: true});
  assert.deepEqual(errors, []);
  assert.deepEqual(requests, []);
  await page.close();
}

(async () => {
  assert.ok(fs.existsSync(edge), 'Edge executable missing');
  fs.mkdirSync(shots, {recursive: true});
  browser = await chromium.launch({headless: true, executablePath: edge});
  await check('artifacts/expression-e1-44-adaptation-20261104/index.html', false);
  await check('tmp/expression-e1-44-adaptation-preview-20261104/index.html', true);
  await browser.close();
  const result = {result: 'passed', editions: ['public', 'local'], frame_range: [0, 187],
    checks: ['animated_node_previews', 'draft_connections_and_reset', 'once_and_loop_tail',
      'raw_and_mirror_layout', 'public_media_boundary', 'desktop_and_mobile', 'no_external_requests', 'no_page_errors']};
  fs.writeFileSync(path.join(shots, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
})().catch(async e => {console.error(e); await browser?.close(); process.exitCode = 1;});
