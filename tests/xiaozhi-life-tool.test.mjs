import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once, on} from 'node:events';
import {mkdtemp, rmdir, stat, unlink, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {createArchivedLifeReader, createMcpSession, createOwnerStorySender, createRecentLifeReader} from '../integrations/client-conversation/xiaozhi-life-tool.mjs';

const characterId = 'shaping-001';
const toolName = 'deskbot_get_recent_life';
const now = Date.parse('2026-11-19T06:00:00Z');
const record = (overrides = {}) => ({
  root_outcome_id: 'fixture-outcome', actor_ids: [characterId], title: '照看苔藓',
  at: '2026-11-19T05:00:00Z', outcome: 'completed', private_note: 'fixture-private-note', ...overrides,
});
const fixture = (...records) => ({
  schema: 'deskbot.development-evidence.v1', owner_id: characterId, recent: records,
  current: [{actor_id: characterId, task_status: 'running', title: '尚未完成的计划'}],
});
const rpc = (id, method, params) => ({jsonrpc: '2.0', id, method, ...(params && {params})});
const call = id => rpc(id, 'tools/call', {name: toolName, arguments: {}});
const parseOutcome = response => JSON.parse(response.result.content[0].text);

test('only the newest completed activity for the bound individual is shared', async () => {
  const source = fixture(
    record({at: '2026-11-18T05:59:59Z', title: '已过期'}),
    record({at: '2026-11-19T06:00:01Z', title: '未来'}),
    record({at: '2026-11-19T05:59:00Z', title: '别人的活动', actor_ids: ['another-actor']}),
    record({at: '2026-11-19T05:55:00Z', title: '失败事项', outcome: 'failed'}),
    record({at: '2026-11-19T05:50:00Z', title: '换\n行'}),
    record({at: '2026-11-19T05:45:00Z', title: '修整小灯'}),
    record(),
  );
  const reader = createRecentLifeReader({clientPort: 4314, characterId, now: () => now, fetchImpl: async (url, request) => {
    assert.equal(url, 'http://127.0.0.1:4314/api/life/development?actor_id=shaping-001&limit=64');
    assert.equal(request.method, 'GET');
    assert.equal(request.redirect, 'error');
    return Response.json(source);
  }});
  assert.deepEqual(await reader(), {status: 'available', activity: '修整小灯'});
});

test('empty records, invalid source, and HTTP failure stay distinct', async () => {
  for (const [response, expected] of [
    [Response.json(fixture()), 'empty'],
    [Response.json({...fixture(record()), owner_id: 'someone-else'}), 'unavailable'],
    [Response.json({...fixture(record()), recent: null}), 'unavailable'],
    [new Response('temporarily stopped', {status: 503}), 'unavailable'],
    [new Response('[' + ' '.repeat(4 * 1024 * 1024) + ']'), 'unavailable'],
  ]) {
    const read = createRecentLifeReader({clientPort: 4314, characterId, now: () => now, fetchImpl: async () => response});
    assert.deepEqual(await read(), {status: expected});
  }
  assert.throws(() => createRecentLifeReader({clientPort: '4314', characterId}), /invalid_client_binding/);
});

test('MCP discovers one tool and refuses calls with owner or URL arguments', async () => {
  let reads = 0;
  const read = createMcpSession(async () => {reads++; return {status: 'empty'};});
  assert.equal((await read(call(0))).error.code, -32002);
  const init = await read(rpc('start', 'initialize', {protocolVersion: '2024-11-05'}));
  assert.equal(init.id, 'start');
  assert.equal(init.result.protocolVersion, '2024-11-05');
  assert.equal(await read({jsonrpc: '2.0', method: 'notifications/initialized'}), null);
  const listed = (await read(rpc(1, 'tools/list'))).result.tools;
  assert.deepEqual(listed.map(tool => tool.name), [toolName]);
  assert.equal(listed[0].annotations.readOnlyHint, true);
  assert.equal((await read(rpc(2, 'tools/call', {name: toolName, arguments: {character_id: 'other', url: 'https://example.com'}}))).error.code, -32602);
  assert.equal((await read(rpc(3, 'tools/call', {name: 'other_tool'}))).error.code, -32602);
  assert.equal(reads, 0);
  assert.deepEqual(parseOutcome(await read(call(4))), {status: 'empty'});
  assert.equal(reads, 1);
});

async function archiveFixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'deskbot-life-archive-'));
  const database = join(directory, 'life.sqlite');
  const writer = new DatabaseSync(database);
  t.after(async () => {
    writer.close();
    await unlink(database);
    await rmdir(directory);
  });
  writer.exec(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE records (namespace TEXT, record_id TEXT, payload_json TEXT, PRIMARY KEY (namespace, record_id));
  `);
  const put = writer.prepare('INSERT OR REPLACE INTO records VALUES (?, ?, ?)');
  const scope = value => put.run('runtime-profile', 'binding',
    JSON.stringify({schema: 'deskbot.store-binding.v1', kind: 'world', scope: value}));
  const world = (records, owner = characterId) => put.run('canonical-world.states', 'deskbot-small-world',
    JSON.stringify({world_id: 'deskbot-small-world', protagonist: {character_id: owner},
      memory: {development: {schema: 'deskbot.development-evidence.v1', records}}}));
  scope('live');
  return {database, scope, world};
}

test('the live archive keeps the latest completed experience on its Shanghai date', async t => {
  const {database, world} = await archiveFixture(t);
  const read = createArchivedLifeReader({database, characterId, now: () => now});
  const previous = record({title: '实际休息结果', at: '2026-10-25T21:37:27.667Z'});
  world([previous, record({actor_ids: ['other']}), record({outcome: 'failed'}),
    record({at: '2026-12-01T00:00:00Z'})]);
  assert.deepEqual(await read(), {status: 'available', activity: '实际休息结果', date: '2026-10-26', recency: 'previous'});
  const oneDay = record({at: '2026-11-18T06:00:00.000Z'});
  world([previous, oneDay]);
  assert.deepEqual(await read(), {status: 'available', activity: '照看苔藓', date: '2026-11-18', recency: 'recent'});
  world([previous, {...oneDay, at: '2026-11-18T05:59:59.999Z'}]);
  assert.deepEqual(await read(), {status: 'available', activity: '照看苔藓', date: '2026-11-18', recency: 'previous'});
});

test('the archive distinguishes missing results from a wrong store or individual', async t => {
  const {database, scope, world} = await archiveFixture(t);
  const read = createArchivedLifeReader({database, characterId, now: () => now});
  world([]);
  assert.deepEqual(await read(), {status: 'empty'});
  world([record()], 'other');
  assert.deepEqual(await read(), {status: 'unavailable'});
  world([record()]);
  scope('lab');
  assert.deepEqual(await read(), {status: 'unavailable'});
  const absent = database + '.absent';
  assert.deepEqual(await createArchivedLifeReader({database: absent, characterId})(), {status: 'unavailable'});
  await assert.rejects(stat(absent), {code: 'ENOENT'});
});

const clientRoot = process.env.DESKBOT_XIAOZHI_CLIENT;
test('share tool sends only explicit owner stories and requires a client save receipt', async () => {
  const story = '今天我看到一只青蛙。';
  const token = 'c'.repeat(64);
  let status = 201, requests = 0;
  const saveOwnerStory = createOwnerStorySender({clientPort: 4311, token, fetchImpl: async (url, options) => {
    requests++;
    assert.equal(url, 'http://127.0.0.1:4311/api/life/owner-story');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Authorization, 'Bearer ' + token);
    assert.deepEqual(JSON.parse(options.body), {text: story});
    return Response.json({status: 'recorded', event_id: 'xiaozhi-story:' + 'b'.repeat(64),
      date: '2026-11-19', duplicate: status === 200}, {status});
  }});
  const shareCall = (id, args) => rpc(id, 'tools/call', {name: 'deskbot_share_owner_story', arguments: args});
  const handle = createMcpSession(async () => ({status: 'empty'}), undefined, {saveOwnerStory});
  await handle(rpc(0, 'initialize', {protocolVersion: '2024-11-05'}));
  assert.deepEqual((await handle(rpc(1, 'tools/list'))).result.tools.map(t => t.name),
    [toolName, 'deskbot_share_owner_story']);
  for (const text of [story, '帮我记下来。', '不用帮我记一下：' + story]) {
    assert.deepEqual(parseOutcome(await handle(shareCall(2, {text}))), {status: 'explicit_share_required'});
  }
  assert.equal((await handle(shareCall(3, {text: story, character_id: 'other'}))).error.code, -32602);
  assert.equal(requests, 0);
  assert.deepEqual(parseOutcome(await handle(shareCall(4, {text: '请帮我记一下，' + story}))),
    {status: 'recorded', duplicate: false});
  status = 503;
  assert.deepEqual(parseOutcome(await handle(shareCall(5, {text: '帮我记一下' + story}))), {status: 'unavailable'});
  assert.equal(requests, 2);
});

test('real client completion records can be shared with observation sampling disabled', {skip: !clientRoot, timeout: 10000}, async t => {
  const source = file => import(pathToFileURL(join(clientRoot, 'apps/deskbot-service/src', file)));
  const [{createDeskBotServer}, {createPersistentWorld}, {syncDevelopmentEvidence}, {LIVING_RULE_VERSION}] = await Promise.all([
    source('app.mjs'), source('persistent-world.mjs'), source('development-evidence.mjs'), source('living-resources.mjs'),
  ]);
  const dayMs = 86_400_000;
  const clock = () => new Date(now);
  const at = clock().toISOString(), start = new Date(now - 2 * dayMs).toISOString();
  const original = createPersistentWorld({now: clock});
  const world = original.get();
  assert.equal(world.protagonist.character_id, characterId);
  world.tasks = [];
  world.memory = {
    schema: 'deskbot.lived-memory.v1', installed_at: start, episodes: [], seen: [], revision: 0,
    actors: {[characterId]: {actor_id: characterId, interests: {}}},
    planner: {enabled: false, requests: {}, attempts: [], recent: []},
  };
  syncDevelopmentEvidence(world, start);
  const server = createDeskBotServer({
    now: clock, persistentWorld: {...original, get: () => structuredClone(world)},
    websocket: false, autonomousLifeEnabled: false,
  });
  t.after(() => new Promise(done => {server.close(done); server.closeAllConnections();}));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const read = createRecentLifeReader({clientPort: server.address().port, characterId, now: () => now});
  assert.deepEqual(await read(), {status: 'empty'});
  const complete = (id, finishedAt) => {
    // Seed terminal tasks; the client's own projection supplies the activity title.
    world.tasks.push({
      task_id: id, actor_id: characterId, status: 'completed', kind: 'care', activity_id: 'tend-bed',
      life_goal: 'tend', location_id: 'moss-sprout-garden', started_at: start, finished_at: finishedAt,
      origin: 'autonomous_life', completion_effect: LIVING_RULE_VERSION,
      completion: {due_at: finishedAt, effect: LIVING_RULE_VERSION, result: {success: true}},
    });
    syncDevelopmentEvidence(world, at);
  };
  complete('expired', new Date(now - dayMs - 1).toISOString());
  assert.deepEqual(await read(), {status: 'empty'});
  // Exactly 24 hours is still shareable; one millisecond older is excluded.
  complete('one-day', new Date(now - dayMs).toISOString());
  const before = JSON.stringify(world);
  const expected = {status: 'available', activity: '整理和照料苗木'};
  assert.deepEqual(await read(), expected);
  assert.deepEqual(await read(), expected);
  assert.equal(JSON.stringify(world), before);
});

const bridgeRuntime = new URL('../tmp/xiaozhi-life-tool-runtime-20261119/', import.meta.url);
const testBridge = process.env.DESKBOT_XIAOZHI_BRIDGE_TEST === '1';
test('official relay discovers the stdio tool and reads fresh data across HTTP', {skip: !testBridge, timeout: 10000}, async t => {
  const {WebSocketServer} = await import('../integrations/client-4b2b/runtime.mjs');
  let source = fixture();
  let state = 200;
  const http = createServer((req, res) => {
    if (req.method !== 'GET' || req.url !== '/api/life/development?actor_id=shaping-001&limit=64') {
      res.writeHead(404).end();
    } else {
      res.writeHead(state, {'Content-Type': 'application/json'}).end(JSON.stringify(source));
    }
  });
  http.listen(0, '127.0.0.1');
  t.after(() => {http.closeAllConnections(); return new Promise(done => http.close(done));});
  await once(http, 'listening');
  const ws = new WebSocketServer({host: '127.0.0.1', port: 0});
  await once(ws, 'listening');

  const temp = await mkdtemp(join(tmpdir(), 'deskbot-mcp-relay-'));
  const manifest = join(temp, 'mcp_config.json');
  let bridge, exited, childStopped;
  let spawnError, stderr = '';
  t.after(async () => {
    try {
      const websocketClosed = new Promise(done => ws.close(done));
      for (const peer of ws.clients) peer.terminate();
      await websocketClosed;
      if (bridge?.pid && bridge.exitCode === null && bridge.signalCode === null) {
        // On Windows, close the relay's Node child before terminating Python.
        const stopped = await Promise.race([childStopped, exited, delay(2000, false, {ref: false})]);
        bridge.kill();
        await exited;
        assert.notEqual(stopped, false, 'Relay did not stop the stdio child');
      }
    } finally {
      await unlink(manifest).catch(error => {if (error.code !== 'ENOENT') throw error;});
      await rmdir(temp);
    }
  });
  await writeFile(manifest, JSON.stringify({mcpServers: {deskbot: {
    type: 'stdio', command: process.execPath, args: [
      fileURLToPath(new URL('../integrations/client-conversation/xiaozhi-life-tool.mjs', import.meta.url)),
      '--client-port', String(http.address().port), '--character-id', characterId,
    ],
  }}}));
  const ready = once(ws, 'connection', {signal: AbortSignal.timeout(4500)});
  bridge = spawn(process.env.DESKBOT_XIAOZHI_BRIDGE_PYTHON ?? 'python',
    [fileURLToPath(new URL('mcp_pipe.py', bridgeRuntime))], {
      cwd: fileURLToPath(bridgeRuntime), stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true,
      env: {...process.env, PYTHONPATH: fileURLToPath(new URL('python-packages', bridgeRuntime)),
        PYTHON_DOTENV_DISABLED: '1', MCP_ENDPOINT: 'ws://127.0.0.1:' + ws.address().port, MCP_CONFIG: manifest},
    });
  bridge.on('error', error => {spawnError = error;});
  exited = new Promise(done => bridge.once('close', done));
  childStopped = new Promise(done => {
    bridge.stderr.on('data', data => {
      stderr = (stderr + data).slice(-6000);
      if (stderr.includes('Server process terminated')) done();
    });
  });
  const [peer] = await ready.catch(error => {
    throw new Error('Local relay did not connect: ' + (spawnError?.message ?? stderr), {cause: error});
  });
  const messages = on(peer, 'message', {signal: AbortSignal.timeout(4200)});
  t.after(() => messages.return());
  const send = data => peer.send(JSON.stringify(data));
  const request = async data => {
    send(data);
    const {value: [frame]} = await messages.next();
    const reply = JSON.parse(frame.toString('utf8'));
    assert.equal(reply.id, data.id);
    return reply;
  };
  assert.equal((await request(rpc(1, 'initialize', {protocolVersion: '2025-06-18',
    capabilities: {}, clientInfo: {name: 'Local test', version: '1.0.0'}}))).result.protocolVersion, '2025-06-18');
  send({jsonrpc: '2.0', method: 'notifications/initialized'});
  assert.equal((await request(rpc(2, 'tools/list'))).result.tools[0].name, toolName);
  assert.deepEqual(parseOutcome(await request(call(3))), {status: 'empty'});
  source = fixture(record({title: '修整小灯', at: new Date(Date.now() - 2000).toISOString()}));
  assert.deepEqual(parseOutcome(await request(call(4))), {status: 'available', activity: '修整小灯'});
  source = fixture(record({title: '照看苔藓', at: new Date(Date.now() - 1000).toISOString()}));
  assert.deepEqual(parseOutcome(await request(call(5))), {status: 'available', activity: '照看苔藓'});
  state = 503;
  assert.deepEqual(parseOutcome(await request(call(6))), {status: 'unavailable'});
});
