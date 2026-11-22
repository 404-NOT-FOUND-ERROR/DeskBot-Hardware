import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {ROOT, WebSocket} from '../integrations/client-4b2b/runtime.mjs';
import {decodePackets, encodePcm, pcmFromWav, rms, wavFromPcm} from '../integrations/client-4b2b/audio.mjs';
import {WindowsVoice} from '../integrations/client-4b2b/windows-voice.mjs';
import {decodeAudio, DOWNLINK, UPLINK} from '../integrations/client-4b2a/xiaozhi-codec.mjs';
import {createClientWithInstallation} from '../integrations/client-installation/server.mjs';
import {digest} from '../integrations/client-installation/receiver.mjs';
import {startVoicePilot} from '../scripts/serve-client-voice-pilot.mjs';
import {verifyClientSnapshot} from '../scripts/serve-client-conversation-20261118.mjs';

const snapshot = process.env.DESKBOT_CONVERSATION_CLIENT ?? resolve(ROOT,'tmp/client-conversation-20261118/client');
const current = JSON.parse(readFileSync(new URL(
  '../artifacts/client-installation-sync-20261117-r3/accepted-archive-replay.json',import.meta.url),'utf8')).state.current;
const sourceId = 'voice-pilot-test';
const {character_id: actor, device_id: device} = current.installation;
const sha256 = file => createHash('sha256').update(readFileSync(file)).digest('hex');
const post = body => ({method:'POST',headers:{'Content-Type':'application/json',
  'X-DeskBot-Integration':'shell-installation-v1'},body:JSON.stringify(body)});

async function request(url,path,body) {
  const response = await fetch(url+path,body === undefined ? undefined : post(body));
  const value = await response.json();
  assert.ok(response.ok,`${path}: status ${response.status} (${value.error ?? 'no error code'})`);
  return value;
}
async function waitFor(read, label, timeout = 5000) {
  const deadline = performance.now()+timeout;
  do {
    const value = read();
    if (value) return value;
    await delay(15);
  } while (performance.now() < deadline);
  assert.fail(label);
}
async function connect(t, pilot) {
  const socket = new WebSocket(pilot.voiceUrl,{headers:{authorization:`Bearer ${pilot.credentials.token}`,
    'device-id':pilot.credentials.device_header,'client-id':pilot.credentials.client_header,'protocol-version':'1'}});
  t.after(() => socket.terminate());
  const messages = [], packets = [];
  let firstAudioAt, closed;
  socket.on('close',(code,reason) => {closed = `${code}: ${reason}`;});
  socket.on('message',(bytes,binary) => {
    if (binary) {
      firstAudioAt ??= performance.now();
      packets.push(decodeAudio(bytes,1).payload);
    } else messages.push(JSON.parse(bytes.toString('utf8')));
  });
  await once(socket,'open');
  const send = value => socket.send(JSON.stringify(value));
  send({type:'hello',transport:'websocket',version:1,audio_params:UPLINK});
  const hello = await waitFor(() => messages.find(message => message.type === 'hello'),'missing voice hello');
  const control = (type,extra) => send({type,session_id:hello.session_id,...extra});
  return {socket,messages,packets,sessionId:hello.session_id,control,
    get closed(){return closed;},get firstAudioAt(){return firstAudioAt;}};
}
function outcomeFor(peer,outcomes,status,offset = 0) {
  return waitFor(() => {
    const value = outcomes.slice(offset).find(outcome => outcome.session_id === peer.sessionId);
    if (value) assert.equal(value.status,status);
    else if (peer.closed) assert.fail(`voice closed before outcome: ${peer.closed}`);
    return value;
  },'voice turn did not settle',80000);
}
function installation(label, revision) {
  const row = structuredClone(current.installation);
  row.installation_id = `voice-pilot-installation-${label}`;
  row.shell_id = `voice-pilot-shell-${label}`;
  row.role_name = `外壳 ${label}`;
  row.profile = {...row.profile, role_id:`voice-pilot-role-${label}`, display_name:row.role_name, profile_revision:revision};
  row.binding = {...row.binding, role_id:row.profile.role_id, profile_revision:revision, profile_sha256:digest(row.profile)};
  row.binding_sha256 = digest(row.binding);
  Object.assign(row.request_result.target,{shell_id:row.shell_id,role_id:row.profile.role_id,
    role_name:row.role_name,profile_revision:revision,binding_sha256:row.binding_sha256});
  return {schema:'deskbot.owner-installation-handoff.v1',source_id:sourceId,source_revision:revision,
    installation:row,device:current.device_observation.value};
}
function speakOnWire(peer, packets) {
  for (const packet of packets) peer.socket.send(packet);
}
function textTurn(pilot,event_id,message) {
  return request(pilot.conversationUrl,'/api/chat',{event_id,message,character_id:pilot.binding.character_id,
    device_id:pilot.binding.device_id});
}
function temporaryDirectory(t) {
  const root = resolve(ROOT,'tmp');
  mkdirSync(root,{recursive:true});
  const directory = mkdtempSync(join(root,'voice-pilot-check-'));
  t.after(() => {
    // This exact directory was allocated here for the current test.
    assert.ok(resolve(directory).startsWith(join(root,'voice-pilot-check-')));
    rmSync(directory,{recursive:true,force:true});
  });
  return directory;
}

