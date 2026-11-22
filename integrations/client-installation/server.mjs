// Isolated original-client host for compatibility checks; no device transport.
import {existsSync, mkdirSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import {parseArgs} from 'node:util';
import {randomUUID} from 'node:crypto';
import {attachInstallationRoutes, createInstallationReceiver} from './receiver.mjs';

export async function createClientWithInstallation({clientRoot, database, sourceId, voiceCatalog = {}, now = () => new Date(),
  createModelRuntime = null, timeMode = 'simulation', worldLifeEnabled = false, autonomousLifeEnabled = false,
  residentLifeEnabled = false}) {
  const load = name => import(pathToFileURL(resolve(clientRoot, 'apps/deskbot-service/src', name + '.mjs')));
  const [{createDeskBotServer}, {createSqlitePersistence}, {createDeviceRegistry},
    {bindRuntimeStore}, {DEFAULT_CHARACTER_ID}, {createInstalledPresentationResolver}] = await Promise.all([
    load('app'), load('persistence'), load('device-registry'), load('runtime-profile'), load('world-definition'),
    load('installed-presentation')]);
  mkdirSync(dirname(database), {recursive: true});
  const persistence = createSqlitePersistence({filename: database, now});
  try {
    bindRuntimeStore(persistence, {scope: 'lab'});
    const runtimeProfile = {schema: 'deskbot.runtime-profile.v1', scope: 'lab',
      run_id: `lab:${randomUUID()}`, database: persistence.filename,
      clock_scope: timeMode === 'realtime' ? 'real_time' : 'isolated_clock', started_at: now().toISOString()};
    const deviceRegistry = createDeviceRegistry({persistence, now});
    let receiver = null;
    const resolveInstalledPresentation = createInstalledPresentationResolver({
      getInstallation: request => receiver?.getInstallation(request) ?? null,
      voiceCatalog,
    });
    const modelRuntime = createModelRuntime
      ? await createModelRuntime({persistence, now, runtimeProfile})
      : {llm: {async complete() { return {text: '你好，收到你的消息。', provider: 'installation-check', model: 'fixed'}; }}};
    if (!modelRuntime || typeof modelRuntime.llm?.complete !== 'function') throw new Error('conversation_model_required');
    const server = createDeskBotServer({persistence, now, deviceRegistry, resolveInstalledPresentation,
      websocket: false, timeMode, worldLifeEnabled, autonomousLifeEnabled, residentLifeEnabled,
      llm: modelRuntime.llm, modelUsage: modelRuntime.modelUsage ?? null, runtimeProfile});
    runtimeProfile.clock_scope = server.persistentWorld.get().clock?.mode === 'real_time' ? 'real_time' : 'isolated_clock';
    receiver = createInstallationReceiver({persistence, sourceId, characterId: DEFAULT_CHARACTER_ID,
      getDevice: id => deviceRegistry.get(id), currentCharacterId: () => server.persistentWorld.get().protagonist.character_id, now});
    attachInstallationRoutes(server, receiver);
    return {server, persistence, receiver, deviceRegistry, modelRuntime, runtimeProfile};
  } catch (error) { persistence.close(); throw error; }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const {values} = parseArgs({options: {'client-root': {type: 'string'}, database: {type: 'string'},
    'source-id': {type: 'string'}, port: {type: 'string', default: '0'}, resume: {type: 'boolean', default: false}}});
  if (!values['client-root'] || !values.database || !values['source-id']) throw new Error('explicit_client_database_source_required');
  if (existsSync(values.database) && !values.resume) throw new Error('fresh_database_or_explicit_resume_required');
  const port = Number(values.port);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('invalid_port');
  const {server, persistence} = await createClientWithInstallation({clientRoot: values['client-root'], database: values.database,
    sourceId: values['source-id'], now: () => new Date('2026-11-17T09:00:00Z')});
  let closing = false;
  const close = () => { if (!closing) { closing = true; server.close(() => {persistence.close(); process.exit(0);}); server.closeAllConnections(); } };
  server.on('error', error => { console.error(error.message); close(); });
  const input = createInterface({input: process.stdin});
  input.on('line', line => {if (line === 'stop') close();}); input.on('close', close);
  server.listen(port, '127.0.0.1', () => process.stdout.write(JSON.stringify({
    url: `http://127.0.0.1:${server.address().port}`, scope: 'lab', source_id: values['source-id']}) + '\n'));
}
