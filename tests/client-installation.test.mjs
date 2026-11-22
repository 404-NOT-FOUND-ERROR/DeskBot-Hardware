import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInstallationReceiver, digest} from '../integrations/client-installation/receiver.mjs';
import {createClientWithInstallation} from '../integrations/client-installation/server.mjs';

if (!process.env.DESKBOT_HANDOFF_CLIENT) throw new Error('explicit_client_checkout_required');
const root = process.env.DESKBOT_HANDOFF_CLIENT;
const {createSqlitePersistence} = await import(pathToFileURL(resolve(root, 'apps/deskbot-service/src/persistence.mjs')));
const DEVICE = 'device-' + 'a'.repeat(32), CHARACTER = 'shaping-001', GRAPH = 'c'.repeat(64);
const SOURCE = 'installation-test';
const now = () => new Date('2026-11-17T09:00:00Z');
const body = value => ({method: 'POST', headers: {'Content-Type': 'application/json',
  'X-DeskBot-Integration': 'shell-installation-v1'}, body: JSON.stringify(value)});

function envelope(id = 'install-a', role = 'round', revision = 1) {
  const profile = {schema: 'deskbot.role_profile.v2', role_id: 'form-' + role, display_name: '形态' + role,
    persona: '', voice: {provider: 'unconfigured', voice_id: '', speaking_style: ''},
    expression_role_id: role, expression_graph_sha256: GRAPH, preview_catalog_id: 'd'.repeat(64), profile_revision: 1};
  const binding = {role_id: profile.role_id, profile_revision: 1, profile_sha256: digest(profile), expression_role_id: role,
    expression_graph_sha256: GRAPH, preview_catalog_id: profile.preview_catalog_id, source: null, confirmation: 'owner_choice_local'};
  const command = {operation: `role.activate.${role === 'round' ? 0 : 1}.${GRAPH}`, command_id: 'command-' + id,
    target_id: DEVICE, connection_id: 'connection-1', boot_id: 'boot-1', session_id: 'session-1'};
  const device = {device_id: DEVICE, connection_id: command.connection_id, boot_id: command.boot_id,
    session_id: command.session_id, sequence: 10, graph_sha256: GRAPH, actual: role, current: role,
    target: role, saved: role, save_state: 'saved', activation: {request_id: 'request-' + id, status: 'applied'}};
  const row = {installation_id: id, character_id: CHARACTER, device_id: DEVICE, shell_id: 'shell-' + role,
    shell_name: '外壳' + role, role_name: profile.display_name,
    binding_sha256: digest(binding), plan_id: null, binding, profile, confirmed_at: now().toISOString(),
    source: 'owner_manual', scope: 'device_expression', phase: 'saved', first_frame: true, saved: true,
    completion_source: 'correlated_device_reply', command, device_result: structuredClone(device),
    request_result: {command_id: command.command_id, operation: command.operation, request_id: device.activation.request_id,
      index: role === 'round' ? 0 : 1, status: 'saved', first_frame: true,
      target: {binding_sha256: digest(binding), shell_id: 'shell-' + role, shell_name: '外壳' + role,
        role_name: profile.display_name, role_id: profile.role_id, profile_revision: profile.profile_revision,
        expression_role_id: role, expression_graph_sha256: GRAPH, ready: true, source: null}}};
  return {schema: 'deskbot.owner-installation-handoff.v1', source_id: SOURCE, source_revision: revision,
    installation: row, device};
}

function rehash(value) {
  const row = value.installation;
  row.binding.profile_sha256 = digest(row.profile); row.binding_sha256 = digest(row.binding);
  row.request_result.target.binding_sha256 = row.binding_sha256;
  row.role_name = row.request_result.target.role_name = row.profile.display_name;
  return value;
}

function magneticSource() {
  return {key: 'sample-a', session_id: 'capture-a', capture_shell_id: 'shell-a',
    device_id: DEVICE, magnet_profile_id: 'magnet-a', archive_sha256: 'b'.repeat(64)};
}