test('voice owns its wire output and shares chat across capture snapshots, abort and reconnect',{timeout:20000},async t => {
  const directory = temporaryDirectory(t);
  const backup = join(directory,'seed.sqlite'), configPath = join(directory,'test-model.json');
  const seed = await createClientWithInstallation({clientRoot:snapshot,database:backup,sourceId});
  try {
    seed.deviceRegistry.register({character_id:actor,device_id:device,hardware:'esp32-s3',firmware:'test'});
  } finally { seed.persistence.close(); }
  const backupSha256 = sha256(backup);
  writeFileSync(configPath,JSON.stringify({base_url:'https://api.deepseek.com',model:'deepseek-flash',api_key:'test-only'}));
  const outcomes = [], prompts = [], syntheses = [];
  const uplink = encodePcm(Buffer.alloc(16000*2*0.06),16000);
  const clip = encodePcm(Buffer.alloc(24000*2*0.60),24000);
  const args = {clientRoot:snapshot,backup,backupSha256,configPath,sourceId,port:0,voicePort:0,stateParent:directory,
    fetchImpl:async (_url,options) => {
      prompts.push(JSON.parse(options.body).messages[0].content);
      return Response.json({choices:[{message:{content:'我记得，是青石铃。'},finish_reason:'stop'}]});
    },
    voice:{
      recognize:async ({turn}) => {
        const text = turn.presentation_installation_id === 'voice-pilot-installation-b'
          ? '我今天有点难过' : '我的测试物叫什么名字';
        return {raw_utterance:text,clean_utterance:text,provider:'test'};
      },
      synthesize:async input => { syntheses.push(input); return {audio:DOWNLINK,packets:clip}; },
    },
    onOutcome:value => outcomes.push(value),
  };
  await assert.rejects(startVoicePilot({...args,backupSha256:'0'.repeat(64)}),/pilot_backup_hash_mismatch/);
  const pilot = await startVoicePilot(args);
  try {
    const url = pilot.conversationUrl;
    assert.equal((await request(url,'/api/integration/status')).voice_pilot.peer_ready,false);
    await textTurn(pilot,'voice-pilot-seed-text','请记住我的测试物叫青石铃。');
    const first = await connect(t,pilot);
    assert.equal((await request(url,'/api/integration/status')).voice_pilot.peer_ready,true);
    assert.equal(pilot.gateway.server.address().address,'127.0.0.1');
    assert.equal(pilot.conversation.server.address().address,'127.0.0.1');
    const a = installation('a',1), b = installation('b',2);
    const start = async peer => {
      peer.control('listen',{state:'start',mode:'manual'});
      await waitFor(() => pilot.gateway.snapshot().session?.state === 'listening','capture did not start');
      speakOnWire(peer,uplink);
    };
    const stop = peer => peer.control('listen',{state:'stop'});

    await start(first);
    await request(url,'/api/hardware/installations',a);
    stop(first);
    const initial = await outcomeFor(first,outcomes,'sent');
    assert.equal(syntheses[0].turn.presentation_installation_id,null);
    assert.equal(syntheses[0].installed_presentation,null);
    assert.equal(syntheses[0].turn.shell_id,null);
    assert.match(prompts[1],/测试物叫青石铃/);
    assert.equal(initial.network_submission,'submitted');
    assert.equal(initial.playback_completion,'unobserved');

    await start(first);
    await request(url,'/api/hardware/installations',b);
    const packetOffset = first.packets.length, outcomeOffset = outcomes.length;
    const interrupt = (_bytes,binary) => {
      if (!binary) return;
      first.socket.off('message',interrupt);
      first.control('abort');
    };
    first.socket.on('message',interrupt);
    stop(first);
    const aborted = await outcomeFor(first,outcomes,'turn_cancelled',outcomeOffset);
    assert.ok(first.packets.length > packetOffset && first.packets.length < packetOffset+clip.length);
    assert.equal(first.messages.at(-1).state,'stop');
    assert.equal(aborted.network_submission,'incomplete');
    assert.equal(syntheses[1].installed_presentation.installation_id,a.installation.installation_id);
    assert.equal(syntheses[1].turn.shell_id,a.installation.shell_id);
    assert.equal(syntheses[1].turn.role_revision,'1');
    assert.equal((await request(url,'/api/integration/status')).installations[0].installation_id,b.installation.installation_id);
    const closed = once(first.socket,'close');
    first.socket.terminate();
    await closed;

    const second = await connect(t,pilot);
    assert.notEqual(second.sessionId,first.sessionId);
    await start(second);
    stop(second);
    const reconnected = await outcomeFor(second,outcomes,'sent');
    assert.equal(second.packets.length,clip.length);
    assert.equal(syntheses[2].installed_presentation.installation_id,b.installation.installation_id);
    assert.equal(syntheses[2].turn.shell_id,b.installation.shell_id);
    assert.equal(syntheses[2].turn.role_revision,'2');
    assert.equal(reconnected.network_submission,'submitted');
    assert.equal(second.messages.filter(message => message.state === 'sentence_start').length,1);
    assert.equal(second.messages.find(message => message.type === 'llm').emotion,'neutral');
    const saved = pilot.conversation.persistence.get('chat.turns',reconnected.turn_id);
    assert.equal(saved.output_plan.find(output => output.type === 'render.expression').expression,'concerned');
    assert.equal(reconnected.requested_expression,'concerned');
    assert.equal(reconnected.emitted_expression,'neutral');
    assert.equal((await request(url,'/api/chat/history')).messages.length,8);
    const queued = await request(url,'/api/outbox?target=vocat');
    assert.deepEqual(queued.commands,[]);
    const inherited = pilot.conversation.persistence.list('output.commands')
      .filter(command => command.target === 'vocat' && command.source_event_id === 'reply-voice-pilot-seed-text');
    assert.ok(inherited.length > 0 && inherited.every(command => command.status === 'queued'));
    const output = pilot.conversation.persistence.list('output.commands')
      .filter(command => command.target === 'vocat' && command.source_event_id.startsWith('reply-voice-chat-'));
    assert.equal(output.length,6);
    assert.ok(output.every(command => command.status === 'failed'
      && command.acknowledgment.error.code === 'pilot_legacy_route_disabled'
      && command.acknowledgment.payload.playback_completion === 'unobserved'));
    assert.equal(output.filter(command => command.acknowledgment.payload.network_outcome === 'turn_cancelled').length,2);
    const incoming = pilot.conversation.persistence.list('input.events').map(row => row.event)
      .filter(event => event.type === 'voice.asr.final');
    assert.equal(incoming.length,3);
    assert.ok(incoming.every(event => event.source === 'xiaozhi-voice-pilot'));
    assert.equal(prompts.length,4);
  } finally { await pilot.close(); }
  await assert.rejects(startVoicePilot({...args,stateDirectory:pilot.directory}),/pilot_database_exists_enable_resume/);
  const restored = await startVoicePilot({...args,stateDirectory:pilot.directory,resume:true});
  try {
    assert.equal((await request(restored.conversationUrl,'/api/chat/history')).messages.length,8);
    assert.equal((await request(restored.conversationUrl,'/api/integration/status')).installations[0].installation_id,
      installation('b',2).installation.installation_id);
    const replay = await textTurn(restored,'voice-pilot-seed-text','请记住我的测试物叫青石铃。');
    assert.equal(replay.duplicate,true);
    assert.equal(prompts.length,4);
    assert.ok(verifyClientSnapshot(snapshot).modules > 0);
    assert.equal(verifyClientSnapshot(join(restored.directory,'client'),{voicePilot:true}).variant,'voice-pilot-continuity');
  } finally { await restored.close(); }
  assert.equal(sha256(backup),backupSha256);
});

