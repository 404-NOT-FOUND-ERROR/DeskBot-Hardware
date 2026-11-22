import {createHash} from 'node:crypto';
import {copyFileSync,existsSync,mkdirSync,readFileSync,readdirSync,statSync,writeFileSync} from 'node:fs';
import {createServer as createSocketServer} from 'node:net';
import {dirname,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import {parseArgs} from 'node:util';
import {createClientWithInstallation} from '../integrations/client-installation/server.mjs';
import {lockConversationStore} from '../integrations/client-conversation/store-owner.mjs';
import {applyVoicePilotContinuity,promptComposer,promptComposerSha256} from '../integrations/client-conversation/voice-pilot-continuity.mjs';

const ROOT = resolve(import.meta.dirname, '..');
const LOCK = JSON.parse(readFileSync(resolve(ROOT, 'integrations/client-conversation/source-lock.json'), 'utf8'));
const PAGE = readFileSync(resolve(ROOT, 'integrations/client-conversation/conversation.html'));
const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const fail = code => { throw Object.assign(new Error(code), {code}); };

export function verifyClientSnapshot(clientRoot, {voicePilot = false} = {}){
  const root=resolve(clientRoot);
  for(const [name,baselineDigest] of Object.entries(LOCK.files)){
    const expected = voicePilot && name === promptComposer ? promptComposerSha256 : baselineDigest;
    const path=resolve(root,name);
    if(!existsSync(path)||digest(path)!==expected)fail(`client_source_mismatch:${name}`);
  }
  const source=resolve(root,'apps/deskbot-service/src');
  const count=folder=>{
    let files=0;
    for(const entry of readdirSync(folder)){
      const path=resolve(folder,entry);
      if(statSync(path).isDirectory())files+=count(path);
      else if(path.endsWith('.mjs'))files+=1;
    }
    return files;
  };
  const expectedModules=Object.keys(LOCK.files).filter(name=>name.startsWith('apps/deskbot-service/src/')&&name.endsWith('.mjs')).length;
  if(count(source)!==expectedModules)fail('client_source_inventory_mismatch');
  return {baseline:LOCK.baseline,files:Object.keys(LOCK.files).length,modules:expectedModules,
    ...(voicePilot ? {variant:'voice-pilot-continuity'} : {})};
}

export function prepareVoicePilotSnapshot(clientRoot, destination) {
  verifyClientSnapshot(clientRoot);
  mkdirSync(destination);
  for (const name of Object.keys(LOCK.files)) {
    const source = resolve(clientRoot,name);
    const target = resolve(destination,name);
    mkdirSync(dirname(target),{recursive:true});
    if (name === promptComposer) writeFileSync(target,applyVoicePilotContinuity(readFileSync(source,'utf8')));
    else copyFileSync(source,target);
  }
  verifyClientSnapshot(destination,{voicePilot:true});
}

function sendJson(response, status, body) {
  response.writeHead(status, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  response.end(JSON.stringify(body));
}

function attachLocalRoutes({server, persistence, modelRuntime, runtimeProfile}, baseline, sourceId, canonicalCharacterId, voicePilot) {
  const [original] = server.listeners('request');
  server.removeListener('request', original);
  server.on('request', (request, response) => {
    let pathname;
    try { pathname = new URL(request.url, 'http://127.0.0.1').pathname; }
    catch { return sendJson(response, 400, {error: 'invalid_request_url'}); }
    const page = pathname === '/conversation' && request.method === 'GET';
    // This service uses the owner's save and a billable provider. Accept direct
    // localhost API clients and same-origin pages. Allow a link to open the UI.
    const host = `127.0.0.1:${server.address().port}`;
    if (request.headers.host !== host || (request.headers.origin && request.headers.origin !== `http://${host}`)
        || (!page && ['cross-site', 'same-site'].includes(request.headers['sec-fetch-site']))) {
      return sendJson(response, 403, {error: 'local_origin_required'});
    }
    if (page) {
      response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"});
      return response.end(PAGE);
    }
    if (voicePilot && pathname === '/api/voice/pilot/recover') {
      if (request.method !== 'POST') return sendJson(response,405,{error:'voice_recovery_requires_post'});
      void Promise.resolve().then(() => voicePilot.recover())
        .then(result => sendJson(response,200,result))
        .catch(error => sendJson(response,409,{error:error.code ?? 'voice_recovery_failed'}));
      return;
    }
    if (voicePilot && pathname === '/api/outbox' && request.method === 'GET') {
      // The pilot keeps legacy commands for correlation and closes its own
      // voice commands by ID; no worker can poll this copy for delivery.
      return sendJson(response, 200, {schema:'foundry.device-command-list.v0.1', commands:[]});
    }
    if (voicePilot && pathname.startsWith('/api/outbox/') && request.method === 'GET') {
      return sendJson(response, 404, {error:'device_delivery_disabled'});
    }
    if (pathname !== '/api/integration/status') return original.call(server, request, response);
    if (request.method !== 'GET') return sendJson(response, 405, {error: 'status_read_only'});
    const world = server.persistentWorld.get();
    const characterId = world.protagonist.character_id;
    const installations = persistence.list('hardware.current-installation')
      .filter(row => row.installation?.character_id === characterId).map(row => ({
        installation_id: row.installation_id, source_revision: row.source_revision,
        device_id: row.installation.device_id, shell_id: row.installation.shell_id,
        role_id: row.installation.profile.role_id, expression_role_id: row.installation.profile.expression_role_id,
        display_name: row.installation.profile.display_name, configuration: row.adoption,
      }));
    const latest = persistence.list('chat.turns')
      .filter(turn => canonicalCharacterId(turn.input_event?.character_id) === characterId).at(-1);
    const {database, ...profile} = runtimeProfile;
    sendJson(response, 200, {schema: 'deskbot.conversation-integration.v1', read_only: true,
      runtime: {...profile, clock_mode: world.clock?.mode,
        world_life_enabled: false, autonomous_life_enabled: false, resident_life_enabled: false, life_choice_enabled: false},
      baseline, source_id: sourceId, character_id: characterId, model: modelRuntime.llm.status(), installations,
      last_turn: latest ? {turn_id: latest.turn_id, installation_id: latest.installed_presentation?.installation_id ?? null,
        adoption: latest.presentation_adoption ?? null, provider: latest.provider,
        model: latest.reply_event?.payload?.metadata?.model ?? null} : null,
      connection: {chat: 'http_text', expression: voicePilot?.mode ?? 'external_usb_adapter',
        voice: voicePilot?.mode ?? 'not_connected', device_playback_verified_here: false},
      ...(voicePilot ? {voice_pilot:voicePilot.status()} : {}),
    });
  });
}

function listenLocal(server, port) {
  return new Promise((done, reject) => {
    const onError = error => { server.off('listening', onListening); reject(error); };
    const onListening = () => { server.off('error', onError); done(); };
    server.once('error', onError);
    try { server.listen(port, '127.0.0.1', onListening); }
    catch (error) { onError(error); }
  });
}

export async function startConversationService({clientRoot, database, sourceId, configPath, port = 4315,
  now = () => new Date(), voiceCatalog = {}, fetchImpl, voicePilot = null} = {}) {
  if (!clientRoot || !database || !sourceId || !configPath) fail('client_database_source_and_config_required');
  if (!Number.isInteger(port) || port < 0 || port > 65535) fail('invalid_port');
  const baseline = verifyClientSnapshot(clientRoot,{voicePilot:Boolean(voicePilot)});
  const modules = resolve(clientRoot, 'apps/deskbot-service/src');
  const load = name => import(pathToFileURL(resolve(modules, name + '.mjs')));
  const [{createReloadableLlm, loadLlmConfiguration}, {createModelUsage}, {canonicalCharacterId}, {InputError}] =
    await Promise.all([load('llm'), load('model-usage'), load('world-definition'), load('input-store')]);
  try { loadLlmConfiguration({env: {DESKBOT_LLM_PROVIDER: 'deepseek'}, configPath}); }
  catch { fail('llm_configuration_required'); }
  if (port !== 0) {
    const probe = createSocketServer();
    await listenLocal(probe, port);
    await new Promise(done => probe.close(done));
  }
  const owner = lockConversationStore(database);
  const pendingModels = new Set();
  let runtime;
  try {
    runtime = await createClientWithInstallation({clientRoot, database: owner.store, sourceId, voiceCatalog, now,
      createModelRuntime: ({persistence, runtimeProfile}) => {
        const modelUsage = createModelUsage({persistence, now, defaultScope: 'lab', runId: runtimeProfile.run_id});
        const original = createReloadableLlm({env: {}, configPath, now, fetchImpl, usageScope: 'lab', onUsage: modelUsage.record});
        const llm = {id: original.id, status: () => original.status(), complete(input) {
          const job = original.complete(input).catch(error => {
            // Both original routes recognize InputError. A known provider error
            // should not enter the recovery path for an uncertain submission.
            if (error.code?.startsWith('llm_')) {
              throw new InputError(424,error.code,'本轮未能生成回答。');
            }
            throw error;
          });
          pendingModels.add(job);
          return job.finally(() => pendingModels.delete(job));
        }};
        return {llm, modelUsage};
      }});
    // Keep previously scheduled life choices for the client that owns them.
    runtime.server.lifeChoiceWorker.stop();
    attachLocalRoutes(runtime, baseline, sourceId, canonicalCharacterId, voicePilot);
  } catch (error) {
    try { runtime?.persistence.close(); } finally { owner.release(); }
    throw error;
  }
  let closing = null, listening = null;
  const close = () => closing ??= (async () => {
    await new Promise(done => runtime.server.close(() => done()));
    // The caller can disconnect before the model finishes. Let the original
    // response and its SQLite checkpoint settle before closing the store.
    await Promise.allSettled([...pendingModels]);
    await new Promise(done => setImmediate(done));
    try { runtime.persistence.close(); } finally { owner.release(); }
  })();
  return {...runtime, baseline, close, async listen() {
    if (closing) fail('runtime_closed');
    listening ??= listenLocal(runtime.server, port).catch(async error => { await close(); throw error; });
    await listening;
    return `http://127.0.0.1:${runtime.server.address().port}`;
  }};
}

if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url){
  const {values}=parseArgs({options:{'client-root':{type:'string'},database:{type:'string'},'source-id':{type:'string'},
    'config-path':{type:'string'},port:{type:'string',default:'4315'}}});
  const service=await startConversationService({clientRoot:values['client-root'],database:values.database,
    sourceId:values['source-id'],configPath:values['config-path'],port:Number(values.port)});
  const url=await service.listen();
  process.stdout.write(JSON.stringify({status:'ready',url,scope:'lab',model:service.modelRuntime.llm.status(),baseline:service.baseline,
    source_id:values['source-id'],database:service.persistence.filename})+'\n');
  let closing=false;
  const input=createInterface({input:process.stdin});
  const close=()=>{
    if(closing)return;
    closing=true;
    input.close();
    process.stdin.unref?.();
    void service.close().then(() => process.exit(0), () => process.exit(1));
  };
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,close);
  input.on('line',line=>{if(line==='stop')close();});
}
