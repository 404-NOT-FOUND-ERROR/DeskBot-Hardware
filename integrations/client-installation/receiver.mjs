// Portable client extension: installation facts use the client's original SQLite.
import {createHash} from 'node:crypto';

export const HANDOFF_SCHEMA = 'deskbot.owner-installation-handoff.v1';
export const MAX_BODY = 128 * 1024;
const INSTALLATIONS = 'hardware.installations';
const CURRENT = 'hardware.current-installation';
const SHA = /^[a-f0-9]{64}$/;
const ROLE = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const clone = value => structuredClone(value);
export const canonical = value => JSON.stringify(sort(value));
function sort(value) {
  if (Array.isArray(value)) return value.map(sort);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, sort(value[key])]));
  return value;
}
export const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
function fail(code, status = 422) { throw Object.assign(new Error(code), {code, status}); }
function requireValue(test, code = 'invalid_installation') { if (!test) fail(code); }
// Match Python's code-point limits and str.strip(), used by the source profile validator.
const pythonWhitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u;
function nonblank(value) { return typeof value === 'string' && !pythonWhitespace.test(value); }
function limited(value, limit) { return typeof value === 'string' && [...value].length <= limit; }
function text(value, limit = 128, empty = false, multiline = false) {
  return limited(value, limit) && (empty || nonblank(value))
    && !(multiline ? /[\x00-\x08\x0b\x0c\x0e-\x1f]/ : /[\x00-\x1f]/).test(value);
}
function keys(value, required, optional = []) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && required.every(key => Object.hasOwn(value, key))
    && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
}
function integer(value) { return Number.isSafeInteger(value) && value >= 0; }
function role(value) { return text(value, 64) && ROLE.test(value); }
function sha(value) { return typeof value === 'string' && value.length === 64 && SHA.test(value); }
function sourceText(value) {
  return text(value, 256) && !pythonWhitespace.test(value[0]) && !pythonWhitespace.test(value.at(-1));
}

function legacyValid(legacy) {
  requireValue(keys(legacy, ['source_sha256', 'profile']), 'invalid_legacy_profile');
  const p = legacy.profile;
  requireValue(keys(p, ['schema', 'shell_id', 'magnet_profile_id', 'role_id', 'display_name', 'voice_id',
    'catchphrases', 'expression_map', 'action_map', 'asset_version', 'profile_revision', 'binding_state'], ['notes', 'binding_version'])
    && p.schema === 'deskbot.role_profile.v1' && role(p.shell_id) && role(p.role_id)
    && limited(p.magnet_profile_id, 128) && nonblank(p.magnet_profile_id)
    && limited(p.display_name, 80) && nonblank(p.display_name)
    && limited(p.voice_id, 96) && nonblank(p.voice_id) && limited(p.asset_version, 96) && nonblank(p.asset_version)
    && integer(p.profile_revision) && p.profile_revision > 0 && ['draft', 'confirmed', 'disabled'].includes(p.binding_state)
    && new Set([p.shell_id, p.role_id, p.magnet_profile_id]).size === 3
    && (!Object.hasOwn(p, 'notes') || limited(p.notes, 1000))
    && (!Object.hasOwn(p, 'binding_version') || (text(p.binding_version, 96)
      && /^[A-Za-z][A-Za-z0-9_.-]*\/v[1-9][0-9]*$/.test(p.binding_version)))
    && Array.isArray(p.catchphrases) && p.catchphrases.length <= 32
    && p.catchphrases.every(phrase => limited(phrase, 160)), 'invalid_legacy_profile');
  const actions = new Set(['shark_head', 'shark_head_decay', 'look_around', 'beat_swing', 'cat_nuzzle']);
  for (const field of ['expression_map', 'action_map']) {
    const map = p[field];
    requireValue(map && typeof map === 'object' && !Array.isArray(map) && Object.keys(map).length <= 64
      && Object.entries(map).every(([key, value]) => role(key) && role(value)
        && (field !== 'action_map' || actions.has(value))), 'invalid_legacy_profile');
  }
  requireValue(legacy.source_sha256 === digest(p), 'legacy_hash_mismatch');
}

