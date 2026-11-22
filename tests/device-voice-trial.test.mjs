import assert from 'node:assert/strict';
import {once} from 'node:events';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {UPLINK, DOWNLINK} from '../integrations/client-4b2a/xiaozhi-codec.mjs';
import {encodePcm} from '../integrations/client-4b2b/audio.mjs';
import {PacedPeer} from '../integrations/client-4b2b/network-session.mjs';
import {WebSocket} from '../integrations/client-4b2b/runtime.mjs';
import {createVoiceGateway} from '../integrations/client-4b2b/websocket-gateway.mjs';
import {createDeviceVoiceTrial, trustedTrialNetwork} from '../integrations/client-conversation/voice-device-trial.mjs';
import {changeTrialAddress} from '../scripts/configure-device-voice-trial.mjs';

const device = {host:'127.0.0.1',device_mac:'02:00:00:00:00:04',client_uuid:'74000000-6800-4000-9000-740000000001',
  firmware_elf_sha256:'a'.repeat(64)};
const token = 'b'.repeat(64), setupKey = 'c'.repeat(24);
const identityHeaders = {'device-id':device.device_mac,'client-id':device.client_uuid};
const body = {chip_model_name:'esp32s3',mac_address:device.device_mac,uuid:device.client_uuid,
  application:{elf_sha256:device.firmware_elf_sha256}};
const binding = {character_id:'trial-fixture',device_id:'phone-hotspot-fixture',role_revision:'1'};

async function fixture(t, allowPeer = ip => ip === '127.0.0.1') {
  const trial = createDeviceVoiceTrial({config:device,token,setupKey,allowPeer});
  const outcomes = [], heard = [], requests = [];
  const audio = encodePcm(Buffer.alloc(24000*2*0.12),24000);
  const gateway = createVoiceGateway({binding,token,deviceHeader:device.device_mac,clientHeader:device.client_uuid,
    deviceTrial:trial,onOutcome: v => outcomes.push(v),
    client: {
      async ingestFinal({turn,transcript}) {
        requests.push(transcript.clean_utterance);
        return {turn:{turn_id:turn.utterance_id,input_event:{...turn,event_id:`voice-chat-${turn.utterance_id}`},
          reply:'是靛蓝色。',output_plan:[{type:'render.expression',expression:'neutral'}]}};
      }, async finish() {},
    },
    voice: {
      async recognize({packets}) {
        heard.push(packets.length);
        return {raw_utterance:'我最喜欢什么颜色',clean_utterance:'我最喜欢什么颜色',provider:'device-trial-fixture'};
      },
      async synthesize() {return {audio:DOWNLINK,packets:audio};},
    },
  });
  await once(gateway.server.listen(0,'127.0.0.1'),'listening');
  t.after(() => gateway.close());
  const root = `http://127.0.0.1:${gateway.server.address().port}`;
  const setup = (info=body, headers=identityHeaders) => fetch(root+trial.path,
    {method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify(info)});
  return {root,trial,gateway,setup,outcomes,heard,requests};
}
function wire(url, headers={}) {
  return new WebSocket(url,{headers:{host:device.host, ...identityHeaders,
    authorization:`Bearer ${token}`,'protocol-version':'1', ...headers}});
}
async function until(test, label) {
  const stop = performance.now()+5000;
  while (performance.now() < stop) {
    if (test()) return;
    await delay(12);
  }
  assert.fail(label);
}

test('trial accepts only the configured private interface and its subnet', () => {
  const adapters = {Wlan:[{family:'IPv4',address:'172.25.246.2',netmask:'255.255.255.0',internal:false}]};
  const allow = trustedTrialNetwork('172.25.246.2',adapters);
  assert.equal(allow('172.25.246.132'),true);
  for (const address of ['172.25.247.2','127.0.0.1','::1','::ffff:172.25.246.132','100.78.13.183',undefined])
    assert.equal(allow(address),false);
  for (const host of ['0.0.0.0','127.0.0.1','100.78.13.183'])
    assert.throws(() => trustedTrialNetwork(host,adapters),/requires_private_ipv4/);
  assert.throws(() => trustedTrialNetwork('172.25.246.5',adapters),/not_on_this_computer/);
});

