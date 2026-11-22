import assert from 'node:assert/strict';
import {test} from 'node:test';
import {once} from 'node:events';
import {OpusScript,WebSocket} from './runtime.mjs';
import {wavFromPcm,pcmFromWav,encodePcm,decodePackets,makeDecoder,rms} from './audio.mjs';
import {SilenceEndpoint,PacedPeer,NetworkSession} from './network-session.mjs';
import {createVoiceGateway} from './websocket-gateway.mjs';
import {UPLINK,DOWNLINK,encodeAudio} from '../client-4b2a/xiaozhi-codec.mjs';

const binding={character_id:'shaping-001',device_id:'network-fixture',role_revision:'4b2b-test'};
const token='4b2b-local-test-token-1234567890';
const headers={'authorization':`Bearer ${token}`,'device-id':'test-head','client-id':'test-owner','protocol-version':'1'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function sine(rate,ms=600){
  const pcm=Buffer.alloc(rate*ms/1000*2);
  for(let i=0;i<pcm.length/2;i++)pcm.writeInt16LE(Math.round(Math.sin(i*2*Math.PI*220/rate)*10000),i*2);
  return pcm;
}
function fakeClient(){return {calls:[],finished:[],
  async ingestFinal({turn,transcript}){this.calls.push({turn,transcript});return {turn:{
    turn_id:`voice-chat-${turn.utterance_id}`,input_event:{...turn,event_id:`voice-chat-${turn.utterance_id}`},
    reply:'这是网络联测。',output_plan:[{type:'render.expression',expression:'happy'}]}};},
  async finish(result,turn,outcome){this.finished.push({turn,outcome});}};}
function fakeVoice(){return {async recognize(){return {raw_utterance:'你好',clean_utterance:'你好',provider:'network-test'};},
  async synthesize(){return {audio:{...DOWNLINK},packets:encodePcm(sine(24000,120),24000)};}};}
function localSession(options={}){
  const messages=[],outcomes=[],closed=[],client=fakeClient(),voice=fakeVoice();
  const peer={sendJson:value=>messages.push(value),sendAudio:async()=>true,close:(code,reason)=>closed.push({code,reason})};
  const session=new NetworkSession({binding,client,recognize:value=>voice.recognize(value),
    synthesize:value=>voice.synthesize(value),peer,onOutcome:value=>outcomes.push(value),...options});
  session.receiveJson({type:'hello',transport:'websocket',version:1,audio_params:UPLINK});
  const control=(type,extra={})=>session.receiveJson({type,session_id:session.sessionId,...extra});
  return {session,client,messages,outcomes,closed,peer,control};
}
async function gatewayFixture(action,options={}){
  const client=fakeClient(),voice=fakeVoice(),outcomes=[];
  const gateway=createVoiceGateway({binding,client,voice,token,deviceHeader:'test-head',clientHeader:'test-owner',
    onOutcome:value=>outcomes.push(value),...options});
  await new Promise(resolve=>gateway.server.listen(0,'127.0.0.1',resolve));
  const url=`ws://127.0.0.1:${gateway.server.address().port}/xiaozhi/v1`;
  try{await action({gateway,url,client,outcomes});}finally{await gateway.close();}
}
async function connect(url,version=1){
  const socket=new WebSocket(url,{headers:{...headers,'protocol-version':String(version)}});
  const messages=[],binary=[];socket.on('message',(bytes,isBinary)=>isBinary?binary.push(bytes):messages.push(JSON.parse(bytes)));
  await once(socket,'open');socket.send(JSON.stringify({type:'hello',version,transport:'websocket',audio_params:UPLINK}));
  for(let i=0;i<100&&!messages.length;i++)await delay(5);
  assert.equal(messages[0]?.type,'hello');
  return {socket,messages,binary,sessionId:messages[0].session_id};
}
for(const rate of [16000,24000]){
  test(`WAV and real Opus round trip at ${rate} Hz`,()=>{
    const input=sine(rate),wav=wavFromPcm(input,rate);assert.deepEqual(pcmFromWav(wav,rate),input);
    const packets=encodePcm(input,rate),output=decodePackets(packets,rate);
    assert.equal(packets.length,10);assert.equal(output.length,input.length);assert.ok(rms(output)>0.1);
  });
}
test('WAV validates declared length, chunk length, format and PCM alignment',()=>{
  for(const mutation of [b=>b.writeUInt32LE(1,4),b=>b.writeUInt32LE(999999,40),
    b=>b.writeUInt16LE(2,22),b=>b.writeUInt16LE(8,34),b=>b.writeUInt32LE(48000,24)]){
    const wav=wavFromPcm(sine(16000,60),16000);mutation(wav);assert.throws(()=>pcmFromWav(wav,16000));
  }
  assert.throws(()=>wavFromPcm(Buffer.alloc(3),16000));assert.throws(()=>encodePcm(Buffer.alloc(0),16000));
  assert.throws(()=>encodePcm(Buffer.alloc(16000*2*31),16000));
});
test('real decoder rejects a valid Opus packet with the wrong frame duration',()=>{
  const encoder=new OpusScript(16000,1,OpusScript.Application.VOIP),decoder=makeDecoder(16000);
  try{const twenty=encoder.encode(sine(16000,20),320);assert.throws(()=>decoder.decode(twenty),{code:'unsupported_packet_duration'});}
  finally{encoder.delete();decoder.close();}
});
test('endpoint waits for voice followed by silence and bounds silent or continuous captures',()=>{
  const end=new SilenceEndpoint();for(let i=0;i<3;i++)assert.equal(end.push(0.1),null);
  for(let i=0;i<14;i++)assert.equal(end.push(0),null);assert.equal(end.push(0),'speech_end');
  const silent=new SilenceEndpoint();let reason;for(let i=0;i<134;i++)reason=silent.push(0);assert.equal(reason,'no_speech');
  const noisy=new SilenceEndpoint();for(let i=0;i<500;i++)reason=noisy.push(0.2);assert.equal(reason,'maximum');
  assert.throws(()=>end.push(NaN));
});
test('paced peer emits 60ms audio slots and includes the final packet duration',async()=>{
  let clock=0;const events=[];
  const socket={readyState:1,bufferedAmount:0,send(bytes,options,done){events.push({at:clock,binary:Buffer.isBuffer(bytes)});done?.();}};
  const peer=new PacedPeer(socket,{clock:()=>clock,sleep:async ms=>{clock+=ms;}});
  peer.sendJson({type:'tts',state:'start'});await peer.sendAudio(Buffer.from([1]));await peer.sendAudio(Buffer.from([1]));
  peer.sendJson({type:'tts',state:'stop'});
  assert.deepEqual(events.map(e=>e.at),[0,0,60,120]);
});
test('paced output rejects cancellation, closed transport and excessive buffer',async()=>{
  const controller=new AbortController();controller.abort();const socket={readyState:1,bufferedAmount:0,send(){assert.fail('sent');}};
  const peer=new PacedPeer(socket);await assert.rejects(peer.sendAudio(Buffer.from([1]),{signal:controller.signal}));
  socket.bufferedAmount=200000;await assert.rejects(peer.sendAudio(Buffer.from([1])),{code:'transport_backpressure'});
  socket.bufferedAmount=0;socket.readyState=3;await assert.rejects(peer.sendAudio(Buffer.from([1])),{code:'transport_closed'});
});
test('auto listen endpoints real decoded packets once, ignores trailing queued capture',async()=>{
  const h=localSession();h.control('listen',{state:'start',mode:'auto'});
  const packets=encodePcm(Buffer.concat([sine(16000,300),Buffer.alloc(16000*2*2)]),16000);
  for(const p of packets)h.session.receiveAudio(p);
  await h.session.drain();await tick();
  assert.equal(h.client.calls.length,1);assert.equal(h.outcomes.filter(x=>x.status==='sent').length,1);
  h.session.close();
});
test('auto silent capture does not become an owner message',async()=>{
  const h=localSession();h.control('listen',{state:'start',mode:'auto'});
  for(const packet of encodePcm(Buffer.alloc(16000*2*9),16000)){
    if(h.session.state==='closed')break;h.session.receiveAudio(packet);
  }
  await h.session.drain();assert.equal(h.client.calls.length,0);assert.equal(h.outcomes[0].status,'no_speech');
  assert.deepEqual(h.closed,[{code:4001,reason:'no_speech'}]);
});
test('capture idle timeout clears audio and reports a stalled microphone stream',async()=>{
  const h=localSession({audioIdleMs:10});h.control('listen',{state:'start',mode:'auto'});await delay(25);
  assert.equal(h.outcomes[0].status,'capture_idle_timeout');assert.equal(h.session.state,'closed');
  assert.deepEqual(h.closed,[{code:4001,reason:'capture_idle_timeout'}]);
});

test('failed voice stages close the listening peer with a recoverable reason',async()=>{
  for(const reason of ['empty_audio','empty_transcript','recognition_timeout','synthesis_timeout']){
    const h=localSession({stageTimeoutMs:15,
      recognize:reason==='recognition_timeout'?()=>new Promise(()=>{}):async()=>({raw_utterance:'你好',
        clean_utterance:reason==='empty_transcript'?'':'你好',provider:'test'}),
      synthesize:()=>new Promise(()=>{}),
    });
    h.control('listen',{state:'start',mode:'auto'});
    if(reason!=='empty_audio')h.session.receiveAudio(encodePcm(sine(16000,60),16000)[0]);
    await h.control('listen',{state:'stop'});await tick();
    assert.equal(h.outcomes[0].status,reason);assert.equal(h.session.state,'closed');
    assert.deepEqual(h.closed,[{code:4001,reason}]);
  }
});

test('an old failed cleanup cannot close a newer listening turn',async()=>{
  const entered=deferred(),gate=deferred();
  const h=localSession({synthesize:async()=>{throw new Error('synthesis_failed');}});
  h.client.finish=async()=>{entered.resolve();await gate.promise;throw new Error('ack_failed');};
  h.control('listen',{state:'start',mode:'manual'});
  h.session.receiveAudio(encodePcm(sine(16000,60),16000)[0]);
  const old=h.control('listen',{state:'stop'});await entered.promise;
  h.control('listen',{state:'start',mode:'manual'});gate.resolve();await old;await tick();
  assert.equal(h.outcomes[0].status,'cleanup_failed');assert.equal(h.session.state,'listening');
  assert.deepEqual(h.closed,[]);h.session.close();
});

test('actual peer observes silent-turn closure and can open a fresh connection',async()=>gatewayFixture(async f=>{
  const first=await connect(f.url),closed=once(first.socket,'close');
  first.socket.send(JSON.stringify({type:'listen',state:'start',mode:'auto',session_id:first.sessionId}));
  for(const packet of encodePcm(Buffer.alloc(16000*2*9),16000))first.socket.send(packet);
  const [code,reason]=await closed;assert.equal(code,4001);assert.equal(reason.toString(),'no_speech');
  for(let i=0;i<100&&f.gateway.snapshot().sessions;i++)await delay(5);
  const second=await connect(f.url);second.socket.send(JSON.stringify({type:'listen',state:'start',mode:'manual',session_id:second.sessionId}));
  for(const packet of encodePcm(sine(16000,180),16000))second.socket.send(packet);
  second.socket.send(JSON.stringify({type:'listen',state:'stop',session_id:second.sessionId}));
  for(let i=0;i<200&&!f.outcomes.some(x=>x.status==='sent');i++)await delay(5);
  assert.equal(f.client.calls.length,1);assert.ok(f.outcomes.some(x=>x.status==='sent'));second.socket.close();
}));

test('late send callback after cancellation cannot change the next reply pacing',async()=>{
  let clock=0,callback;const sleeps=[],controller=new AbortController();
  const peer=new PacedPeer({readyState:1,bufferedAmount:0,send(_bytes,options,done){if(options)callback=done;}},
    {clock:()=>clock,sleep:async ms=>{sleeps.push(ms);clock+=ms;}});
  peer.sendJson({type:'tts',state:'start'});const pending=peer.sendAudio(Buffer.from([1]),{signal:controller.signal});
  const cancelled=assert.rejects(pending);controller.abort();await cancelled;
  clock=500;peer.sendJson({type:'tts',state:'start'});callback();await tick();
  assert.equal(peer.nextFrame,500);assert.deepEqual(sleeps,[]);
});
test('wake detection is not ingested and realtime mode is explicit',()=>{
  const h=localSession();h.control('listen',{state:'detect',text:'你好小智'});
  assert.equal(h.client.calls.length,0);assert.throws(()=>h.control('listen',{state:'start',mode:'realtime'}),{code:'unsupported_listen_mode'});
  h.session.close();
});
for(const version of [1,2,3]){
  test(`actual WebSocket exchanges v${version} audio and correlated controls`,async()=>gatewayFixture(async f=>{
    const c=await connect(f.url,version),control=(state)=>c.socket.send(JSON.stringify({type:'listen',state,mode:'manual',session_id:c.sessionId}));
    control('start');for(const packet of encodePcm(sine(16000,180),16000))c.socket.send(encodeAudio(packet,version));control('stop');
    for(let i=0;i<200&&(!f.outcomes.length||c.messages.at(-1)?.state!=='stop');i++)await delay(5);
    assert.equal(f.outcomes[0]?.status,'sent');assert.equal(c.binary.length,2);assert.equal(f.client.calls.length,1);
    assert.equal(c.messages.at(-1).state,'stop');c.socket.close();
  }));
}
test('upgrade checks token, device, owner, path and protocol version',async()=>gatewayFixture(async f=>{
  for(const [url,custom,status] of [[f.url,{authorization:'Bearer wrong'},401],
    [f.url,{'device-id':'other'},401],[f.url,{'client-id':'other'},401],
    [f.url,{'protocol-version':'4'},400],[f.url+'/other',{},404]]){
    await new Promise((resolve,reject)=>{
      const socket=new WebSocket(url,{headers:{...headers,...custom}});
      socket.on('open',()=>reject(new Error('unexpected_open')));socket.on('error',()=>{});
      socket.on('unexpected-response',(_,response)=>{assert.equal(response.statusCode,status);response.resume();socket.terminate();resolve();});
    });
  }
}));
test('header/hello version mismatch closes the connection',async()=>gatewayFixture(async f=>{
  const socket=new WebSocket(f.url,{headers});await once(socket,'open');const closed=once(socket,'close');
  socket.send(JSON.stringify({type:'hello',version:2,transport:'websocket',audio_params:UPLINK}));
  assert.equal((await closed)[0],1008);assert.equal(f.outcomes[0].status,'protocol_version_mismatch');
}));
test('a new authenticated connection replaces the old one without late voice',async()=>{
  const gate=deferred(),entered=deferred(),voice=fakeVoice();
  voice.synthesize=async()=>{entered.resolve();await gate.promise;return {audio:DOWNLINK,packets:encodePcm(sine(24000,60),24000)};};
  await gatewayFixture(async f=>{
    const first=await connect(f.url);first.socket.send(JSON.stringify({type:'listen',state:'start',mode:'manual',session_id:first.sessionId}));
    first.socket.send(encodePcm(sine(16000,60),16000)[0]);
    first.socket.send(JSON.stringify({type:'listen',state:'stop',session_id:first.sessionId}));await entered.promise;
    const closed=once(first.socket,'close'),second=await connect(f.url);assert.equal((await closed)[0],4000);
    gate.resolve();await delay(30);assert.equal(first.binary.length,0);assert.equal(second.binary.length,0);
    assert.ok(f.outcomes.some(x=>x.status==='turn_cancelled'));second.socket.close();
  },{voice});
});
