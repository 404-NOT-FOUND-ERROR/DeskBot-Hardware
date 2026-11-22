'use strict';
// Test the current production page; the fixture replaces only the serial bridge.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.DESKBOT_PLAYWRIGHT_MODULE || path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..'), out = path.join(root, 'tmp/studio-shy-e3f5/browser');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'artifacts/studio-resources-e3f4d/expected-directory.json')));
const device = 'device-' + 'a'.repeat(32), boot = 'boot-' + 'b'.repeat(32), session = 'session-' + 'b'.repeat(32) + '-1';
const checks = [];
let server, browser, origin;
const ready = () => ({ schema: 'deskbot.studio_control_link/v1', source: 'usb_session', connection_id: 'connection-test', target_id: device, boot_id: boot, session_id: session, enabled: true, status: 'ready', reason: 'sleep_unknown', age_ms: 50, can_submit: true, display: { application: { phase: 'settled', value: 'idle' }, page: { phase: 'settled', value: 'home' }, player: { state: 'initialized' }, expression_semantics: ['neutral', 'embarrassed', 'eat'] }, catalog: { status: 'complete', reason: 'verified', target_id: device, boot_id: boot, session_id: session, manifest: structuredClone(manifest), received_entries: 37, total: 37 }, receipts: [], pending: null, host_command: null, local_result: null, last_command: null });
const body = () => ({ schema: 'deskbot.body_observation/v2', source: 'usb_console', read_only: true, controls_available: false, generated_at: '2026-11-10T09:00:00.000Z', hardware_accessed: false, device_written: false, connection: { status: 'connected', port: 'COM4', reason: 'memory_fixture' }, freshness: 'fresh', fresh_for_ms: 30000, live: true, timeline_id: 'observation-' + 'a'.repeat(32), device_log_time_ms: 10000, last_seen_at: '2026-11-10T09:00:00.000Z', last_seen_age_ms: 50, application_state: null, internal_memory: null, events: [], dropped_lines: 0, identity: { status: 'confirmed', source: 'device_announcement', received_at: '2026-11-10T09:00:00.000Z', age_ms: 50, reason: 'memory_fixture', rejected_messages: 0, hello: { schema: 'deskbot.device_link/v1', message_type: 'hello', device_id: device, boot_id: boot, sequence: 3, uptime_ms: 31000, firmware: { edition: 'E3f-5-fixture', version: '2.0.5', app_elf_sha256: 'c'.repeat(64) }, hardware: { chip: 'esp32s3', board: 'esp_vocat', flash_bytes: 33554432, psram_bytes: 16777216 }, capabilities: ['device_identity'], transport: 'usb_console', controls_available: false, catalog_id: null, limits: { max_frame_bytes: 2048, hello_interval_ms: 10000 } } } });
async function setFixture(value) {
  const response = await fetch(origin + '/__fixture/state', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DeskBot-Fixture': 'memory' }, body: JSON.stringify(value) });
  assert.equal(response.status, 200);
}
async function requests() { return (await (await fetch(origin + '/__fixture/state')).json()).requests; }
async function start() {
  server = spawn(process.env.DESKBOT_TEST_PYTHON || 'D:/Program Files (x86)/Python/Python 3.13.0/python.exe', [path.join(root, 'tests/serve_studio_shy_e3f5_fixture.py')], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  origin = await new Promise((resolve, reject) => {
    let log = '';
    const timer = setTimeout(() => reject(Error(log)), 30000);
    server.stderr.on('data', s => { log += s; });
    server.stdout.on('data', s => { log += s; const match = log.match(/http:\/\/127\.0\.0\.1:\d+/); if (match) { clearTimeout(timer); resolve(match[0]); } });
    server.once('error', reject);
    server.once('exit', code => { clearTimeout(timer); reject(Error(code + ' ' + log)); });
  });
}
async function exercise(width) {
  const initial = ready(); initial.catalog = { status: 'idle', reason: 'not_requested' };
  await setFixture({ live: initial, body: body() });
  const context = await browser.newContext({ viewport: { width, height: 1050 }, reducedMotion: width === 320 ? 'reduce' : 'no-preference' });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(origin + '/#connection');
  const shy = page.locator('[data-e3c-action="embarrassed"]');
  await page.waitForFunction(() => DeskBotStudio.control().device?.catalog?.total === 37);
  await page.waitForFunction(() => !document.querySelector('[data-e3c-action="embarrassed"]').disabled);
  assert.match(await page.locator('#e3c-shy-binding').textContent(), /shy_candidate\.eaf.*25 fps/);
  assert.match(await shy.locator('..').textContent(), /蹭蹭/);
  checks.push('37_entry_binding_and_action_' + width);

  const old = ready(); old.display.expression_semantics = ['neutral', 'eat'];
  await setFixture({ live: old });
  await page.waitForFunction(() => DeskBotStudio.control().device?.display?.expression_semantics?.length === 2);
  assert.equal(await shy.isDisabled(), true);
  assert.equal(await page.locator('[data-e3c-action="eat"]').isEnabled(), true);
  checks.push('current_E4e_keeps_eat_and_disables_shy_' + width);

  await setFixture({ live: ready() });
  await page.waitForFunction(() => !document.querySelector('[data-e3c-action="embarrassed"]').disabled);
  for (const semantic of ['eat', 'neutral', 'embarrassed', 'neutral', 'eat', 'embarrassed']) {
    const button = page.locator(`[data-e3c-action="${semantic}"]`), before = (await requests()).length;
    await button.click();
    await page.waitForFunction(() => document.querySelector('#e3c-outcome').textContent.includes('已提交播放器'));
    await page.waitForFunction(() => !document.querySelector('[data-e3c-action="embarrassed"]').disabled);
    const posted = (await requests()).slice(before);
    assert.equal(posted.length, 1);
    assert.equal(posted[0].operation, semantic);
    checks.push('mixed_one_click_one_post_' + semantic + '_' + width + '_' + before);
  }
  const waiting = ready(); waiting.can_submit = false; waiting.display = null; waiting.age_ms = 3500;
  waiting.status = 'waiting'; waiting.reason = 'state_stale';
  await setFixture({ live: waiting });
  await page.waitForFunction(() => DeskBotStudio.control().device?.display === null);
  const before = (await requests()).length;
  await shy.click(); await page.waitForTimeout(500);
  assert.equal((await requests()).length, before);
  await setFixture({ live: ready() });
  await page.waitForFunction(() => !document.querySelector('[data-e3c-action="embarrassed"]').disabled);
  assert.equal((await requests()).length, before + 1);
  checks.push('shy_waits_for_fresh_reply_then_posts_once_' + width);

  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  checks.push('responsive_without_errors_' + width);
  await page.screenshot({ path: path.join(out, 'connection-' + width + '.png'), fullPage: true });
  await context.close();
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  try {
    await start();
    const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
    browser = await chromium.launch({ headless: true, ...(fs.existsSync(edge) ? { executablePath: edge } : {}) });
    for (const width of [1440, 390, 320]) await exercise(width);
    const report = { result: 'passed', checks, checks_passed: checks.length, production_http: true, installed_entries: 37, bridge: 'memory', hardware_accessed: false, device_written: false };
    fs.writeFileSync(path.join(out, 'validation.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  } finally {
    if (browser) await browser.close();
    if (server) server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