test('the trial address checks the firmware and only returns WebSocket configuration', async t => {
  const f = await fixture(t);
  assert.deepEqual(f.trial.status(),{state:'waiting',at:null});
  for (const path of ['/health','/api/chat/history','/deskbot-voice/typo',f.trial.path]) {
    const response = await fetch(f.root+path);
    assert.equal(response.status,404);
    await response.arrayBuffer();
  }
  const cases = [
    [body,{'device-id':'other','client-id':device.client_uuid},403],
    [body,{...identityHeaders,origin:'http://other.example'},404],
    [{...body,application:{elf_sha256:'d'.repeat(64)}},identityHeaders,409],
    [{...body,uuid:'other'},identityHeaders,409],
    [{...body,unused:'e'.repeat(8192)},identityHeaders,400],
  ];
  for (const [info,headers,code] of cases) {
    const response = await f.setup(info,headers);
    assert.equal(response.status,code);
    assert.equal(Object.hasOwn(await response.json(),'websocket'),false);
  }
  assert.equal(f.trial.status().state,'firmware_mismatch');
  const response = await f.setup();
  assert.equal(response.status,200);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(await response.json(),{websocket:{url:f.root.replace('http:','ws:')+'/xiaozhi/v1',token,version:1}});
  assert.equal(f.trial.status().state,'configured');
});

test('firmware-shaped upgrade, wake frames, two turns and rejoin use one trial address', {timeout:15000}, async t => {
  const f = await fixture(t);
  const {websocket} = await (await f.setup()).json();
  for (const [headers,expected] of [
    [{'device-id':'other'},401],[{authorization:'Bearer other'},401],
    [{origin:'http://other.example'},403],[{'protocol-version':'2'},400],
  ]) {
    const rejected = wire(websocket.url,headers);
    const [,response] = await once(rejected,'unexpected-response');
    response.resume();
    assert.equal(response.statusCode,expected);
  }
  const nonSilent = Buffer.alloc(16000*2*0.3);
  for (let i=0;i<nonSilent.length;i+=2) nonSilent.writeInt16LE(Math.round(7000*Math.sin(i/11)),i);
  const spoken = encodePcm(Buffer.concat([nonSilent,Buffer.alloc(16000*2*1.08)]),16000);
  async function connect() {
    const ws=wire(websocket.url);
    const json=[], audio=[];
    ws.on('message',(bytes,binary) => binary ? audio.push(bytes) : json.push(JSON.parse(bytes.toString('utf8'))));
    await once(ws,'open');
    ws.send(JSON.stringify({type:'hello',transport:'websocket',version:1,features:{mcp:true},audio_params:UPLINK}));
    await until(() => json[0]?.type === 'hello','missing ESP hello');
    const id = json[0].session_id;
    return {ws,id,json,audio,send: (type,fields) => ws.send(JSON.stringify({type,session_id:id,...fields}))};
  }
  function say(peer) {
    peer.send('listen',{state:'start',mode:'auto'});
    for (const packet of spoken) peer.ws.send(packet);
  }
  const receivedStop = peer => peer.json.filter(x => x.type === 'tts' && x.state === 'stop').length;
  const first = await connect();
  first.ws.send(spoken[0]); // The real device can send a wake frame before listen.detect.
  first.send('listen',{state:'detect',text:'Wake word'});
  say(first);
  await until(() => f.outcomes[0]?.status==='sent' && receivedStop(first)===1,'first answer not received');
  assert.ok(f.heard[0] < spoken.length+1);
  assert.deepEqual(first.json.filter(x => x.type === 'tts').map(x => x.state),['start','sentence_start','stop']);
  assert.equal(first.json.find(x => x.state==='sentence_start').text,'是靛蓝色。');
  assert.equal(first.audio.length,2);
  say(first);
  await until(() => f.outcomes[1]?.status==='sent' && receivedStop(first)===2,'follow-up not received');
  const closed=once(first.ws,'close'); first.ws.terminate(); await closed;
  const second = await connect();
  assert.notEqual(first.id,second.id);
  say(second);
  await until(() => f.outcomes[2]?.status==='sent' && receivedStop(second)===1,'rejoined peer received no answer');
  assert.deepEqual(f.requests,Array(3).fill('我最喜欢什么颜色'));
  second.ws.close();
});