function migratedProfile() {
  return {schema: 'deskbot.role_profile.v1', shell_id: 'old-shell', magnet_profile_id: 'old-magnet',
    role_id: 'old-form', display_name: '旧形态', voice_id: 'voice-a', catchphrases: ['你好'],
    expression_map: {happy: 'smile'}, action_map: {greeting: 'cat_nuzzle'}, asset_version: 'assets-1',
    profile_revision: 1, binding_state: 'confirmed', binding_version: 'deskbot.expression_bindings/v1', notes: ''};
}

function fixture(t) {
  const folder = mkdtempSync(join(tmpdir(), 'installation-unit-'));
  let persistence = createSqlitePersistence({filename: join(folder, 'client.sqlite'), now});
  t.after(() => {persistence.close(); rmSync(folder, {recursive: true, force: true});});
  const make = overrides => createInstallationReceiver({persistence, sourceId: SOURCE, characterId: CHARACTER,
    currentCharacterId: () => CHARACTER, getDevice: () => ({device_id: DEVICE, character_id: CHARACTER}), now, ...overrides});
  return {get persistence() {return persistence;}, make, receiver: make(), reopen() {
    persistence.close(); persistence = createSqlitePersistence({filename: join(folder, 'client.sqlite'), now}); return make();
  }};
}

test('A/B/A install sequence preserves life, identity and growth records', t => {
  const f = fixture(t), life = {character_id: CHARACTER, memory: ['共同经历'], relationship: {owner: 4}, role_stages: {form: 'existing-stage'}};
  f.persistence.put('canonical-world.states', 'world', life);
  for (const [id, role, revision] of [['a1', 'round', 1], ['b1', 'original', 2], ['a2', 'round', 3]]) {
    const result = f.receiver.accept(envelope(id, role, revision));
    assert.equal(result.current_installation_id, id); assert.equal(result.duplicate, false);
  }
  assert.equal(f.persistence.count('hardware.installations'), 3);
  assert.deepEqual(f.persistence.get('canonical-world.states', 'world'), life);
  assert.equal(f.receiver.state(DEVICE).current.installation.profile.expression_role_id, 'round');
});

test('fresh device query does not change an installation fingerprint or receipt', t => {
  const f = fixture(t), value = envelope(), first = f.receiver.accept(value);
  value.device.sequence++; value.device.boot_id = 'later-boot'; value.device.session_id = 'later-session';
  value.source_revision = 8;
  const again = f.receiver.accept(value);
  assert.equal(again.duplicate, true); assert.deepEqual(again.receipt, first.receipt);
  assert.equal(f.persistence.count('hardware.installations'), 1);
});

test('late old duplicate carrying higher query revision cannot replace current', t => {
  const f = fixture(t), old = envelope(); f.receiver.accept(old);
  f.receiver.accept(envelope('b', 'original', 2)); old.source_revision = 99;
  const result = f.receiver.accept(old);
  assert.equal(result.current_installation_id, 'b'); assert.equal(result.duplicate, true);
  assert.equal(f.receiver.state(DEVICE).current.source_revision, 2);
});

test('same and lower revisions for a different installation conflict', t => {
  const f = fixture(t); f.receiver.accept(envelope('a', 'round', 4));
  for (const revision of [1, 4]) assert.throws(() => f.receiver.accept(envelope('b', 'original', revision)), {code: 'installation_revision_conflict'});
  assert.equal(f.persistence.count('hardware.installations'), 1);
});

test('same installation with changed frozen content conflicts', t => {
  const f = fixture(t), value = envelope(); f.receiver.accept(value);
  value.installation.plan_id = 'new-plan';
  assert.throws(() => f.receiver.accept(value), {code: 'installation_content_conflict'});
});

test('wrong source, device owner and current individual are rejected', t => {
  const f = fixture(t), value = envelope();
  assert.throws(() => f.make({sourceId: 'another'}).accept(value), {code: 'installation_source_conflict'});
  assert.throws(() => f.make({getDevice: () => null}).accept(value), {code: 'installation_identity_conflict'});
  assert.throws(() => f.make({currentCharacterId: () => 'another'}).accept(value), {code: 'installation_identity_conflict'});
  assert.throws(() => f.make({getDevice: () => ({character_id: 'another'})}).accept(value), {code: 'installation_identity_conflict'});
  f.receiver.accept(value);
  const later = envelope('b', 'original', 2); later.source_id = 'another';
  assert.throws(() => f.make({sourceId: 'another'}).accept(later), {code: 'installation_source_conflict'});
});