const realConfig = process.env.DESKBOT_VOICE_PILOT_CONFIG;
test('Chinese audio and DeepSeek share the original chat save with text',{skip:!realConfig,timeout:140000},async t => {
  const archived = JSON.parse(readFileSync(resolve(ROOT,'tmp/client-conversation-20261118/backup-validation.json'),'utf8'));
  const backup = resolve(ROOT,archived.baseline);
  const voice = new WindowsVoice(), outcomes = [];
  const question = await voice.run('synthesize',{text:'我最喜欢什么颜色',rate:16000});
  const pcm = pcmFromWav(question.wav,16000);
  const input = encodePcm(Buffer.concat([pcm,Buffer.alloc(16000*2*1.2)]),16000);
  let transcript, synthesisText;
  const pilot = await startVoicePilot({clientRoot:snapshot,backup,backupSha256:archived.baseline_sha256,
    configPath:realConfig,sourceId:archived.installation.source_id,port:0,voicePort:0,
    voice:{
      async recognize(data){transcript = await voice.recognize(data);return transcript;},
      async synthesize(data){synthesisText = data.text;return voice.synthesize(data);},
    },
    onOutcome:value => outcomes.push(value),
  });
  const url = pilot.conversationUrl;
  try {
    const before = (await request(url,'/api/chat/history')).messages;
    assert.ok(before.length > 0);
    const text = await textTurn(pilot,'voice-pilot-real-text','请记住，我最喜欢的颜色是靛蓝色。用一句简短的话回应。');
    assert.equal(text.turn.provider,'deepseek-api-v0.1');
    const peer = await connect(t,pilot);
    const startedAt = performance.now();
    peer.control('listen',{state:'start',mode:'auto'});
    speakOnWire(peer,input);
    const result = await outcomeFor(peer,outcomes,'sent');
    assert.match(transcript.clean_utterance,/颜色/);
    assert.match(transcript.clean_utterance,/什么|哪/);
    assert.equal(peer.messages.find(m => m.type === 'stt').text,transcript.clean_utterance);
    const sentence = peer.messages.find(m => m.state === 'sentence_start').text;
    assert.equal(sentence,synthesisText);
    assert.match(sentence,/靛蓝/);
    const reply = (await request(url,'/api/chat/history')).messages.find(m => m.id === result.turn_id+':assistant');
    assert.equal(reply?.text,sentence);
    assert.ok(peer.packets.length >= 10);
    assert.equal(peer.packets.length,result.packets);
    const decoded = decodePackets(peer.packets,24000);
    assert.ok(rms(decoded) > 0.005);
    assert.equal(peer.messages.at(-1).state,'stop');
    assert.equal(result.network_submission,'submitted');
    assert.equal(result.playback_completion,'unobserved');

    const followup = await textTurn(pilot,'voice-pilot-real-followup','什么颜色？');
    assert.equal(followup.turn.provider,'deepseek-api-v0.1');
    assert.match(followup.turn.reply,/靛蓝/);
    const recent = followup.turn.prompt.text.split('[DESKBOT_RECENT_CONVERSATION]')[1].split('[/DESKBOT_RECENT_CONVERSATION]')[0];
    assert.ok(recent.includes(transcript.clean_utterance));
    const history = (await request(url,'/api/chat/history')).messages;
    assert.deepEqual(history.slice(0,before.length),before);
    assert.equal(history.length,before.length+6);
    const status = await request(url,'/api/integration/status');
    const usage = await request(url,'/api/life/model-usage?scope=lab&run=current');
    assert.equal(status.model.status,'connected');
    assert.equal(usage.totals.provider_attempts,3);
    assert.equal((await request(url,'/api/outbox?target=vocat')).commands.length,0);
    assert.equal(sha256(backup),archived.baseline_sha256);
    const installed = pilot.conversation.persistence.list('chat.turns').find(turn => turn.turn_id === result.turn_id);
    assert.equal(installed?.provider,'deepseek-api-v0.1');
    assert.equal(installed?.installed_presentation?.installation_id,archived.installation.installation);
    writeFileSync(join(pilot.directory,'input.wav'),wavFromPcm(pcm,16000));
    writeFileSync(join(pilot.directory,'reply.wav'),wavFromPcm(decoded,24000));
    const report = {status:'passed',scope:'desktop_loopback',recorded_at:new Date().toISOString(),
      input_source:'synthetic_chinese_recording',transcript:transcript.clean_utterance,asr_provider:transcript.provider,
      reply:sentence,followup:followup.turn.reply,model:status.model.model,tts_voice:result.actual_voice,
      text_model_received_voice_context:true,saved_subtitle_and_speech_match:true,
      history_messages:{before:before.length,after:history.length},installation_preserved:true,provider_attempts:3,
      incoming_packets:input.length,outgoing_packets:peer.packets.length,
      first_audio_ms:Math.round(peer.firstAudioAt-startedAt),reply_duration_ms:Math.round(decoded.length/48),
      network_submission:result.network_submission,playback_completion:result.playback_completion,
      physical_device_tested:false,original_backup_unchanged:true};
    writeFileSync(join(pilot.directory,'report.json'),JSON.stringify(report,null,2)+'\n');
    process.stdout.write(`Voice pilot evidence: ${pilot.directory}\n`);
  } finally { await pilot.close(); }
});

