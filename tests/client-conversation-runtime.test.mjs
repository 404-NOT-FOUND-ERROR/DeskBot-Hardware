import assert from 'node:assert/strict';
import {existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {digest} from '../integrations/client-installation/receiver.mjs';
import {createClientWithInstallation} from '../integrations/client-installation/server.mjs';
import {startConversationService, verifyClientSnapshot} from '../scripts/serve-client-conversation-20261118.mjs';

const clientRoot = process.env.DESKBOT_CONVERSATION_CLIENT;
if (!clientRoot) throw new Error('explicit_conversation_snapshot_required');
const accepted = JSON.parse(readFileSync(new URL(
  '../artifacts/client-installation-sync-20261117-r3/accepted-archive-replay.json', import.meta.url), 'utf8')).state.current;
const sourceId = 'conversation-test';
const actor = accepted.installation.character_id, device = accepted.installation.device_id;
const now = () => new Date('2026-11-18T10:00:00Z');
const proof = accepted.device_observation.value;
const secret = 'test-only-never-return-this-key';

function installation(label, persona, revision) {
  const row = structuredClone(accepted.installation);
  // Test-only installation identities with the archived device proof shape.
  row.installation_id = `fixture-installation-${label}`;
  row.shell_id = `fixture-shell-${label}`;
  row.role_name = `形态 ${label}`;
  row.profile = {...row.profile, role_id: `fixture-form-${label}`, display_name: row.role_name, persona};
  row.binding = {...row.binding, role_id: row.profile.role_id, profile_sha256: digest(row.profile)};
  row.binding_sha256 = digest(row.binding);
  Object.assign(row.request_result.target, {binding_sha256: row.binding_sha256,
    shell_id: row.shell_id, role_id: row.profile.role_id, role_name: row.role_name});
  return {schema: 'deskbot.owner-installation-handoff.v1', source_id: sourceId, source_revision: revision,
    installation: row, device: proof};
}

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), 'deskbot-conversation-'));
  const database = join(directory, 'client.sqlite'), configPath = join(directory, 'llm.json');
  writeFileSync(configPath, JSON.stringify({base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: secret}));
  let runtime, url;
  const close = async () => { if (runtime) { await runtime.close(); runtime = null; } };
  t.after(async () => { await close(); rmSync(directory, {recursive: true, force: true}); });
  return {database, configPath, directory, close, get url() { return url; }, get runtime() { return runtime; }, async open(fetchImpl) {
    runtime = await startConversationService({clientRoot, database, sourceId, configPath, fetchImpl, port: 0, now});
    url = await runtime.listen();
  }, async request(path, body, headers) {
    const response = await fetch(url + path, body === undefined ? {headers} : post(body, headers));
    return [response.status, await response.json()];
  }};
}

const post = (body, headers) => ({method: 'POST', headers: {'Content-Type': 'application/json',
  'X-DeskBot-Integration': 'shell-installation-v1', ...headers}, body: JSON.stringify(body)});
const chat = (event_id, message) => ({character_id: actor, device_id: device, event_id,
  correlation_id: `correlation-${event_id}`, role_revision: 'fixture', message});
const completion = text => Response.json({choices: [{finish_reason: 'stop', message: {content: text}}],
  usage: {prompt_tokens: 123, completion_tokens: 8, prompt_cache_hit_tokens: 12}});
const register = f => f.request('/api/devices/hello', {character_id: actor, device_id: device,
  hardware: 'esp32-s3', firmware: 'E3f-9d.3', capabilities: {'display.expression': true}});

test('startup validates source, port and config before opening SQLite', async t => {
  const f = fixture(t);
  assert.ok(verifyClientSnapshot(clientRoot).modules > 0);
  const args = {clientRoot, database: f.database, sourceId, configPath: f.configPath};
  await assert.rejects(startConversationService({...args, clientRoot: f.directory}), /client_source_mismatch/);
  await assert.rejects(startConversationService({...args, configPath: join(f.directory, 'missing.json')}), /llm_configuration_required/);
  await assert.rejects(startConversationService({...args, port: 65536}), /invalid_port/);
  assert.equal(existsSync(f.database), false);
});

test('an occupied port leaves the save untouched; another process cannot open the owned save', async t => {
  const f = fixture(t);
  const server = createServer();
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  const args = {clientRoot, database: f.database, sourceId, configPath: f.configPath, now};
  try {
    await assert.rejects(startConversationService({...args, port: server.address().port}), {code: 'EADDRINUSE'});
    assert.equal(existsSync(f.database), false);
  } finally { await new Promise(done => server.close(done)); }
  await f.open(() => { throw new Error('unexpected_model_call'); });
  await assert.rejects(startConversationService({...args, port: 0}), {code: 'conversation_store_in_use'});
  await f.close();
  await f.open(() => { throw new Error('unexpected_model_call'); });
  assert.equal((await f.request('/health'))[0], 200);
});