test('receipt, profile, device and first-frame errors leave storage empty', t => {
  const f = fixture(t);
  const mutations = [v => v.installation.first_frame = false, v => v.installation.saved = false,
    v => v.installation.profile.persona = 'changed', v => v.installation.request_result.request_id = 'other',
    v => v.installation.command.command_id = 'other', v => v.device.actual = 'other',
    v => v.device.graph_sha256 = 'f'.repeat(64), v => v.device.save_state = 'pending',
    v => v.installation.device_result.session_id = 'other', v => v.source_revision = true,
    v => v.installation.profile.role_id = null, v => v.device.activation.status = 'unrecognized'];
  for (const mutate of mutations) {const value = envelope(); mutate(value); assert.throws(() => f.receiver.accept(value));}
  assert.equal(f.persistence.count('hardware.installations'), 0);
});

test('complete receipt target and role index must match the frozen installation', t => {
  const f = fixture(t);
  const changes = [r => r.index = 2, r => r.index = true, r => r.target.shell_name = '其他外壳',
    r => r.target.role_name = '其他角色', r => r.target.role_id = 'another-form',
    r => r.target.profile_revision++, r => r.target.expression_graph_sha256 = 'e'.repeat(64),
    r => r.target.ready = false, r => r.target.source = magneticSource(), r => delete r.target.role_id];
  for (const change of changes) {
    const value = envelope(); change(value.installation.request_result);
    assert.throws(() => f.receiver.accept(value), {code: 'receipt_mismatch'});
  }
  assert.equal(f.persistence.count('hardware.installations'), 0);
});

test('invalid binding confirmation and magnetic source remain invalid after rehashing', t => {
  const f = fixture(t), mutations = [v => v.installation.binding.confirmation = 'automatic',
    v => v.installation.binding.source = {key: 'incomplete'},
    v => {v.installation.binding.source = magneticSource(); v.installation.binding.source.archive_sha256 = 'wrong';},
    v => {v.installation.binding.source = magneticSource(); v.installation.binding.source.key = ' padded ';}];
  for (const mutate of mutations) {
    const value = envelope(); mutate(value); rehash(value);
    assert.throws(() => f.receiver.accept(value));
  }
  assert.equal(f.persistence.count('hardware.installations'), 0);
});

test('manual installation preserves a registered magnetic source separately from its target', t => {
  const f = fixture(t), value = envelope(); value.installation.binding.source = magneticSource(); rehash(value);
  f.receiver.accept(value);
  const stored = f.receiver.state(DEVICE).current.installation;
  assert.deepEqual(stored.binding.source, magneticSource());
  assert.equal(stored.request_result.target.source, null);
});

test('valid migrated v1 profile and its digest survive synchronization', t => {
  const f = fixture(t), value = envelope(), profile = migratedProfile();
  value.installation.profile.legacy = {profile, source_sha256: digest(profile)}; rehash(value);
  f.receiver.accept(value);
  assert.deepEqual(f.receiver.state(DEVICE).current.installation.profile.legacy.profile, profile);
});

test('malformed legacy evidence cannot be accepted by recomputing outer hashes', t => {
  const f = fixture(t), mutations = [p => delete p.voice_id, p => p.binding_state = 'applied',
    p => p.magnet_profile_id = p.shell_id, p => p.action_map.greeting = 'unsafe-action',
    p => p.binding_version = 'invalid', p => p.binding_version += '\n',
    p => p.notes = 'x'.repeat(1001), p => p.profile_revision = true,
    p => p.unexpected = true, p => p.role_id += '\n', p => p.catchphrases.push(7)];
  for (const mutate of mutations) {
    const value = envelope(), profile = migratedProfile(); mutate(profile);
    value.installation.profile.legacy = {profile, source_sha256: digest(profile)}; rehash(value);
    assert.throws(() => f.receiver.accept(value), {code: 'invalid_legacy_profile'});
  }
  const value = envelope(), profile = migratedProfile();
  value.installation.profile.legacy = {profile, source_sha256: 'f'.repeat(64)}; rehash(value);
  assert.throws(() => f.receiver.accept(value), {code: 'legacy_hash_mismatch'});
  assert.equal(f.persistence.count('hardware.installations'), 0);
});