test('known model failure permits a new turn and a lost response recovers the original turn',{timeout:30000},async t => {
  const directory = temporaryDirectory(t), backup = join(directory,'seed.sqlite');
  const seed = await createClientWithInstallation({clientRoot:snapshot,database:backup,sourceId});
  try { seed.deviceRegistry.register({character_id:actor,device_id:device,hardware:'esp32-s3',firmware:'test'}); }
  finally { seed.persistence.close(); }
  const configPath = join(directory,'test-model.json');
  writeFileSync(configPath,JSON.stringify({base_url:'https://api.deepseek.com',model:'deepseek-flash',api_key:'test-only'}));
  let calls = 0, lose = true;
  const outcomes = [];
  const pilot = await startVoicePilot({clientRoot:snapshot,backup,backupSha256:sha256(backup),configPath,sourceId,
    port:0,voicePort:0,stateParent:directory,
    fetchImpl:async () => ++calls === 1 ? new Response('unauthorized',{status:401})
      : Response.json({choices:[{message:{content:'我听见了。'}}]}),
    clientFetchImpl:async (url,options) => {
      const response = await fetch(url,options);
      if (lose && response.ok && String(url).endsWith('/api/voice/asr')) {
        lose = false;
        await response.arrayBuffer();
        throw new Error('response_dropped_after_commit');
      }
      return response;
    },
    voice:{recognize:async()=>({raw_utterance:'你好',clean_utterance:'你好',provider:'test'}),
      synthesize:async()=>({audio:DOWNLINK,packets:encodePcm(Buffer.alloc(24000*2*0.12),24000)})},
    onOutcome:value => outcomes.push(value),
  });
  try {
    const url = pilot.conversationUrl, status = () => request(url,'/api/integration/status');
    async function tryTurn(expected) {
      const peer = await connect(t,pilot);
      peer.control('listen',{state:'start',mode:'manual'});
      speakOnWire(peer,encodePcm(Buffer.alloc(16000*2*0.06),16000));
      peer.control('listen',{state:'stop'});
      return {peer, result:await outcomeFor(peer,outcomes,expected)};
    }
    const failed = await tryTurn('llm_http_error');
    await waitFor(()=>failed.peer.closed,'provider failure did not end the session');
    assert.equal(failed.peer.packets.length,0);
    assert.equal(calls,1);
    assert.equal((await status()).voice_pilot.recovery,'ready');

    const lost = await tryTurn('client_delivery_unknown');
    await waitFor(()=>lost.peer.closed,'lost response did not end the session');
    assert.equal(lost.peer.packets.length,0);
    assert.equal((await status()).voice_pilot.recovery,'client_delivery_unknown');
    const recovered = await request(url,'/api/voice/pilot/recover',{});
    assert.equal(recovered.status,'recovered_no_replay');
    assert.equal(calls,2);
    assert.equal((await status()).voice_pilot.recovery,'ready');
    const settled = pilot.conversation.persistence.list('output.commands').filter(command =>
      command.target==='vocat' && command.acknowledgment?.payload.network_outcome==='delivery_unknown');
    assert.equal(settled.length,2);
    assert.ok(settled.every(command=>command.acknowledgment.payload.playback_completion==='unobserved'));

    const next = await tryTurn('sent');
    assert.equal(next.peer.packets.length,2);
    assert.equal(calls,3);
    assert.equal((await request(url,'/api/chat/history')).messages.length,4);
  } finally { await pilot.close(); }
});
