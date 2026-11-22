import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {VoiceSession} from '../integrations/client-4b2a/voice-session.mjs';
import {OriginalClientFixture} from '../integrations/client-4b2a/original-client-fixture.mjs';
import {UPLINK,DOWNLINK} from '../integrations/client-4b2a/xiaozhi-codec.mjs';
import {WindowsVoice} from '../integrations/client-4b2b/windows-voice.mjs';
import {wavFromPcm} from '../integrations/client-4b2b/audio.mjs';

if (!process.env.DESKBOT_HANDOFF_CLIENT) throw new Error('explicit_client_checkout_required');
const root=process.env.DESKBOT_HANDOFF_CLIENT;
const load=name=>import(pathToFileURL(resolve(root,'apps/deskbot-service/src',name+'.mjs')));
const [{createChatOrchestrator},{createInputStore},{createStateEngine},{createWorldContext},
  {createSqlitePersistence},{createOutputRouter},{createInstalledPresentationResolver},{createDeskBotServer}]=
  await Promise.all(['chat-orchestrator','input-store','state-engine','world-context','persistence',
    'output-router','installed-presentation','app'].map(load));
const actor='shaping-001',device='adoption-fixture',now=()=>new Date('2026-11-18T01:00:00Z');
const packet=Buffer.from([0xf8,0xff,0xfe]);
const catalog={'gateway:soft':{target:'windows-system-speech',voice_id:'huihui',voice_name:'Microsoft Huihui Desktop'}};
function installation(id){return {source_revision:id==='a'?1:2,installation:{
  installation_id:id,character_id:actor,device_id:device,shell_id:'shell-'+id,
  binding:{profile_sha256:(id==='a'?'a':'b').repeat(64)},profile:{role_id:'form-'+id,
    expression_role_id:'expression-'+id,profile_revision:1,display_name:'形态'+id,
    persona:id==='a'?'留意溪水的小变化。':'喜欢拆解小物件。',
    voice:{provider:'gateway',voice_id:'soft',speaking_style:'轻快简洁'},
  }}};}

for(const phase of ['prepared','output_enqueued'])test(`reopening ${phase} retains the original shell, answer and actual voice`,async t=>{
  const directory=mkdtempSync(join(tmpdir(),'role-recovery-'));
  t.after(()=>rmSync(directory,{recursive:true,force:true}));
  let current='a',models=0,syntheses=0,resolutions=0;
  const request={event_id:'recover-'+phase,character_id:actor,device_id:device,
    role_revision:'fixture',correlation_id:'recover-'+phase,message:'你留意到了什么？'};
  const life={character_id:actor,memories:['一起生活'],relationship:{owner:5},growth_stage:'existing'};
  function open(checkpoint){
    const persistence=createSqlitePersistence({filename:join(directory,'world.sqlite'),now});
    const outputRouter=createOutputRouter({persistence,now});
    const resolver=createInstalledPresentationResolver({voiceCatalog:catalog,getInstallation:({installation_id})=>{
      resolutions++;return installation(installation_id??current);
    }});
    const chat=createChatOrchestrator({persistence,now,outputRouter,resolveInstalledPresentation:resolver,
      recoveryCheckpoint:checkpoint,inputStore:createInputStore({persistence,now}),
      stateEngine:createStateEngine({persistence,now}),worldContext:createWorldContext({persistence,now}),
      llm:{async complete(){models++;return {text:'我留意到了溪水。',provider:'fixed',trace:{}};}},
      voiceClient:{async synthesize(input){syntheses++;return {request_id:input.request_id,
        correlation_id:input.correlation_id,profile:input.profile,provider:'windows-system-speech',
        voice:'Microsoft Huihui Desktop',audio:{audio_id:'audio-'+phase,format:input.format,
          byte_count:1,data_base64:'AA==',duration_ms:10,sha256:'a'.repeat(64),encoding:'base64'}};}},
      audioArtifacts:{put(value){return {duplicate:false,artifact:value};}},
    });
    return {persistence,outputRouter,chat};
  }
  const first=open(value=>{if(value===phase)throw Error('injected_interruption');});
  first.persistence.put('life.fixture','continuous',life);
  await assert.rejects(first.chat.run(request),/injected_interruption/);
  assert.equal(first.persistence.get('chat.recovery',request.event_id).turn.installed_presentation.installation_id,'a');
  first.persistence.close();current='b';
  const second=open();
  try{
    const turn=await second.chat.run(request);
    assert.equal(turn.installed_presentation.installation_id,'a');
    assert.equal(turn.presentation_adoption.voice.synthesis.status,'applied');
    assert.equal(turn.presentation_adoption.voice.synthesis.actual_voice,'Microsoft Huihui Desktop');
    assert.match(turn.prompt.text,/留意溪水的小变化/);
    assert.doesNotMatch(turn.prompt.text,/喜欢拆解小物件/);
    assert.equal(models,1);assert.equal(syntheses,1);assert.equal(resolutions,1);
    const commands=second.outputRouter.listQueued({device_id:device});
    assert.ok(commands.length>0);
    assert.deepEqual(await second.chat.run(request),{...turn,duplicate:true});
    assert.deepEqual(second.outputRouter.listQueued({device_id:device}).map(row=>row.command_id),commands.map(row=>row.command_id));
    assert.deepEqual(second.persistence.get('life.fixture','continuous'),life);
    const next=await second.chat.run({...request,event_id:'next-'+phase,correlation_id:'next-'+phase});
    assert.equal(next.installed_presentation.installation_id,'b');
  }finally{second.persistence.close();}
});

