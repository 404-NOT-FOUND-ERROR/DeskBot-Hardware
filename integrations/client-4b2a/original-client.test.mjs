import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {OriginalClientFixture} from './original-client-fixture.mjs';
import {VoiceSession} from './voice-session.mjs';
import {UPLINK,DOWNLINK} from './xiaozhi-codec.mjs';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const clientRoot=resolve(root,'tmp/deskbot-client-capability-4a-20261116');
assert.equal(execFileSync('git',['-C',clientRoot,'rev-parse','HEAD'],{encoding:'utf8',windowsHide:true}).trim(),
  '0d8d4c5daf261446735b049c6f385981ffd96b21');
const reviewed=JSON.parse(readFileSync(resolve(root,'artifacts/client-expression-loop-4b1a-20261116/report.json'),'utf8'));
for(const [name,expected] of Object.entries(reviewed.source_sha256))
  assert.equal(createHash('sha256').update(readFileSync(resolve(clientRoot,name))).digest('hex'),expected,name);
const load=name=>import(pathToFileURL(resolve(clientRoot,'apps/deskbot-service/src',name+'.mjs')));
const {createDeskBotServer}=await load('app'),{createSqlitePersistence}=await load('persistence'),
  {bindRuntimeStore}=await load('runtime-profile');
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

async function fixture(action,{complete}={}){
  // Exact mkdtemp child only; this owns every file removed below.
  const directory=mkdtempSync(join(tmpdir(),'deskbot-4b2a-'));
  const persistence=createSqlitePersistence({filename:join(directory,'fixture.sqlite')});
  bindRuntimeStore(persistence,{scope:'lab'});
  let calls=0;
  const server=createDeskBotServer({persistence,websocket:false,timeMode:'simulation',
    llm:{async complete(input){calls++;if(complete)await complete(input);
      return {text:'我很开心，想试做一道菜。',provider:'4b2a-fixed-test',model:'fixture'};}}});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const url=`http://127.0.0.1:${server.address().port}`,client=new OriginalClientFixture(url);
  try {await action({url,client,persistence,calls:()=>calls});}
  finally {await new Promise(resolve=>server.close(resolve));persistence.close();rmSync(directory,{recursive:true,force:true});}
}
function sessionFor(client){
  const messages=[],audio=[],synthesized=[];
  const session=new VoiceSession({binding:{character_id:'shaping-001',device_id:'fixture-head-4b2a',role_revision:'fixture-revision'},client,
    recognize:async()=>({raw_utterance:' 今天很开心 ',clean_utterance:'今天很开心',provider:'4b2a-fixed-test'}),
    synthesize:async value=>{synthesized.push(value);return {audio:{...DOWNLINK},packets:[Buffer.from([0xf8,0xff,0xfe])]};},
    peer:{sendJson:value=>messages.push(value),sendAudio:value=>audio.push(value)}});
  session.receiveJson({type:'hello',version:1,transport:'websocket',audio_params:{...UPLINK}});
  const control=(type,extra={})=>session.receiveJson({type,session_id:session.sessionId,...extra});
  function start(){control('listen',{state:'start',mode:'manual'});session.receiveAudio(Buffer.from([0xf8,0xff,0xfe]));}
  return {session,messages,audio,synthesized,control,start,stop:()=>control('listen',{state:'stop'})};
}