test('Unicode limits count code points for names, persona and legacy text', t => {
  const f = fixture(t), value = envelope(), profile = migratedProfile();
  value.installation.profile.display_name = profile.display_name = '🐸'.repeat(80);
  value.installation.profile.persona = '🍲'.repeat(2000);
  value.installation.profile.legacy = {profile, source_sha256: digest(profile)}; rehash(value);
  f.receiver.accept(value);
  assert.equal(f.receiver.state(DEVICE).current.adoption.persona, 'pending');
  const invalid = envelope('too-long', 'round', 2); invalid.installation.profile.persona = '🍲'.repeat(2001); rehash(invalid);
  assert.throws(() => f.receiver.accept(invalid), {code: 'invalid_profile'});
});

test('whitespace persona and configured provider without a voice id preserve existing settings', t => {
  const f = fixture(t), value = envelope();
  value.installation.profile.persona = ' \t\n\r\u0085\u3000';
  value.installation.profile.voice = {provider: 'gateway', voice_id: ' \u3000', speaking_style: ''}; rehash(value);
  f.receiver.accept(value);
  const adoption = f.receiver.state(DEVICE).current.adoption;
  assert.equal(adoption.persona, 'preserve_existing'); assert.equal(adoption.voice, 'preserve_existing');
});

test('reconciled device snapshot is retained as its own completion basis', t => {
  const f = fixture(t), value = envelope();
  value.installation.completion_source = 'device_snapshot'; delete value.installation.request_result;
  value.installation.device_result.boot_id = 'restored-boot'; value.installation.device_result.save_state = 'restored';
  f.receiver.accept(value);
  assert.equal(f.receiver.state(DEVICE).current.installation.completion_source, 'device_snapshot');
  assert.equal(f.receiver.state(DEVICE).current.installation.request_result, undefined);
});

test('second write failure rolls back installation and current together', t => {
  const f = fixture(t), originalPut = f.persistence.put;
  f.persistence.put = (namespace, ...args) => {if (namespace === 'hardware.current-installation') throw new Error('write-failed'); return originalPut(namespace, ...args);};
  assert.throws(() => f.receiver.accept(envelope()), /write-failed/);
  assert.equal(f.persistence.count('hardware.installations'), 0);
  assert.equal(f.receiver.state(DEVICE).current, null);
});

test('reopen restores original receipt and current form', t => {
  const f = fixture(t), value = envelope(), first = f.receiver.accept(value), reopened = f.reopen();
  assert.deepEqual(reopened.accept(value).receipt, first.receipt);
  assert.equal(reopened.state(DEVICE).current.installation_id, value.installation.installation_id);
});

test('failed replacement preserves the previous saved installation after reopen', t => {
  const f = fixture(t); f.receiver.accept(envelope());
  const originalPut = f.persistence.put;
  f.persistence.put = (namespace, ...args) => {if (namespace === 'hardware.current-installation') throw new Error('write-failed'); return originalPut(namespace, ...args);};
  assert.throws(() => f.receiver.accept(envelope('b', 'original', 2)), /write-failed/);
  assert.equal(f.persistence.count('hardware.installations'), 1);
  const reopened = f.reopen();
  assert.equal(reopened.state(DEVICE).current.installation_id, 'install-a');
});

test('empty personality and unconfigured voice explicitly preserve client settings', t => {
  const f = fixture(t); f.receiver.accept(envelope());
  const state = f.receiver.state(DEVICE);
  assert.deepEqual(state.current.adoption, {form: 'recorded', persona: 'preserve_existing', voice: 'preserve_existing', growth_stage: 'unchanged'});
  assert.equal(state.live_device_state, false); assert.equal(state.current.device_observation.live, false);
});