function profileValid(profile, binding) {
  requireValue(keys(profile, ['schema', 'role_id', 'display_name', 'persona', 'voice', 'expression_role_id',
    'expression_graph_sha256', 'preview_catalog_id', 'profile_revision'], ['legacy']), 'invalid_profile');
  requireValue(profile.schema === 'deskbot.role_profile.v2' && role(profile.role_id)
    && role(profile.expression_role_id) && text(profile.display_name, 80)
    && text(profile.persona, 2000, true, true) && integer(profile.profile_revision) && profile.profile_revision > 0
    && sha(profile.expression_graph_sha256) && sha(profile.preview_catalog_id), 'invalid_profile');
  const voice = profile.voice;
  requireValue(keys(voice, ['provider', 'voice_id', 'speaking_style'])
    && ['unconfigured', 'xiaozhi', 'gateway'].includes(voice.provider)
    && text(voice.voice_id, 96, true) && text(voice.speaking_style, 500, true, true), 'invalid_voice_profile');
  requireValue(keys(binding, ['role_id', 'profile_revision', 'profile_sha256', 'expression_role_id',
    'expression_graph_sha256', 'preview_catalog_id', 'source', 'confirmation']), 'invalid_binding');
  requireValue(binding.confirmation === 'owner_choice_local', 'invalid_binding_confirmation');
  if (binding.source !== null) {
    const source = binding.source;
    requireValue(keys(source, ['key', 'session_id', 'capture_shell_id', 'device_id', 'magnet_profile_id', 'archive_sha256'])
      && ['key', 'session_id', 'capture_shell_id', 'device_id', 'magnet_profile_id'].every(key => sourceText(source[key]))
      && (source.archive_sha256 === null || sha(source.archive_sha256)), 'invalid_binding_source');
  }
  if (Object.hasOwn(profile, 'legacy')) legacyValid(profile.legacy);
  requireValue(binding.role_id === profile.role_id && binding.profile_revision === profile.profile_revision
    && binding.profile_sha256 === digest(profile) && binding.expression_role_id === profile.expression_role_id
    && binding.expression_graph_sha256 === profile.expression_graph_sha256
    && binding.preview_catalog_id === profile.preview_catalog_id, 'binding_profile_mismatch');
}

function deviceValid(device, record) {
  requireValue(device && device.device_id === record.device_id
    && ['connection_id', 'boot_id', 'session_id'].every(key => text(device[key]))
    && integer(device.sequence) && device.graph_sha256 === record.profile.expression_graph_sha256
    && ['actual', 'current', 'target', 'saved'].every(key => device[key] === record.profile.expression_role_id)
    && ['saved', 'restored'].includes(device.save_state)
    && device.activation && ['idle', 'applied', 'cancelled', 'failed'].includes(device.activation.status), 'device_not_saved');
}

export function validateHandoff(value) {
  requireValue(keys(value, ['schema', 'source_id', 'source_revision', 'installation', 'device']), 'invalid_handoff');
  requireValue(value.schema === HANDOFF_SCHEMA && text(value.source_id) && integer(value.source_revision), 'invalid_source');
  const row = value.installation;
  requireValue(row && typeof row === 'object' && !Array.isArray(row), 'invalid_installation');
  requireValue(['installation_id', 'character_id', 'device_id', 'shell_id'].every(key => text(row[key]))
    && /^device-[a-f0-9]{32}$/.test(row.device_id) && role(row.shell_id)
    && text(row.shell_name, 40) && text(row.role_name, 80)
    && (row.plan_id === null || text(row.plan_id)) && row.scope === 'device_expression'
    && row.source === 'owner_manual' && row.phase === 'saved' && row.first_frame === true && row.saved === true
    && typeof row.confirmed_at === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(row.confirmed_at)
    && Number.isFinite(Date.parse(row.confirmed_at)), 'installation_not_saved');
  profileValid(row.profile, row.binding);
  requireValue(row.role_name === row.profile.display_name, 'binding_profile_mismatch');
  requireValue(row.binding_sha256 === digest(row.binding), 'binding_hash_mismatch');
  const command = row.command;
  requireValue(command && text(command.command_id) && command.target_id === row.device_id
    && ['connection_id', 'boot_id', 'session_id'].every(key => text(command[key]))
    && new RegExp('^role\\.activate\\.\\d+\\.' + row.profile.expression_graph_sha256 + '$').test(command.operation), 'invalid_command_proof');
  deviceValid(row.device_result, row);
  if (row.completion_source === 'correlated_device_reply') {
    const result = row.request_result, device = row.device_result;
    requireValue(result && result.status === 'saved' && result.first_frame === true
      && result.command_id === command.command_id && result.operation === command.operation
      && text(result.request_id) && result.request_id === device.activation.request_id
      && device.activation.status === 'applied'
      && ['connection_id', 'boot_id', 'session_id'].every(key => device[key] === command[key])
      && result.target?.binding_sha256 === row.binding_sha256
      && result.target?.shell_id === row.shell_id
      && result.target?.shell_name === row.shell_name && result.target?.role_name === row.role_name
      && result.target?.role_id === row.profile.role_id && result.target?.profile_revision === row.profile.profile_revision
      && result.target?.expression_role_id === row.profile.expression_role_id
      && result.target?.expression_graph_sha256 === row.profile.expression_graph_sha256
      // Manual installation is the owner target; binding.source separately retains magnetic provenance.
      && result.target?.ready === true && result.target?.source === null
      && integer(result.index) && command.operation === `role.activate.${result.index}.${row.profile.expression_graph_sha256}`, 'receipt_mismatch');
  } else {
    // Recovery can prove a saved result by a fresh query without fabricating a receipt.
    requireValue(row.completion_source === 'device_snapshot', 'invalid_completion_source');
  }
  deviceValid(value.device, row);
  return value;
}