test('one original SQLite conversation spans A, B and restart while an active turn keeps its original shell', async t => {
  const f = fixture(t);
  const a = installation('a', '留意浅湾里的蓝色水纹。', 1);
  const b = installation('b', '偏爱拆解小齿轮。', 2);
  const messages = [];
  let enter, release;
  const entered = new Promise(done => { enter = done; });
  const waiting = new Promise(done => { release = done; });
  const model = async (url, options) => {
    assert.equal(url, 'https://api.deepseek.com/chat/completions');
    assert.equal(options.headers.authorization, `Bearer ${secret}`);
    const body = JSON.parse(options.body);
    assert.equal(body.model, 'deepseek-flash');
    messages.push(body.messages[0].content);
    if (messages.length === 1) { enter(); await waiting; }
    return completion('我记得，是青石铃。');
  };
  await f.open(model);
  assert.equal((await register(f))[0], 201);
  const acceptedA = await f.request('/api/hardware/installations', a);
  assert.equal(acceptedA[0], 200, JSON.stringify(acceptedA[1]));
  const firstBody = chat('first', '请记住我的测试物叫青石铃。');
  const first = f.request('/api/chat', firstBody);
  let firstResult;
  try {
    await Promise.race([entered, delay(3000, null, {ref: false}).then(() => assert.fail('provider did not start'))]);
    const acceptedB = await f.request('/api/hardware/installations', b);
    assert.equal(acceptedB[0], 200, JSON.stringify(acceptedB[1]));
    const [code, live] = await f.request('/api/integration/status');
    assert.equal(code, 200);
    assert.equal(live.installations[0].installation_id, b.installation.installation_id);
    assert.equal(live.model.status, 'configured');
  } finally {
    release();
    firstResult = await first;
  }
  const [firstCode, firstValue] = firstResult;
  assert.equal(firstCode, 202, JSON.stringify(firstValue));
  assert.equal(firstValue.turn.installed_presentation.installation_id, a.installation.installation_id);
  assert.equal(firstValue.turn.presentation_adoption.persona, 'adopted');
  assert.equal(firstValue.turn.presentation_adoption.voice.synthesis.status, 'not_requested');
  assert.match(messages[0], /留意浅湾里的蓝色水纹/);
  assert.doesNotMatch(messages[0], /偏爱拆解小齿轮/);

  const [secondCode, second] = await f.request('/api/chat', chat('second', '我刚才给测试物取的名字是什么？'));
  assert.equal(secondCode, 202, JSON.stringify(second));
  assert.equal(second.turn.installed_presentation.installation_id, b.installation.installation_id);
  assert.match(messages[1], /偏爱拆解小齿轮/);
  assert.ok(messages[1].includes('请记住我的测试物叫青石铃'), 'follow-up contains the original user message');
  const [, status] = await f.request('/api/integration/status');
  assert.equal(status.character_id, actor);
  assert.equal(status.last_turn.provider, 'deepseek-api-v0.1');
  assert.equal(status.last_turn.model, 'deepseek-flash');
  assert.equal(status.model.status, 'connected');
  assert.equal(status.connection.voice, 'not_connected');
  assert.doesNotMatch(JSON.stringify(status), new RegExp(secret));
  const [, usage] = await f.request('/api/life/model-usage?scope=lab&run=current');
  assert.equal(usage.totals.provider_attempts, 2);
  assert.equal(usage.filter_run_id, status.runtime.run_id);
  assert.deepEqual(await f.request('/api/integration/status', {}), [405, {error: 'status_read_only'}]);
  assert.deepEqual(await f.request('/api/chat', chat('blocked', '拒绝外站请求'), {Origin: 'https://example.test'}),
    [403, {error: 'local_origin_required'}]);
  assert.equal(messages.length, 2);

  await f.close();
  await f.open(model);
  const [replayCode, replay] = await f.request('/api/chat', firstBody);
  assert.equal(replayCode, 200);
  assert.equal(replay.duplicate, true);
  assert.deepEqual(replay.turn.installed_presentation, firstValue.turn.installed_presentation);
  assert.equal(messages.length, 2);
  const [, history] = await f.request('/api/chat/history');
  assert.equal(history.messages.length, 4);
  assert.ok(history.messages.some(event => event.text.includes('青石铃')));
  const [thirdCode, third] = await f.request('/api/chat', chat('third', '你还记得测试物的名字吗？'));
  assert.equal(thirdCode, 202, JSON.stringify(third));
  assert.equal(third.turn.installed_presentation.installation_id, b.installation.installation_id);
  assert.ok(messages[2].includes('请记住我的测试物叫青石铃'), 'restart preserves the original user message');
  const [legacyCode] = await f.request('/api/chat', {...chat('legacy-id', '你好'), character_id: 'ember-001'});
  assert.equal(legacyCode, 202);
  const [, latest] = await f.request('/api/integration/status');
  assert.equal(latest.last_turn.turn_id, 'legacy-id');
});

test('provider failure remains visible and replay cannot pay for the same failed turn again', async t => {
  const f = fixture(t);
  let calls = 0;
  await f.open(async () => { calls++; return new Response('private provider response', {status: 401}); });
  const input = {...chat('provider-error', '请回复我。'), device_id: null};
  const [status, output] = await f.request('/api/chat', input);
  assert.equal(status, 424, JSON.stringify(output));
  assert.equal(output.error, 'llm_http_error');
  assert.equal(output.turn, undefined);
  assert.doesNotMatch(JSON.stringify(output), /private provider response/);
  const [, state] = await f.request('/api/integration/status');
  assert.equal(state.last_turn, null);
  assert.equal(state.model.status, 'error');
  const [, usage] = await f.request('/api/life/model-usage?scope=lab');
  assert.equal(usage.totals.failed, 1);
  assert.equal((await f.request('/api/chat', input))[0], 409);
  assert.equal(calls, 1);
});

test('the original installation-only host keeps its fixed reply default', async t => {
  const f = fixture(t);
  const runtime = await createClientWithInstallation({clientRoot, database: f.database, sourceId, now});
  try {
    await new Promise(done => runtime.server.listen(0, '127.0.0.1', done));
    const response = await fetch(`http://127.0.0.1:${runtime.server.address().port}/api/chat`,
      post({...chat('compat', '你好'), device_id: null}));
    assert.equal(response.status, 202);
    assert.equal((await response.json()).turn.provider, 'installation-check');
  } finally {
    await new Promise(done => runtime.server.close(() => done()));
    runtime.persistence.close();
  }
});
