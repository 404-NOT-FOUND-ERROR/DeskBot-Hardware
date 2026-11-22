import {createInterface} from 'node:readline';
import {appendFileSync, readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';

const DAY_MS = 86_400_000;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const DEVELOPMENT = 'deskbot.development-evidence.v1';
const WORLD_ID = 'deskbot-small-world';
const shanghaiDate = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', dateStyle: 'short'});
const PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05'];
const TOOL = {
  name: 'deskbot_get_recent_life',
  description: '主人问你在聚形域最近或上一次做了什么、在忙什么时，先调用。available 是一条已完成的活动，用一句自然口语讲述。recency 为 previous 时说出 date，称它为上一次的经历。标题「实际休息结果」可以说成「我休息了一次」。只使用返回的事实。empty 表示没有可分享的记录，unavailable 表示暂时读不到。',
  inputSchema: {type: 'object', properties: {}, additionalProperties: false},
  annotations: {readOnlyHint: true},
};
const SHARE_TOOL = {
  name: 'deskbot_share_owner_story',
  description: '主人明确说「帮我记一下」或「请帮我记一下」并带上一段要记的见闻时调用。text 填完整原话，含开头的记下请求和主人刚说的内容，保留人称和事实。只有 recorded 才回答已经记下。其他结果告诉主人这次暂未保存。',
  inputSchema: {
    type: 'object',
    properties: {text: {type: 'string', description: '主人本轮原话，含开头的「帮我记一下」和要保存的内容。', maxLength: 320}},
    required: ['text'],
    additionalProperties: false,
  },
  annotations: {readOnlyHint: false, destructiveHint: false},
};

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const isCharacterId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(value);

function latestCompleted(records, characterId, nowMs) {
  let latest = null;
  for (const result of records) {
    if (result?.outcome !== 'completed' || !Array.isArray(result.actor_ids) ||
        !result.actor_ids.includes(characterId) || typeof result.at !== 'string' ||
        typeof result.title !== 'string' || typeof result.root_outcome_id !== 'string' || !result.root_outcome_id) continue;
    const atMs = Date.parse(result.at);
    if (!Number.isFinite(atMs) || atMs > nowMs) continue;
    // The client derives these titles from its activity catalogue.
    const activity = result.title.trim();
    if (!activity || activity.length > 80 || /[\p{C}\p{Zl}\p{Zp}]/u.test(activity)) continue;
    if (latest === null || atMs > latest.atMs) latest = {activity, atMs};
  }
  return latest;
}

export function createArchivedLifeReader({database, characterId, now = Date.now}) {
  if (typeof database !== 'string' || !database.trim() || !isCharacterId(characterId)) {
    throw new Error('invalid_archive_binding');
  }
  const filename = resolve(database);
  return async () => {
    let archive;
    try {
      archive = new DatabaseSync(filename, {readOnly: true});
      // Both records come from one SQLite snapshot while the client may be writing.
      const rows = archive.prepare(`
        SELECT namespace, payload_json FROM records
        WHERE (namespace = 'runtime-profile' AND record_id = 'binding')
           OR (namespace = 'canonical-world.states' AND record_id = ?)
      `).all(WORLD_ID);
      const stored = Object.fromEntries(rows.map(row => [row.namespace, JSON.parse(row.payload_json)]));
      const binding = stored['runtime-profile'];
      const world = stored['canonical-world.states'];
      if (binding?.schema !== 'deskbot.store-binding.v1' || binding.scope !== 'live' || binding.kind !== 'world' ||
          world?.world_id !== WORLD_ID || world.protagonist?.character_id !== characterId) return {status: 'unavailable'};

      const development = world.memory?.development;
      if (development == null) return {status: 'empty'};
      const nowMs = now();
      if (development.schema !== DEVELOPMENT || !Array.isArray(development.records) || !Number.isFinite(nowMs)) {
        return {status: 'unavailable'};
      }
      const latest = latestCompleted(development.records, characterId, nowMs);
      if (!latest) return {status: 'empty'};
      return {
        status: 'available',
        activity: latest.activity,
        date: shanghaiDate.format(latest.atMs),
        recency: nowMs - latest.atMs <= DAY_MS ? 'recent' : 'previous',
      };
    } catch {
      return {status: 'unavailable'};
    } finally {
      archive?.close();
    }
  };
}

async function readDevelopment(response) {
  if (response.status !== 200 || !response.body) {
    await response.body?.cancel();
    throw new Error('development_unavailable');
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.byteLength;
    if (size > MAX_RESPONSE_BYTES) throw new Error('development_too_large');
    chunks.push(chunk);
  }
  return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks, size)));
}