export function createInstallationReceiver({persistence, sourceId, characterId, currentCharacterId,
  getDevice, now = () => new Date()}) {
  if (!persistence?.transaction || !text(sourceId) || !text(characterId) || typeof getDevice !== 'function'
      || typeof currentCharacterId !== 'function') throw new Error('installation_receiver_dependencies_required');
  const pair = deviceId => `${characterId}:${deviceId}`;
  function identity(deviceId, actorId) {
    const device = getDevice(deviceId);
    if (actorId !== characterId || currentCharacterId() !== characterId || device?.character_id !== characterId)
      fail('installation_identity_conflict', 409);
  }
  function getInstallation({ device_id: deviceId = null, character_id: actorId = characterId, installation_id: installationId = null } = {}) {
    if (deviceId) {
      identity(deviceId, actorId);
      if (!installationId) return persistence.get(CURRENT, pair(deviceId));
      const selected = persistence.get(INSTALLATIONS, `${pair(deviceId)}:${installationId}`);
      if (selected && (selected.installation?.character_id !== actorId || selected.installation?.device_id !== deviceId))
        fail('installation_identity_conflict', 409);
      return selected;
    }
    if (installationId) fail('installation_device_required', 400);
    if (actorId !== characterId || currentCharacterId() !== characterId) fail('installation_identity_conflict', 409);
    const current = persistence.list(CURRENT).filter(row => row.installation?.character_id === actorId);
    if (current.length > 1) fail('installation_device_required', 409);
    if (current.length === 0) return null;
    identity(current[0].installation.device_id, actorId);
    return current[0];
  }
  function state(deviceId, actorId = characterId, installationId = null) {
    identity(deviceId, actorId);
    const current = persistence.get(CURRENT, pair(deviceId));
    const selectedInstallation = installationId
      ? persistence.get(INSTALLATIONS, `${pair(deviceId)}:${installationId}`)
      : null;
    if (selectedInstallation && (selectedInstallation.installation?.character_id !== actorId
        || selectedInstallation.installation?.device_id !== deviceId)) fail('installation_identity_conflict', 409);
    return {schema: 'deskbot.client-installation-state.v1', character_id: characterId, device_id: deviceId,
      current, ...(installationId ? {selected_installation: selectedInstallation} : {}),
      scope: 'installation_facts', live_device_state: false,
      queried_at: now().toISOString()};
  }
  function accept(input) {
    const value = clone(validateHandoff(input)), row = value.installation;
    identity(row.device_id, row.character_id);
    if (value.source_id !== sourceId) fail('installation_source_conflict', 409);
    const hash = digest(row), key = pair(row.device_id), recordKey = `${key}:${row.installation_id}`;
    // Both writes use the original transaction, including all conflict checks.
    return persistence.transaction(() => {
      const previous = persistence.get(INSTALLATIONS, recordKey), current = persistence.get(CURRENT, key);
      if (current && current.source_id !== sourceId) fail('installation_source_conflict', 409);
      if (previous) {
        if (previous.installation_sha256 !== hash || previous.source_id !== sourceId)
          fail('installation_content_conflict', 409);
        // A repeated old installation never becomes current, even with a higher query revision.
        return {accepted: true, duplicate: true, receipt: clone(previous.receipt), current_installation_id: current?.installation_id ?? null};
      }
      if (current && value.source_revision <= current.source_revision) fail('installation_revision_conflict', 409);
      if (persistence.count(INSTALLATIONS) >= 1024) fail('installation_archive_required', 409);
      const receipt = {schema: 'deskbot.client-installation-receipt.v1',
        receipt_id: `installation-received-${digest({recordKey, hash}).slice(0, 32)}`,
        installation_id: row.installation_id, character_id: characterId, device_id: row.device_id,
        source_id: sourceId, source_revision: value.source_revision, installation_sha256: hash,
        received_at: now().toISOString(), status: 'recorded', scope: 'installation_facts'};
      const stored = {installation_id: row.installation_id, source_id: sourceId,
        source_revision: value.source_revision, installation_sha256: hash, installation: row,
        device_observation: {received_at: receipt.received_at, value: value.device, live: false}, receipt,
        adoption: {form: 'recorded', persona: nonblank(row.profile.persona) ? 'pending' : 'preserve_existing',
          voice: row.profile.voice.provider !== 'unconfigured' && nonblank(row.profile.voice.voice_id) ? 'pending' : 'preserve_existing', growth_stage: 'unchanged'}};
      persistence.put(INSTALLATIONS, recordKey, stored);
      persistence.put(CURRENT, key, stored);
      return {accepted: true, duplicate: false, receipt: clone(receipt), current_installation_id: row.installation_id};
    });
  }
  return {accept, getInstallation, state};
}

