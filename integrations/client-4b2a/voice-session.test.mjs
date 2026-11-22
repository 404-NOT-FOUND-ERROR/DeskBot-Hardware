import assert from 'node:assert/strict';
import {test} from 'node:test';
import {VoiceSession} from './voice-session.mjs';
import {OriginalClientFixture} from './original-client-fixture.mjs';
import {UPLINK, DOWNLINK, encodeAudio, decodeAudio, parseHello} from './xiaozhi-codec.mjs';

const hello = (version=1) => ({type:'hello',version,transport:'websocket',audio_params:{...UPLINK}});
const binding = {character_id:'shaping-001',device_id:'fixture-head',role_revision:'fixture-4b2a'};
const packet = Buffer.from([0xf8,0xff,0xfe]); // Fixed bytes exercise framing; no audio decoding claimed.
const deferred = () => {let resolve; const promise = new Promise(r=>{resolve=r;}); return {promise,resolve};};
const tick = () => new Promise(resolve=>setImmediate(resolve));
function harness(options={}) {
  const messages=[], audio=[], calls=[], finishes=[], synthesized=[];
  const client = {
    async ingestFinal({turn,transcript}) {
      calls.push({turn,transcript});
      return {turn:{turn_id:`voice-chat-${turn.utterance_id}`,input_event:{...turn,event_id:`voice-chat-${turn.utterance_id}`},
        reply:'我很开心，想试做一道菜。',output_plan:[{type:'render.expression',expression:'happy'}],
        ...(options.presentationAdoption ? {presentation_adoption:options.presentationAdoption} : {})}};
    },
    async finish(result,turn,outcome) {finishes.push({result,turn,outcome});},
  };
  const peer={sendJson(value){messages.push(value);},async sendAudio(value){audio.push(value);}};
  const session=new VoiceSession({binding,client,
    recognize:async()=>({raw_utterance:' 今天开心 ',clean_utterance:'今天开心',provider:'fixed-test'}),
    synthesize:async value=>{synthesized.push(value);return {audio:{...DOWNLINK},packets:[packet,packet]};},peer,...options});
  const control=(type,extra={})=>session.receiveJson({type,session_id:session.sessionId,...extra});
  const start=()=>control('listen',{state:'start',mode:'manual'});
  const stop=()=>control('listen',{state:'stop'});
  const record=()=>session.receiveAudio(encodeAudio(packet,session.version));
  return {session,client,peer,messages,audio,calls,finishes,synthesized,control,start,stop,record};
}

for(const version of [1,2,3]) {
  test(`Opus v${version} matches wire layout and copies received bytes`,()=>{
    const encoded=encodeAudio(packet,version,0x12345678);
    assert.equal(encoded.length,packet.length+(version===2?16:version===3?4:0));
    const decoded=decodeAudio(encoded,version);
    assert.deepEqual(decoded.payload,packet);
    assert.equal(decoded.timestamp,version===2?0x12345678:0);
    if(version===2)assert.equal(encoded.subarray(0,16).toString('hex'),'00020000000000001234567800000003');
    if(version===3)assert.equal(encoded.subarray(0,4).toString('hex'),'00000003');
    encoded.fill(0);assert.deepEqual(decoded.payload,packet);
  });
  test(`hello v${version} and full turn use one saved reply`,async()=>{
    const h=harness();h.session.receiveJson(hello(version));h.start();h.record();
    const first=h.stop(),duplicate=h.stop();assert.equal(first,duplicate);
    const result=await first;
    assert.equal(result.status,'sent');assert.equal(result.playback_completion,'unobserved');
    assert.equal(h.calls.length,1);assert.equal(h.audio.length,2);
    assert.equal(h.finishes[0].outcome,'sent');
    assert.deepEqual(h.messages.map(m=>[m.type,m.state??m.emotion]),[
      ['hello',undefined],['stt',undefined],['tts','start'],['llm','happy'],['tts','sentence_start'],['tts','stop']]);
    assert.equal(h.messages[4].text,h.finishes[0].result.turn.reply);
    assert.deepEqual(h.messages[0].audio_params,DOWNLINK);
    assert.deepEqual(decodeAudio(h.audio[1],version).payload,packet);
    assert.equal(h.session.state,'ready');
    await h.stop();assert.equal(h.calls.length,1);assert.equal(h.audio.length,2);
  });
}
test('codec rejects unknown version, empty, oversized and non-byte input',()=>{
  for(const fn of [()=>encodeAudio(packet,4),()=>decodeAudio(packet,0),()=>encodeAudio(Buffer.alloc(0),1),
    ()=>encodeAudio(Buffer.alloc(4097),1),()=>decodeAudio(Buffer.alloc(4097),1),()=>decodeAudio('opus',1),
    ()=>encodeAudio(packet,2,-1),()=>encodeAudio(packet,2,2**32)])assert.throws(fn);
});

