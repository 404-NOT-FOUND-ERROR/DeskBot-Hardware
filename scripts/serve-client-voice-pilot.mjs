import {createHash, randomBytes} from 'node:crypto';
import {once} from 'node:events';
import {constants, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {ROOT} from '../integrations/client-4b2b/runtime.mjs';
import {WindowsVoice} from '../integrations/client-4b2b/windows-voice.mjs';
import {createVoiceGateway} from '../integrations/client-4b2b/websocket-gateway.mjs';
import {OriginalClientVoiceBridge} from '../integrations/client-4b2a/original-client-fixture.mjs';
import {createDeviceVoiceTrial, trustedTrialNetwork, validateTrialDevice} from '../integrations/client-conversation/voice-device-trial.mjs';
import {prepareVoicePilotSnapshot, startConversationService} from './serve-client-conversation-20261118.mjs';

const validPort = value => Number.isInteger(value) && value >= 0 && value <= 65535;
const sha256 = value => createHash('sha256').update(value).digest('hex');
const studioId = mac => 'device-' + sha256(Buffer.concat([
  Buffer.from('deskbot-link-v1\0'), Buffer.from(mac.replaceAll(':',''),'hex'),
])).slice(0,32);

export async function startVoicePilot({clientRoot, backup, backupSha256, configPath, sourceId, deviceId,
  port = 4316, voicePort = 8891, stateDirectory = null, stateParent = join(ROOT,'tmp','client-voice-pilot'),
  resume = false, voice = new WindowsVoice(), fetchImpl, clientFetchImpl = fetch, onOutcome = () => {},
  deviceTrial = null} = {}) {
  if (!clientRoot || !backup || !/^[a-f0-9]{64}$/i.test(backupSha256 ?? '') || !configPath || !sourceId)
    throw new Error('verified_backup_client_source_and_config_required');
  if (!validPort(port) || !validPort(voicePort) || (!deviceTrial && port !== 0 && port === voicePort))
    throw new Error('invalid_pilot_ports');
  const host = deviceTrial ? deviceTrial.host : '127.0.0.1';
  const mode = deviceTrial ? 'xiaozhi_device_trial' : 'xiaozhi_loopback';
  let trialIdentity, allowPeer;
  if (deviceTrial) {
    validateTrialDevice(deviceTrial);
    if (!stateDirectory || voicePort < 1024) throw new Error('device_trial_requires_fixed_port_and_state');
    allowPeer = trustedTrialNetwork(host);
    const selected = studioId(deviceTrial.device_mac);
    if (deviceId !== selected) throw new Error('voice_trial_device_binding_mismatch');
    trialIdentity = sha256(JSON.stringify([selected,deviceTrial.client_uuid,deviceTrial.firmware_elf_sha256,host,voicePort]));
  }
  const wal = backup+'-wal';
  if (existsSync(wal) && statSync(wal).size > 0) throw new Error('pilot_requires_standalone_backup');
  const sourceDigest = sha256(readFileSync(backup));
  if (sourceDigest !== backupSha256.toLowerCase()) throw new Error('pilot_backup_hash_mismatch');
  if (!stateDirectory) mkdirSync(stateParent,{recursive:true});
  const directory = stateDirectory ? resolve(stateDirectory) : mkdtempSync(join(resolve(stateParent),'run-'));
  mkdirSync(directory,{recursive:true});
  const database = join(directory,'conversation.sqlite');
  const pilotClient = join(directory,'client');
  const metadataPath = join(directory,'pilot.json');
  const alreadyExists = existsSync(database);
  if (alreadyExists && !resume) throw new Error('pilot_database_exists_enable_resume');
  if (!alreadyExists && resume) throw new Error('pilot_database_missing');
  if (alreadyExists) {
    const metadata = JSON.parse(readFileSync(metadataPath,'utf8'));
    if (metadata.schema !== 'deskbot.voice-pilot-state.v1'
        || metadata.source_id !== sourceId || metadata.backup_sha256 !== sourceDigest
        || metadata.trial_identity !== trialIdentity)
      throw new Error('pilot_database_source_mismatch');
  } else {
    prepareVoicePilotSnapshot(clientRoot,pilotClient);
    // Copy the standalone backup, then verify the copy before opening it.
    copyFileSync(backup,database,constants.COPYFILE_EXCL);
    if (sha256(readFileSync(database)) !== sourceDigest)
      throw new Error('pilot_backup_copy_mismatch');
  }
  const check = new DatabaseSync(database,{readOnly:true});
  try {
    if (check.prepare('PRAGMA quick_check').get().quick_check !== 'ok') throw new Error('pilot_database_integrity_failed');
  } finally { check.close(); }
  const credentials = {token:randomBytes(32).toString('hex'), device_header:'deskbot-pilot-head', client_header:'deskbot-pilot-owner'};
  let trial;
  if (deviceTrial) {
    const keysPath = join(directory,'device-trial-secret.json');
    // A service restart keeps the configuration URL and the ESP's cached key usable.
    const keys = alreadyExists ? JSON.parse(readFileSync(keysPath,'utf8')) : {
      token:credentials.token, setupKey:randomBytes(18).toString('base64url'),
    };
    trial = createDeviceVoiceTrial({config:{...deviceTrial,host},...keys,allowPeer});
    if (!alreadyExists) writeFileSync(keysPath,JSON.stringify(keys)+'\n',{mode:0o600,flag:'wx'});
    Object.assign(credentials,{token:keys.token,device_header:deviceTrial.device_mac,client_header:deviceTrial.client_uuid});
  }
  if (!alreadyExists) {
    writeFileSync(metadataPath,JSON.stringify({schema:'deskbot.voice-pilot-state.v1',source_id:sourceId,
      backup_sha256:sourceDigest, trial_identity:trialIdentity,created_at:new Date().toISOString()})+'\n',{flag:'wx'});
  }

  let conversation, gateway, bridge, lastOutcome, closing, setupUrl = null;
  const close = () => closing ??= (async () => {
    try { await gateway?.close(); } finally { await conversation?.close(); }
  })();
  try {
    conversation = await startConversationService({clientRoot:pilotClient,database,configPath,sourceId,port,fetchImpl,
      voicePilot: {
        mode,
        status() {
          const active = gateway?.snapshot();
          return {peer_ready: Boolean(active?.session && !['awaiting_hello','closed'].includes(active.session.state)),
            ...(trial ? {device_setup:trial.status(),session_state:active?.session?.state ?? null,
              setup_url:setupUrl} : {}),
            legacy_dispatch: 'disabled', recovery: bridge?.recoveryState().status ?? 'ready',
            last_outcome: lastOutcome ?? null};
        },
        recover: () => bridge?.recover() ?? {status:'starting'},
      },
    });
    const characterId = conversation.server.persistentWorld.get().protagonist.character_id;
    const devices = conversation.deviceRegistry.list()
      .filter(row => row.character_id === characterId && (!deviceId || row.device_id === deviceId));
    if (devices.length !== 1) throw new Error('one_registered_pilot_device_required');
    const binding = {character_id:characterId, device_id:devices[0].device_id, role_revision:'voice-pilot-v1'};
    const conversationUrl = await conversation.listen();
    bridge = new OriginalClientVoiceBridge(conversationUrl,{mode,fetchImpl:clientFetchImpl});
    gateway = createVoiceGateway({binding,client:bridge,voice,token:credentials.token,
      deviceHeader:credentials.device_header,clientHeader:credentials.client_header,deviceTrial:trial,
      sessionOptions:{stageTimeoutMs:30000, completionScope:mode,
        // The original reply keeps its expression plan. The wire supports the
        // pilot's two known expressions and can speak with a neutral face.
        selectExpression: expression => expression === 'happy' ? 'happy' : 'neutral',
        resolvePresentationAtStart: ({character_id,device_id}) => {
          const current = conversation.receiver.getInstallation({character_id,device_id});
          return current ? {installation_id:current.installation_id, shell_id:current.installation.shell_id,
            role_revision:String(current.installation.profile.profile_revision)} : null;
        }},
      onOutcome(result) {
        const {playback_completion = 'unobserved', ...outcome} = result;
        lastOutcome = {...outcome,completion_scope:mode,playback_completion,
          network_submission:result.packets > 0 ? 'submitted' : 'incomplete'};
        onOutcome(lastOutcome);
      },
    });
    await once(gateway.server.listen(voicePort,host),'listening');
    const voiceUrl = `ws://${host}:${gateway.server.address().port}/xiaozhi/v1`;
    setupUrl = trial ? `http://${host}:${gateway.server.address().port}${trial.path}` : null;
    return {conversation,conversationUrl,voiceUrl,setupUrl,gateway,bridge,credentials,binding,directory,close,mode};
  } catch (error) {
    await close();
    throw error;
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const {values} = parseArgs({options:{
    'client-root':{type:'string'}, backup:{type:'string'}, 'backup-sha256':{type:'string'},
    'config-path':{type:'string'}, 'source-id':{type:'string'}, 'device-id':{type:'string'},
    'device-trial-config':{type:'string'},
    port:{type:'string',default:'4316'}, 'voice-port':{type:'string',default:'8891'},
    'state-dir':{type:'string',default:'tmp/client-voice-pilot-20261118'}, resume:{type:'boolean',default:false},
  }});
  const pilot = await startVoicePilot({clientRoot:values['client-root'],backup:values.backup,backupSha256:values['backup-sha256'],
    configPath:values['config-path'],sourceId:values['source-id'],deviceId:values['device-id'],
    port:Number(values.port),voicePort:Number(values['voice-port']),
    stateDirectory:resolve(ROOT,values['state-dir']),resume:values.resume,
    deviceTrial:values['device-trial-config'] ? JSON.parse(readFileSync(values['device-trial-config'],'utf8')) : null,
    onOutcome: result => process.stdout.write(JSON.stringify({event:'voice_turn',...result})+'\n')});
  try {
    const connectionFile = join(pilot.directory,'connection.json');
    writeFileSync(connectionFile,JSON.stringify(pilot.setupUrl ? {setup_url:pilot.setupUrl} :
      {url:pilot.voiceUrl,...pilot.credentials})+'\n',{mode:0o600});
    process.stdout.write(JSON.stringify({status:'ready',scope:pilot.mode,conversation:pilot.conversationUrl+'/conversation',
      voice:pilot.voiceUrl,connection_file:connectionFile})+'\n');
  } catch (error) {
    await pilot.close();
    throw error;
  }
  let closing = false;
  const input = createInterface({input:process.stdin});
  const stop = () => {
    if (closing) return;
    closing = true;
    input.close();
    process.stdin.unref?.();
    void pilot.close().catch(() => {process.exitCode = 1;});
  };
  for (const signal of ['SIGINT','SIGTERM']) process.once(signal,stop);
  input.on('line',line => {if (line === 'stop') stop();});
}