function send(response, status, value) {
  response.writeHead(status, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  response.end(JSON.stringify(value));
}

// Compose before listen(): keep the original handler for every existing endpoint.
export function attachInstallationRoutes(server, receiver) {
  const original = server.listeners('request');
  if (server.listening || original.length !== 1) throw new Error('installation_routes_attach_before_listen');
  server.removeListener('request', original[0]);
  server.on('request', (request, response) => {
    let url;
    try { url = new URL(request.url, 'http://127.0.0.1'); }
    catch { return send(response, 400, {error: 'invalid_request_url'}); }
    if (!['/api/hardware/installations', '/api/hardware/installation'].includes(url.pathname))
      return original[0].call(server, request, response);
    const origin = `http://127.0.0.1:${server.address().port}`;
    if (request.headers.host !== new URL(origin).host || (request.headers.origin && request.headers.origin !== origin))
      return send(response, 403, {error: 'installation_local_origin_required'});
    if (request.method === 'GET' && url.pathname === '/api/hardware/installation') {
      try { return send(response, 200, receiver.state(url.searchParams.get('device_id'), url.searchParams.get('character_id'),
        url.searchParams.get('installation_id'))); }
      catch (error) { return send(response, error.status ?? 503, {error: error.code ?? 'installation_store_unavailable'}); }
    }
    if (request.method !== 'POST' || url.pathname !== '/api/hardware/installations')
      return send(response, 405, {error: 'installation_method_required'});
    if (request.headers['x-deskbot-integration'] !== 'shell-installation-v1'
        || request.headers['content-type']?.split(';')[0].trim() !== 'application/json')
      return send(response, 415, {error: 'installation_json_header_required'});
    const length = Number(request.headers['content-length']);
    if (!Number.isSafeInteger(length) || length < 1 || length > MAX_BODY || request.headers['transfer-encoding'])
      return send(response, 413, {error: 'installation_body_limit'});
    const chunks = []; let size = 0, finished = false;
    const reject = (status, code) => { if (!finished) { finished = true; send(response, status, {error: code}); } };
    request.setTimeout(5000, () => reject(408, 'installation_body_timeout'));
    request.on('data', chunk => { size += chunk.length; if (size > MAX_BODY) reject(413, 'installation_body_limit'); else chunks.push(chunk); });
    request.on('error', () => reject(400, 'installation_body_incomplete'));
    request.on('end', () => {
      if (finished) return;
      try {
        if (size !== length) return reject(400, 'installation_body_incomplete');
        const result = receiver.accept(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        finished = true; send(response, 200, result);
      } catch (error) { reject(error.status ?? (error instanceof SyntaxError ? 422 : 503), error.code ?? 'installation_store_unavailable'); }
    });
  });
  return server;
}