test('listen start freezes the installation id and passes the adopted voice to synthesis',async()=>{
  const voice={status:'supported',requested:{provider:'gateway',voice_id:'voice-a'},
    applied:{target:'windows-system-speech',voice_id:'voice-a',voice_name:'Microsoft Huihui Desktop'},reason:null};
  const h=harness({resolvePresentationAtStart:()=>({installation_id:'install-at-start'}),
    presentationAdoption:{voice}});
  h.session.receiveJson(hello());h.start();
  assert.equal(h.session.context(h.session.active).presentation_installation_id,'install-at-start');
  h.record();const result=await h.stop();
  assert.equal(h.calls[0].turn.presentation_installation_id,'install-at-start');
  assert.equal(h.synthesized[0].voice_adoption,voice);
  assert.equal(result.voice_adoption,voice);
});

test('listen start freezes no installation even if a role appears before recognition completes',async()=>{
  let current=null;
  const h=harness({binding:{...binding,presentation_installation_id:'outdated-binding'},
    resolvePresentationAtStart:()=>current});
  h.session.receiveJson(hello());h.start();h.record();
  current={installation_id:'installed-later'};
  assert.equal((await h.stop()).status,'sent');
  assert.ok(Object.hasOwn(h.calls[0].turn,'presentation_installation_id'));
  assert.equal(h.calls[0].turn.presentation_installation_id,null);
  h.start();h.record();await h.stop();
  assert.equal(h.calls[1].turn.presentation_installation_id,'installed-later');
});
for(const version of [2,3]) {
  test(`v${version} rejects short headers, mismatched lengths and control types`,()=>{
    for(const size of [0,1,version===2?15:3,version===2?16:4])assert.throws(()=>decodeAudio(Buffer.alloc(size),version));
    const length=encodeAudio(packet,version);length[length.length-packet.length-1]=99;
    assert.throws(()=>decodeAudio(length,version),{code:'audio_length_mismatch'});
    const type=encodeAudio(packet,version);type[version===2?3:0]=1;
    assert.throws(()=>decodeAudio(type,version),{code:'invalid_audio_header'});
    const reserved=encodeAudio(packet,version);reserved[version===2?7:1]=1;
    assert.throws(()=>decodeAudio(reserved,version),{code:'invalid_audio_header'});
  });
}
test('hello checks transport, version, sample rate and frame duration',()=>{
  for(const value of [null,{},hello(4),{...hello(),transport:'udp'},
    {...hello(),audio_params:{...UPLINK,sample_rate:24000}},
    {...hello(),audio_params:{...UPLINK,frame_duration:20}}])assert.throws(()=>parseHello(value));
});
test('controls require hello, session identity, manual endpoint and valid ordering',()=>{
  const h=harness();assert.throws(()=>h.start(),{code:'invalid_hello'});
  assert.throws(()=>h.session.receiveAudio(packet),{code:'not_listening'});
  h.session.receiveJson(hello());assert.throws(()=>h.stop(),{code:'not_listening'});
  assert.throws(()=>h.session.receiveJson({type:'abort',session_id:'old'}),{code:'stale_session'});
  for(const mode of ['auto','realtime'])assert.throws(()=>h.control('listen',{state:'start',mode}),{code:'manual_listen_required'});
  assert.throws(()=>h.control('mcp',{payload:{}}),{code:'unsupported_control'});
  assert.throws(()=>h.control('listen',{state:'detect'}),{code:'unsupported_listen_state'});
  h.session.close();assert.throws(()=>h.start(),{code:'session_closed'});
});
test('invalid binding and remote fixture URL fail before use',()=>{
  assert.throws(()=>harness({binding:{}}),{code:'binding_required'});
  for(const url of ['https://127.0.0.1','http://example.com','http://a:b@127.0.0.1','http://127.0.0.1/a'])
    assert.throws(()=>new OriginalClientFixture(url));
});
test('empty audio and empty final transcript create no client turn',async()=>{
  for(const kind of ['audio','transcript']){
    const h=harness({recognize:async()=>({raw_utterance:' ',clean_utterance:' ',provider:'fixed-test'})});
    h.session.receiveJson(hello());h.start();if(kind==='transcript')h.record();
    assert.equal((await h.stop()).status,kind==='audio'?'empty_audio':'empty_transcript');
    assert.equal(h.calls.length,0);assert.equal(h.audio.length,0);
  }
});
test('receive limits invalidate excessive audio and reject a late stop',async()=>{
  let clock=0;const h=harness({now:()=>clock});h.session.receiveJson(hello());h.start();
  clock=30001;assert.throws(()=>h.record(),{code:'utterance_too_large'});assert.equal(h.session.state,'ready');
  clock=0;h.start();h.record();clock=30001;
  assert.equal((await h.stop()).status,'utterance_too_large');assert.equal(h.calls.length,0);
});
test('packet count and byte budget are independently bounded',()=>{
  for(const large of [false,true]){
    const h=harness();h.session.receiveJson(hello());h.start();
    const bytes=Buffer.alloc(large?4096:1),limit=large?64:500;
    for(let i=0;i<limit;i++)h.session.receiveAudio(bytes);
    assert.throws(()=>h.session.receiveAudio(bytes),{code:'utterance_too_large'});
  }
});
test('abort during recognition drops a late result',async()=>{
  const gate=deferred(),h=harness({recognize:()=>gate.promise});h.session.receiveJson(hello());h.start();h.record();
  const job=h.stop();h.control('abort');gate.resolve({raw_utterance:'旧话',clean_utterance:'旧话',provider:'fixed-test'});
  assert.equal((await job).status,'turn_cancelled');assert.equal(h.calls.length,0);assert.equal(h.messages.length,1);
});
test('abort while original chat runs settles only the old outbox',async()=>{
  const h=harness(),gate=deferred(),original=h.client.ingestFinal;
  h.client.ingestFinal=async input=>{await gate.promise;return original(input);};
  h.session.receiveJson(hello());h.start();h.record();const job=h.stop();await tick();
  h.control('abort');gate.resolve();assert.equal((await job).status,'turn_cancelled');
  assert.equal(h.finishes[0].outcome,'turn_cancelled');assert.equal(h.audio.length,0);
  assert.ok(!h.messages.some(m=>m.type==='tts'));
});
test('abort during synthesis prevents speaking and allows a new turn',async()=>{
  const gate=deferred(),h=harness({synthesize:()=>gate.promise});h.session.receiveJson(hello());h.start();h.record();
  const old=h.stop();await tick();h.control('abort');
  h.session.synthesize=async()=>({audio:{...DOWNLINK},packets:[packet]});h.start();h.record();
  assert.equal((await h.stop()).status,'sent');
  gate.resolve({audio:{...DOWNLINK},packets:[packet]});assert.equal((await old).status,'turn_cancelled');
  assert.equal(h.audio.length,1);assert.deepEqual(h.finishes.map(x=>x.outcome).sort(),['sent','turn_cancelled']);
});
test('abort during audio suppresses remaining frames and stops once',async()=>{
  const gate=deferred(),audio=[];
  const h=harness();h.peer.sendAudio=async value=>{audio.push(value);await gate.promise;};
  h.session.receiveJson(hello());h.start();h.record();const job=h.stop();await tick();h.control('abort');gate.resolve();
  assert.equal((await job).status,'turn_cancelled');assert.equal(audio.length,1);
  assert.equal(h.messages.filter(m=>m.type==='tts'&&m.state==='stop').length,1);
});
test('closing an old connection suppresses its late completion',async()=>{
  const gate=deferred(),h=harness({synthesize:()=>gate.promise});h.session.receiveJson(hello());h.start();h.record();
  const job=h.stop();await tick();h.session.close();gate.resolve({audio:{...DOWNLINK},packets:[packet]});
  assert.equal((await job).status,'turn_cancelled');await h.session.drain();assert.equal(h.audio.length,0);
  assert.equal(h.session.state,'closed');
});
test('new listen start invalidates the previous generation',async()=>{
  const gate=deferred(),h=harness({recognize:()=>gate.promise});h.session.receiveJson(hello());h.start();h.record();
  const job=h.stop(),generation=h.session.generation;h.start();
  gate.resolve({raw_utterance:'旧话',clean_utterance:'旧话',provider:'fixed-test'});
  assert.equal((await job).status,'turn_cancelled');assert.equal(h.session.generation,generation+1);
  assert.equal(h.session.state,'listening');
});
test('bad transcript, unsupported expression and mismatched reply are reported',async()=>{
  for(const kind of ['transcript','expression','binding']){
    const h=harness();
    if(kind==='transcript')h.session.recognize=async()=>({raw_utterance:4,clean_utterance:'a',provider:'test'});
    else {const original=h.client.ingestFinal;h.client.ingestFinal=async input=>{
      const result=await original(input);
      if(kind==='expression')result.turn.output_plan[0].expression='surprised';
      else result.turn.input_event.device_id='other';return result;
    };}
    h.session.receiveJson(hello());h.start();h.record();
    assert.equal((await h.stop()).status,{transcript:'invalid_transcript',expression:'unsupported_expression',binding:'reply_binding_mismatch'}[kind]);
    assert.equal(h.audio.length,0);
  }
});
test('invalid synthesis is rejected before sending tts.start',async()=>{
  for(const audio of [{audio:{...DOWNLINK,sample_rate:16000},packets:[packet]},
    {audio:DOWNLINK,packets:[]},{audio:DOWNLINK,packets:[packet,Buffer.alloc(4097)]},
    {audio:DOWNLINK,packets:Array(501).fill(packet)},
    {audio:DOWNLINK,packets:Array(65).fill(Buffer.alloc(4096))}]){
    const h=harness({synthesize:async()=>audio});h.session.receiveJson(hello());h.start();h.record();
    assert.notEqual((await h.stop()).status,'sent');assert.ok(!h.messages.some(m=>m.type==='tts'));
  }
});
test('transport failure releases processing state and closes original commands',async()=>{
  const h=harness();h.peer.sendAudio=async()=>false;h.session.receiveJson(hello());h.start();h.record();
  assert.equal((await h.stop()).status,'transport_failed');assert.equal(h.session.state,'ready');
  assert.equal(h.finishes[0].outcome,'transport_failed');
});
test('cleanup failure remains visible in the result and state',async()=>{
  const h=harness();h.client.finish=async()=>{throw new Error('offline');};h.session.receiveJson(hello());h.start();h.record();
  assert.equal((await h.stop()).status,'cleanup_failed');assert.equal(h.session.snapshot().cleanup_error,'offline');
});
test('abort completes stalled recognition, synthesis and send even if the provider ignores signal',async()=>{
  for(const stage of ['recognize','synthesize','sendAudio']){
    const h=harness();
    if(stage==='sendAudio')h.peer.sendAudio=()=>new Promise(()=>{});
    else h.session[stage]=()=>new Promise(()=>{});
    h.session.receiveJson(hello());h.start();h.record();const job=h.stop();await tick();h.control('abort');
    assert.equal((await job).status,'turn_cancelled');await h.session.drain();
    assert.equal(h.session.snapshot().pending_jobs,0);
  }
});
test('stalled providers and audio submissions have independent deadlines',async()=>{
  for(const stage of ['recognize','synthesize','sendAudio']){
    const h=harness({stageTimeoutMs:10});
    if(stage==='sendAudio')h.peer.sendAudio=()=>new Promise(()=>{});
    else h.session[stage]=()=>new Promise(()=>{});
    h.session.receiveJson(hello());h.start();h.record();
    assert.equal((await h.stop()).status,{recognize:'recognition_timeout',synthesize:'synthesis_timeout',sendAudio:'audio_send_timeout'}[stage]);
    assert.equal(h.session.state,'ready');
  }
});
test('two unsettled original requests bound further turns until cleanup completes',async()=>{
  const h=harness(),gate=deferred(),original=h.client.ingestFinal;
  h.client.ingestFinal=async input=>{await gate.promise;return original(input);};
  h.session.receiveJson(hello());h.start();h.record();const first=h.stop();await tick();
  h.start();h.record();const second=h.stop();await tick();assert.equal(h.session.snapshot().pending_jobs,2);
  assert.throws(()=>h.start(),{code:'voice_busy'});
  h.control('abort');gate.resolve();await Promise.all([first,second]);await h.session.drain();
  assert.equal(h.session.snapshot().pending_jobs,0);
});
test('speaking ends before a deferred queue cleanup after transport failure',async()=>{
  const h=harness(),gate=deferred();h.peer.sendAudio=async()=>false;
  h.client.finish=()=>gate.promise;
  h.session.receiveJson(hello());h.start();h.record();const job=h.stop();await tick();
  assert.equal(h.session.state,'settling');
  assert.equal(h.messages.filter(m=>m.type==='tts'&&m.state==='stop').length,1);
  gate.resolve();assert.equal((await job).status,'transport_failed');
});