export function createRecentLifeReader({clientPort, characterId, now = Date.now, fetchImpl = fetch}) {
  if (!Number.isInteger(clientPort) || clientPort < 1 || clientPort > 65535 ||
      !isCharacterId(characterId)) {
    throw new Error('invalid_client_binding');
  }
  const endpoint = 'http://127.0.0.1:' + clientPort + '/api/life/development?actor_id=' + characterId + '&limit=64';
  return async () => {
    try {
      const response = await fetchImpl(endpoint, {
        method: 'GET',
        headers: {Accept: 'application/json'},
        redirect: 'error',
        signal: AbortSignal.timeout(1500),
      });
      const source = await readDevelopment(response);
      const nowMs = now();
      if (source?.schema !== DEVELOPMENT || source.owner_id !== characterId ||
          !Array.isArray(source.recent) || !Number.isFinite(nowMs)) return {status: 'unavailable'};
      const latest = latestCompleted(source.recent, characterId, nowMs);
      return latest && nowMs - latest.atMs <= DAY_MS ? {status: 'available', activity: latest.activity} : {status: 'empty'};
    } catch {
      return {status: 'unavailable'};
    }
  };
}

export function createOwnerStorySender({clientPort, token, fetchImpl = fetch}) {
  if (!Number.isInteger(clientPort) || clientPort < 1 || clientPort > 65535 || !/^[0-9a-f]{64}$/.test(token)) {
    throw new Error('invalid_owner_story_binding');
  }
  const endpoint = 'http://127.0.0.1:' + clientPort + '/api/life/owner-story';
  return async utterance => {
    const original = utterance.trim();
    const request = original.match(/^(?:请)?帮我(?:记一下|记下来|记下|记录一下)[\s，。,:：、]*/u);
    const text = request && original.slice(request[0].length).trim();
    if (!text || !/[\p{L}\p{N}]/u.test(text) || text.length > 300 || /[\p{C}\p{Zl}\p{Zp}]/u.test(text)) {
      return {status: 'explicit_share_required'};
    }
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(8000),
        headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      if (response.status !== 200 && response.status !== 201) {
        await response.body?.cancel();
        return {status: 'unavailable'};
      }
      const receipt = await response.json();
      if (receipt?.status === 'recorded' && /^xiaozhi-story:[0-9a-f]{64}$/.test(receipt.event_id)
          && /^\d{4}-\d{2}-\d{2}$/.test(receipt.date) && receipt.duplicate === (response.status === 200)) {
        return {status: 'recorded', duplicate: receipt.duplicate};
      }
    } catch {
      // Only an explicit client receipt is reported as saved.
    }
    return {status: 'unavailable'};
  };
}