test('configured personality and voice are recorded for subsequent runtime adoption', t => {
  const f = fixture(t), value = envelope(), row = value.installation;
  row.profile.persona = '有自己的偏好'; row.profile.voice = {provider: 'gateway', voice_id: 'voice-a', speaking_style: '轻快'};
  row.binding.profile_sha256 = digest(row.profile); row.binding_sha256 = digest(row.binding);
  row.request_result.target.binding_sha256 = row.binding_sha256;
  f.receiver.accept(value);
  assert.equal(f.receiver.state(DEVICE).current.adoption.voice, 'pending');
  assert.equal(f.receiver.state(DEVICE).current.adoption.persona, 'pending');
});

test('implicit unique installation rechecks device ownership after unbinding', t => {
  const f = fixture(t);
  let registered = {device_id: DEVICE, character_id: CHARACTER};
  const receiver = f.make({getDevice: () => registered});
  receiver.accept(envelope());
  assert.equal(receiver.getInstallation().installation_id, 'install-a');
  registered = null;
  assert.throws(() => receiver.getInstallation(), {code: 'installation_identity_conflict'});
});

test('multiple device installations require an explicit device selection',t=>{
  const f=fixture(t),secondDevice='device-'+'b'.repeat(32);
  const receiver=f.make({getDevice:id=>({device_id:id,character_id:CHARACTER})});
  receiver.accept(envelope());
  const second=envelope('other-device','original',1);
  second.installation.device_id=secondDevice;
  second.installation.command.target_id=secondDevice;
  second.installation.device_result.device_id=secondDevice;
  second.device.device_id=secondDevice;
  receiver.accept(second);
  assert.throws(()=>receiver.getInstallation(),{code:'installation_device_required'});
  assert.equal(receiver.getInstallation({device_id:DEVICE}).installation_id,'install-a');
  assert.equal(receiver.getInstallation({device_id:secondDevice}).installation_id,'other-device');
});