test('Windows voice produces the requested mapped voice and reports the actual voice',async()=>{
  const voice=new WindowsVoice();
  const result=await voice.synthesize({text:'角色音色本地验证',voice_adoption:{status:'supported',
    requested:{provider:'gateway',voice_id:'soft',speaking_style:'轻快简洁'},
    applied:catalog['gateway:soft']}});
  assert.ok(result.packets.length>0);
  assert.equal(result.voice,'Microsoft Huihui Desktop');
  assert.equal(result.voice_adoption.synthesis.status,'applied');
  assert.equal(result.voice_adoption.synthesis.provider,'windows-system-speech');
  assert.equal(result.voice_adoption.synthesis.actual_voice,'Microsoft Huihui Desktop');
  assert.equal(result.voice_adoption.synthesis.style_status,'unsupported');
});

test('Windows voice reports mismatched target and actual voice separately from mapping support',async()=>{
  const voice=new WindowsVoice(),requests=[];
  voice.run=async(action,input)=>{requests.push(input);return {provider:'windows-system-speech',
    voice:'Microsoft Huihui Desktop',wav:wavFromPcm(Buffer.alloc(2880),24000)};};
  const wrongTarget=await voice.synthesize({text:'测试',voice_adoption:{status:'supported',
    applied:{target:'cloud-tts',voice_id:'cloud-a'}}});
  assert.equal(wrongTarget.voice_adoption.synthesis.reason,'voice_target_mismatch');
  assert.equal(requests[0].voiceName,null);
  const wrongVoice=await voice.synthesize({text:'测试',voice_adoption:{status:'supported',
    applied:{target:'windows-system-speech',voice_id:'Microsoft Zira Desktop'}}});
  assert.equal(wrongVoice.voice_adoption.synthesis.reason,'voice_identity_mismatch');
  assert.equal(wrongVoice.voice_adoption.synthesis.actual_voice,'Microsoft Huihui Desktop');
});

test('voice start, original HTTP and synthesis share the frozen installation including explicit absence',async()=>{
  let current=null,calls=0;
  const resolver=createInstalledPresentationResolver({voiceCatalog:catalog,
    getInstallation:({installation_id})=>installation_id?installation(installation_id):current?installation(current):null});
  const server=createDeskBotServer({websocket:false,timeMode:'simulation',now,
    worldLifeEnabled:false,autonomousLifeEnabled:false,residentLifeEnabled:false,
    resolveInstalledPresentation:resolver,llm:{async complete(){calls++;return {text:'你好，今天开心。',provider:'fixed'};}}});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const client=new OriginalClientFixture(`http://127.0.0.1:${server.address().port}`),synthesized=[];
  const session=new VoiceSession({binding:{character_id:actor,device_id:device,role_revision:'fixture'},
    resolvePresentationAtStart:resolver,client,recognize:async()=>({raw_utterance:'你好',clean_utterance:'你好',provider:'fixed'}),
    synthesize:async input=>{synthesized.push(input);return {audio:DOWNLINK,packets:[packet]};},
    peer:{sendJson(){},sendAudio(){}}});
  try{
    session.receiveJson({type:'hello',version:1,transport:'websocket',audio_params:UPLINK});
    const listen=state=>session.receiveJson({type:'listen',state,mode:'manual',session_id:session.sessionId});
    const round=async(start,end)=>{current=start;listen('start');session.receiveAudio(packet);current=end;
      const result=await listen('stop');assert.equal(result.status,'sent');return synthesized.at(-1);};
    assert.equal((await round(null,'a')).installed_presentation,null);
    assert.equal((await round('a','b')).installed_presentation.installation_id,'a');
    assert.equal((await round('b','a')).installed_presentation.installation_id,'b');
    assert.equal(calls,3);
  }finally{session.close();await session.drain();await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});}
});