export function createMcpSession(readRecentLife, observe = () => {}, {saveOwnerStory} = {}) {
  const ok = (id, result) => ({jsonrpc: '2.0', id, result});
  const error = (id, code, message) => ({jsonrpc: '2.0', id, error: {code, message}});
  let initialized = false;
  return async request => {
    if (!isObject(request) || request.jsonrpc !== '2.0' || typeof request.method !== 'string') {
      return error(null, -32600, 'Invalid Request');
    }
    if (!Object.hasOwn(request, 'id')) return null; // Notifications have no response.
    const {id, method, params} = request;
    if (typeof id !== 'string' && !Number.isFinite(id)) return error(null, -32600, 'Invalid Request');
    if (params !== undefined && !isObject(params)) return error(id, -32602, 'Invalid params');
    if (method === 'initialize') {
      if (initialized) return error(id, -32600, 'Already initialized');
      if (typeof params?.protocolVersion !== 'string') return error(id, -32602, 'Invalid protocol version');
      const protocolVersion = PROTOCOL_VERSIONS.includes(params.protocolVersion) ? params.protocolVersion : PROTOCOL_VERSIONS[0];
      initialized = true;
      return ok(id, {protocolVersion, capabilities: {tools: {}}, serverInfo: {name: 'DeskBotLife', version: '0.1.0'}});
    }
    if (method === 'ping') return ok(id, {});
    if (!initialized) return error(id, -32002, 'Not initialized');
    if (method === 'tools/list') {
      observe('tools/list');
      return ok(id, {tools: saveOwnerStory ? [TOOL, SHARE_TOOL] : [TOOL]});
    }
    if (method === 'tools/call') {
      if (saveOwnerStory && params?.name === SHARE_TOOL.name) {
        const args = params.arguments;
        if (!isObject(args) || Object.keys(args).length !== 1 || typeof args.text !== 'string' || args.text.length > 320) {
          return error(id, -32602, 'Expected only text for deskbot_share_owner_story');
        }
        const result = await saveOwnerStory(args.text);
        observe('tools/share ' + result.status);
        return ok(id, {content: [{type: 'text', text: JSON.stringify(result)}]});
      }
      if (params?.name !== TOOL.name || (params.arguments !== undefined &&
          (!isObject(params.arguments) || Object.keys(params.arguments).length !== 0))) {
        return error(id, -32602, 'Expected deskbot_get_recent_life without arguments');
      }
      const result = await readRecentLife();
      observe('tools/call ' + result.status);
      return ok(id, {content: [{type: 'text', text: JSON.stringify(result)}]});
    }
    return error(id, -32601, 'Method not found');
  };
}

async function main() {
  const {values} = parseArgs({options: {
    'client-port': {type: 'string'}, 'archive-database': {type: 'string'},
    'character-id': {type: 'string'}, 'status-log': {type: 'string'},
    'share-client-port': {type: 'string'}, 'share-token-file': {type: 'string'},
  }});
  const database = values['archive-database'];
  const clientPort = values['client-port'];
  const characterId = values['character-id'];
  if ((database === undefined) === (clientPort === undefined)) throw new Error('expected_one_life_source');
  const readRecentLife = database === undefined
    ? createRecentLifeReader({clientPort: Number(clientPort), characterId})
    : createArchivedLifeReader({database, characterId});
  const statusLog = values['status-log'];
  const shareTokenFile = values['share-token-file'];
  if ((shareTokenFile && !database) || (values['share-client-port'] && !shareTokenFile)) {
    throw new Error('owner_story_requires_archive_binding');
  }
  const saveOwnerStory = shareTokenFile ? createOwnerStorySender({
    token: readFileSync(shareTokenFile, 'utf8').trim(),
    clientPort: Number(values['share-client-port'] ?? 4311),
  }) : undefined;
  function observe(event) {
    process.stderr.write('DeskBotLife ' + event + '\n');
    if (!statusLog) return;
    try {
      // Store status only, so this log can be used during voice acceptance.
      appendFileSync(statusLog, JSON.stringify({at: new Date().toISOString(), event}) + '\n');
    } catch {
      process.stderr.write('DeskBotLife: status log unavailable.\n');
    }
  }
  const handle = createMcpSession(readRecentLife, observe, {saveOwnerStory});
  for await (const line of createInterface({input: process.stdin, crlfDelay: Infinity})) {
    if (!line.trim()) continue;
    let request;
    try { request = JSON.parse(line); } catch {
      process.stdout.write('{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}\n');
      continue;
    }
    const response = await handle(request);
    if (response) process.stdout.write(JSON.stringify(response) + '\n');
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(() => {
    process.stderr.write('DeskBotLife: specify --character-id and one of --client-port or --archive-database.\n');
    process.exitCode = 1;
  });
}