test('original HTTP/SQLite creates one living-state turn and closes only its fixture vocat commands',async()=>{
  await fixture(async f=>{
    const h=sessionFor(f.client);h.start();const turn=h.session.context(h.session.active),result=await h.stop();
    assert.equal(result.status,'sent');assert.equal(f.calls(),1);
    const world=await (await fetch(f.url+'/api/world/state')).json();
    assert.equal(world.world.interaction.user_turn_count,1);
    const inputs=f.persistence.list('input.events').map(row=>row.event);
    assert.ok(inputs.some(e=>e.type==='voice.asr.final'&&e.payload.raw_utterance===' 今天很开心 '));
    const repeated=await f.client.ingestFinal({turn,signal:new AbortController().signal,
      transcript:{raw_utterance:' 今天很开心 ',clean_utterance:'今天很开心',provider:'4b2a-fixed-test'}});
    assert.equal(repeated.duplicate,true);assert.equal(f.calls(),1);
    assert.equal(h.synthesized[0].text,repeated.turn.reply);
    assert.equal(h.messages.find(m=>m.state==='sentence_start').text,repeated.turn.reply);
    const commands=f.persistence.list('output.commands'),vocat=commands.filter(c=>c.target==='vocat');
    assert.equal(vocat.length,2);
    assert.ok(vocat.every(c=>c.status==='failed'&&c.acknowledgment.error.code==='protocol_fixture'
      &&c.acknowledgment.payload.playback_completion==='unobserved'));
    assert.ok(commands.filter(c=>c.target!=='vocat').every(c=>c.status==='queued'));
    await f.client.finish(repeated,turn,'sent'); // Lost HTTP ACK can be retried without a new turn.
    assert.equal(f.calls(),1);assert.equal(h.audio.length,1);
  });
});
test('abort during original model call preserves input/history and prevents old wire output',async()=>{
  const gate=deferred(),entered=deferred();
  await fixture(async f=>{
    const h=sessionFor(f.client);h.start();const job=h.stop();await entered.promise;
    h.control('abort');h.start();const next=h.stop();await tick();assert.equal(f.calls(),1);
    gate.resolve();
    assert.equal((await job).status,'turn_cancelled');assert.equal((await next).status,'sent');assert.equal(f.calls(),2);
    assert.equal(h.audio.length,1);
    const world=await (await fetch(f.url+'/api/world/state')).json();assert.equal(world.world.interaction.user_turn_count,2);
    const commands=f.persistence.list('output.commands').filter(c=>c.target==='vocat');
    assert.equal(commands.filter(c=>c.acknowledgment?.error.code==='turn_cancelled').length,2);
    assert.equal(commands.filter(c=>c.acknowledgment?.error.code==='protocol_fixture').length,2);
  },{complete:async()=>{entered.resolve();await gate.promise;}});
});
test('queued cancellation does not send another final transcript to original HTTP',async()=>{
  const gate=deferred(),entered=deferred();
  await fixture(async f=>{
    const h=sessionFor(f.client);h.start();const first=h.stop();await entered.promise;
    h.control('abort');h.start();const second=h.stop();await tick();h.control('abort');gate.resolve();
    assert.equal((await first).status,'turn_cancelled');assert.equal((await second).status,'turn_cancelled');
    assert.equal(f.calls(),1);assert.equal(h.audio.length,0);
  },{complete:async()=>{entered.resolve();await gate.promise;}});
});
test('fixture ACK checks immutable binding and never touches another device',async()=>{
  await fixture(async f=>{
    const turn={character_id:'shaping-001',device_id:'fixture-head-4b2a',role_revision:'fixture-revision',
      session_id:'session-fixture',generation:1,utterance_id:'fixture-retry-4b2a',correlation_id:'corr-fixture-4b2a',
      occurred_at:new Date().toISOString()};
    const result=await f.client.ingestFinal({turn,signal:new AbortController().signal,
      transcript:{raw_utterance:'今天很开心',clean_utterance:'今天很开心',provider:'fixed-test'}});
    await assert.rejects(f.client.finish(result,{...turn,device_id:'other'},'sent'),{code:'command_binding_mismatch'});
    assert.ok(f.persistence.list('output.commands').every(c=>c.status==='queued'));
    await f.client.finish(result,turn,'sent');
  });
});
test('lost original HTTP result pauses new turns and recovers the same event without playback',async()=>{
  await fixture(async f=>{
    let drop=true;
    const client=new OriginalClientFixture(f.url,{fetchImpl:async(url,options)=>{
      const response=await fetch(url,options);
      if(String(url).endsWith('/api/voice/asr')&&drop){drop=false;await response.arrayBuffer();throw new Error('response_lost');}
      return response;
    }});
    const h=sessionFor(client);h.start();assert.equal((await h.stop()).status,'client_delivery_unknown');
    assert.equal(client.recoveryState().status,'client_delivery_unknown');assert.equal(f.calls(),1);
    h.start();assert.equal((await h.stop()).status,'client_recovery_required');assert.equal(f.calls(),1);
    const recovered=await client.recoverUnknown();assert.equal(recovered.status,'recovered_no_replay');
    assert.equal(f.calls(),1);assert.equal(h.audio.length,0);assert.equal(client.recoveryState().status,'ready');
    assert.ok(f.persistence.list('output.commands').filter(c=>c.target==='vocat')
      .every(c=>c.status==='failed'&&c.acknowledgment.error.code==='delivery_unknown'));
    h.start();assert.equal((await h.stop()).status,'sent');assert.equal(f.calls(),2);
    const world=await (await fetch(f.url+'/api/world/state')).json();assert.equal(world.world.interaction.user_turn_count,2);
  });
});
test('HTTP timeout retains the immutable original event for recovery',async()=>{
  const entered=deferred(),gate=deferred();
  await fixture(async f=>{
    const client=new OriginalClientFixture(f.url,{timeoutMs:30}),h=sessionFor(client);
    h.start();const started=h.session.context(h.session.active),job=h.stop();await entered.promise;
    assert.equal((await job).status,'client_delivery_unknown');assert.deepEqual(client.recoveryState().turn,started);
    gate.resolve();client.timeoutMs=60000;
    assert.equal((await client.recoverUnknown()).status,'recovered_no_replay');assert.equal(f.calls(),1);
    assert.equal(h.audio.length,0);
  },{complete:async()=>{entered.resolve();await gate.promise;}});
});
test('lost queue ACK retains exact cleanup result and retries without another model or audio',async()=>{
  await fixture(async f=>{
    let drop=true;
    const client=new OriginalClientFixture(f.url,{fetchImpl:async(url,options)=>{
      const response=await fetch(url,options);
      if(String(url).endsWith('/ack')&&drop){drop=false;await response.arrayBuffer();throw new Error('ack_lost');}
      return response;
    }}),h=sessionFor(client);
    h.start();assert.equal((await h.stop()).status,'cleanup_failed');
    assert.equal(client.recoveryState().status,'client_cleanup_required');
    h.start();assert.equal((await h.stop()).status,'client_cleanup_required');assert.equal(f.calls(),1);
    assert.equal((await client.retryCleanup()).status,'cleanup_recovered_no_replay');
    assert.equal(h.audio.length,1);assert.equal(f.calls(),1);assert.equal(client.recoveryState().status,'ready');
    assert.ok(f.persistence.list('output.commands').filter(c=>c.target==='vocat').every(c=>c.status==='failed'));
  });
});