test('original HTTP and canonical world continue through composed installation routes', async t => {
  const folder = mkdtempSync(join(tmpdir(), 'installation-http-'));
  const host = await createClientWithInstallation({clientRoot: root, database: join(folder, 'client.sqlite'), sourceId: SOURCE, now});
  t.after(async () => {await new Promise(resolve => {host.server.close(resolve); host.server.closeAllConnections();}); host.persistence.close(); rmSync(folder, {recursive: true, force: true});});
  await new Promise(resolve => host.server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${host.server.address().port}`;
  const hello = await fetch(url + '/api/devices/hello', body({device_id: DEVICE, character_id: CHARACTER, hardware: 'esp32-s3', firmware: 'test', capabilities: {'display.expression': true}}));
  assert.equal(hello.status, 201);
  const world = await (await fetch(url + '/api/world/state')).json();
  const result = await fetch(url + '/api/hardware/installations', body(envelope()));
  assert.equal(result.status, 200, await result.clone().text());
  const state = await (await fetch(url + `/api/hardware/installation?character_id=${CHARACTER}&device_id=${DEVICE}`)).json();
  assert.equal(state.current.installation_id, 'install-a');
  assert.deepEqual(await (await fetch(url + '/api/world/state')).json(), world);
  assert.equal((await fetch(url + '/api/hardware/installations', {...body(envelope()), headers: {'Content-Type': 'application/json'}})).status, 415);
  assert.equal((await fetch(url + '/api/hardware/installations', {...body(envelope()), headers: {...body(envelope()).headers, Origin: 'https://another.invalid'}})).status, 403);
  assert.equal((await fetch(url + '/api/hardware/installations', {...body(envelope()), body: '{'})).status, 422);
  assert.equal((await fetch(url + '/api/hardware/installations', {...body(envelope()), body: 'x'.repeat(128 * 1024 + 1)})).status, 413);
  assert.equal((await fetch(url + '/api/hardware/installation?character_id=someone-else&device_id=' + DEVICE)).status, 409);
  const response = await fetch(url + '/api/chat', body({event_id: 'installation-chat', correlation_id: 'installation-chat', message: '你好', character_id: CHARACTER}));
  assert.equal(response.status, 202, (await response.clone().text()).slice(0, 240));
  assert.equal((await response.json()).accepted, true);
});

test('chat adopts the current role and a frozen voice installation can resolve an archived shell', async t => {
  const folder = mkdtempSync(join(tmpdir(), 'installation-adoption-'));
  const host = await createClientWithInstallation({clientRoot: root, database: join(folder, 'client.sqlite'), sourceId: SOURCE, now});
  t.after(async () => {await new Promise(resolve => {host.server.close(resolve); host.server.closeAllConnections();}); host.persistence.close(); rmSync(folder, {recursive: true, force: true});});
  await new Promise(resolve => host.server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${host.server.address().port}`;
  const hello = await fetch(url + '/api/devices/hello', body({device_id: DEVICE, character_id: CHARACTER, hardware: 'esp32-s3', firmware: 'test', capabilities: {'display.expression': true}}));
  assert.equal(hello.status, 201);

  const first = envelope('install-a', 'round', 1);
  first.installation.profile.persona = '喜欢留意水边的小变化。';
  rehash(first);
  const second = envelope('install-b', 'original', 2);
  second.installation.profile.persona = '对拆解和修理有兴趣。';
  rehash(second);
  assert.equal((await fetch(url + '/api/hardware/installations', body(first))).status, 200);
  assert.equal((await fetch(url + '/api/hardware/installations', body(second))).status, 200);

  const currentResponse = await fetch(url + '/api/chat', body({event_id: 'adoption-current', correlation_id: 'adoption-current',
    character_id: CHARACTER, device_id: DEVICE, message: '你现在喜欢什么？'}));
  const current = (await currentResponse.json()).turn;
  assert.equal(current.installed_presentation.installation_id, 'install-b');
  assert.match(current.prompt.text, /对拆解和修理有兴趣/);
  assert.equal(current.installed_presentation.character_id, CHARACTER);

  const frozenResponse = await fetch(url + '/api/chat', body({event_id: 'adoption-frozen', correlation_id: 'adoption-frozen',
    character_id: CHARACTER, device_id: DEVICE, presentation_installation_id: 'install-a', message: '你还记得吗？'}));
  const frozen = (await frozenResponse.json()).turn;
  assert.equal(frozen.installed_presentation.installation_id, 'install-a');
  assert.match(frozen.prompt.text, /喜欢留意水边的小变化/);
  assert.doesNotMatch(frozen.prompt.text, /对拆解和修理有兴趣/);
  assert.equal(frozen.input_event.character_id, current.input_event.character_id);

  const noInstallResponse = await fetch(url + '/api/voice/asr', body({event_id: 'asr-none-at-start',
    correlation_id: 'no-install-at-start', utterance_id: 'no-install-at-start', stage: 'final',
    raw_utterance: '你好', clean_utterance: '你好', provider: 'fixed',
    character_id: CHARACTER, device_id: DEVICE, presentation_installation_id: null}));
  const noInstall = (await noInstallResponse.json()).turn;
  assert.equal(noInstallResponse.status, 202);
  assert.equal(noInstall.installed_presentation, null);
  assert.doesNotMatch(noInstall.prompt.text, /对拆解和修理有兴趣|喜欢留意水边的小变化/);

  const missingResponse = await fetch(url + '/api/chat', body({event_id: 'adoption-missing',
    character_id: CHARACTER, device_id: DEVICE, presentation_installation_id: 'not-received', message: '你好'}));
  assert.equal(missingResponse.status, 409);

  const aliasResponse = await fetch(url + '/api/chat', body({event_id: 'adoption-legacy-id',
    character_id: 'ember-001', device_id: DEVICE, message: '你好'}));
  const aliasTurn = (await aliasResponse.json()).turn;
  assert.equal(aliasResponse.status, 202);
  assert.equal(aliasTurn.installed_presentation.character_id, CHARACTER);
  assert.equal(aliasTurn.input_event.character_id, 'ember-001');
});