test('a peer outside the selected subnet gets neither configuration nor audio access', async t => {
  const f = await fixture(t, () => false);
  const response = await f.setup();
  assert.equal(response.status,404);
  await response.arrayBuffer();
  const refused = wire(f.root.replace('http:','ws:') + '/xiaozhi/v1');
  const [,upgrade] = await once(refused,'unexpected-response');
  assert.equal(upgrade.statusCode,403);
  upgrade.resume();
});

test('ESP receives the speaking state before the first paced Opus frame', async () => {
  let now = 0;
  const sent = [];
  const peer = new PacedPeer({readyState:1,bufferedAmount:0,
    send(data,opts,done) {sent.push([now,Buffer.isBuffer(data)]);done?.();}},
    {clock:() => now,sleep:async ms => {now+=ms;},firstFrameLeadMs:120});
  peer.sendJson({type:'tts',state:'start'});
  await peer.sendAudio(Buffer.from([1]));
  await peer.sendAudio(Buffer.from([2]));
  peer.sendJson({type:'tts',state:'stop'});
  assert.deepEqual(sent,[[0,false],[120,true],[180,true],[240,false]]);
});

test('address change saves the original before setting only ota_url and restores it', async t => {
  const dir = mkdtempSync(join(tmpdir(),'deskbot-voice-settings-'));
  t.after(() => rmSync(dir,{recursive:true,force:true}));
  const original = 'https://original.example/xiaozhi/ota/';
  const trialUrl = 'http://192.168.20.3:8891/deskbot-voice/' + setupKey;
  const rollbackFile = join(dir,'original.json');
  const profile = {ota_url:original,max_tx_power:-1,remember_bssid:true,sleep_mode:false};
  const writes = [];
  let restarts = 0;
  const request = async (path,value) => {
    if (path === '/advanced/config') return {...profile};
    if (path === '/advanced/submit') {
      assert.equal(JSON.parse(readFileSync(rollbackFile,'utf8')).original,original);
      writes.push(value);
      Object.assign(profile,value);
    } else if (path === '/reboot') restarts++;
    else assert.fail(path);
    return {success:true};
  };
  const config = {ssid:'Xiaozhi-A123',rollbackFile,trialUrl,request};
  await changeTrialAddress({...config,action:'apply',restart:true});
  await changeTrialAddress({...config,action:'apply'});
  assert.equal(profile.ota_url,trialUrl);
  await assert.rejects(changeTrialAddress({...config,action:'restore',ssid:'Xiaozhi-A124'}),/record_mismatch/);
  profile.ota_url = 'https://other.example/changed';
  await assert.rejects(changeTrialAddress({...config,action:'restore'}),/changed_elsewhere/);
  profile.ota_url = trialUrl;
  await changeTrialAddress({...config,action:'restore',restart:true});
  assert.deepEqual(writes,[{ota_url:trialUrl},{ota_url:original}]);
  assert.equal(restarts,2);
  assert.deepEqual(profile,{ota_url:original,max_tx_power:-1,remember_bssid:true,sleep_mode:false});
});

test('default cloud URL is restored to blank and restart waits for readback', async t => {
  const dir = mkdtempSync(join(tmpdir(),'deskbot-voice-settings-'));
  t.after(() => rmSync(dir,{recursive:true,force:true}));
  const trialUrl = 'http://192.168.20.3:8891/deskbot-voice/' + setupKey;
  let url, restarts=0, simulateWriteLoss=true;
  const request = async (path,value) => {
    if (path === '/advanced/config') return {ota_url:url || undefined,
      max_tx_power:-1,remember_bssid:false,sleep_mode:true};
    if (path === '/advanced/submit' && !simulateWriteLoss) url=value.ota_url;
    if (path === '/reboot') restarts++;
    return {success:true};
  };
  const config = {ssid:'Xiaozhi-1234',rollbackFile:join(dir,'default.json'),trialUrl,request,restart:true};
  await assert.rejects(changeTrialAddress({...config,action:'apply'}),/readback_mismatch/);
  assert.equal(restarts,0);
  simulateWriteLoss=false;
  await changeTrialAddress({...config,action:'apply'});
  await changeTrialAddress({...config,action:'restore'});
  assert.equal(url,'');
  assert.equal(restarts,2);
});
