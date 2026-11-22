import assert from 'node:assert/strict';
import {test} from 'node:test';
import {once} from 'node:events';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {ROOT,WebSocket} from './runtime.mjs';
import {WindowsVoice} from './windows-voice.mjs';
import {pcmFromWav,wavFromPcm,encodePcm,decodePackets,rms} from './audio.mjs';
import {createLabClient} from './lab-client.mjs';
import {createVoiceGateway} from './websocket-gateway.mjs';
import {OriginalClientFixture} from '../client-4b2a/original-client-fixture.mjs';
import {UPLINK,decodeAudio} from '../client-4b2a/xiaozhi-codec.mjs';

const evidence=resolve(ROOT,'tmp/xiaozhi-real-voice-4b2b-20261117');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const normalized=text=>text.replace(/[\p{P}\p{Z}\s]/gu,'');

test('real Chinese audio crosses WebSocket, original HTTP/SQLite, ASR and TTS',{timeout:60000},async()=>{
  await mkdir(evidence,{recursive:true});
  const directory=await mkdtemp(join(evidence,'store-'));
  const windows=new WindowsVoice(),voiceCalls={asr:0,tts:0},observations=[],messages=[],received=[];
  let transcript=null,synthesisText=null;
  const input=await windows.run('synthesize',{text:'今天我很开心',rate:16000});
  const pcm=pcmFromWav(input.wav,16000),packets=encodePcm(Buffer.concat([pcm,Buffer.alloc(16000*2*1.2)]),16000);
  const client=await createLabClient({database:resolve(directory,'life.sqlite')});
  const voice={
    async recognize(value){voiceCalls.asr++;const result=await windows.recognize(value);transcript=result;return result;},
    async synthesize(value){voiceCalls.tts++;synthesisText=value.text;return windows.synthesize(value);},
  };
  const token=randomBytes(24).toString('hex');
  const gateway=createVoiceGateway({binding:{character_id:'shaping-001',device_id:'real-audio-fixture',role_revision:'4b2b-real-audio'},
    client:new OriginalClientFixture(client.url),voice,token,deviceHeader:'real-audio-head',clientHeader:'real-audio-owner',
    sessionOptions:{stageTimeoutMs:30000},onOutcome:value=>observations.push(value)});
  let socket;
  try{
    await new Promise(resolve=>gateway.server.listen(0,'127.0.0.1',resolve));
    socket=new WebSocket(`ws://127.0.0.1:${gateway.server.address().port}/xiaozhi/v1`,{headers:{
      authorization:`Bearer ${token}`,'device-id':'real-audio-head','client-id':'real-audio-owner','protocol-version':'1'}});
    let startedAt=0,firstAudioAt=0,stoppedAt=0;
    socket.on('message',(bytes,binary)=>{
      if(binary){received.push(decodeAudio(bytes,1).payload);if(!firstAudioAt)firstAudioAt=performance.now();}
      else{const value=JSON.parse(bytes.toString('utf8'));messages.push(value);
        if(value.type==='tts'&&value.state==='stop')stoppedAt=performance.now();}
    });
    await once(socket,'open');socket.send(JSON.stringify({type:'hello',version:1,transport:'websocket',audio_params:UPLINK}));
    for(let i=0;i<200&&!messages.length;i++)await sleep(5);
    assert.equal(messages[0]?.type,'hello');const sessionId=messages[0].session_id;
    startedAt=performance.now();
    socket.send(JSON.stringify({type:'listen',state:'start',mode:'auto',session_id:sessionId}));
    for(const packet of packets)socket.send(packet);
    for(let i=0;i<1200&&(!observations.length||!stoppedAt);i++)await sleep(25);
    assert.equal(observations[0]?.status,'sent');assert.equal(normalized(transcript.clean_utterance),'今天我很开心');
    assert.deepEqual(voiceCalls,{asr:1,tts:1});assert.equal(client.modelCalls(),1);
    assert.equal(messages.find(m=>m.type==='stt').text,transcript.clean_utterance);
    assert.equal(messages.find(m=>m.state==='sentence_start').text,synthesisText);
    const output=decodePackets(received,24000);
    assert.ok(rms(output)>0.005);assert.ok(received.length>10);
    assert.ok(stoppedAt-firstAudioAt>=(received.length-1)*50);
    const world=await (await fetch(client.url+'/api/world/state')).json();assert.equal(world.world.interaction.user_turn_count,1);
    const commands=client.persistence.list('output.commands').filter(c=>c.target==='vocat');
    assert.equal(commands.length,2);assert.ok(commands.every(c=>c.status==='failed'&&c.acknowledgment.error.code==='protocol_fixture'));
    await writeFile(resolve(evidence,'input.wav'),input.wav);
    await writeFile(resolve(evidence,'reply.wav'),wavFromPcm(output,24000));
    await writeFile(resolve(evidence,'real-voice.json'),JSON.stringify({
      status:'passed',recorded_at:new Date().toISOString(),transport:'actual_loopback_websocket',
      input_source:'windows_tts_generated_recording',recognition:'general_chinese_dictation',
      transcript,reply:synthesisText,model_provider:'fixed_reply',voice_calls:voiceCalls,
      incoming_packets:packets.length,outgoing_packets:received.length,
      reply_duration_ms:output.length/2/24000*1000,reply_rms:rms(output),
      measured_ms:{first_audio:Math.round(firstAudioAt-startedAt),all_audio_submitted:Math.round(stoppedAt-startedAt)},
      live_microphone:false,device_migrated:false,device_playback:'pending',model_calls:client.modelCalls(),
      world_user_turn_count:world.world.interaction.user_turn_count,computer_outcome:observations[0],
    },null,2)+'\n');
  }finally{
    socket?.terminate();await gateway.close();await client.close();await rm(directory,{recursive:true,force:true});
  }
});

test('general Chinese dictation retains both sides of a short sentence pause',{timeout:45000},async()=>{
  const voice=new WindowsVoice();
  const first=await voice.run('synthesize',{text:'今天我很开心',rate:16000});
  const second=await voice.run('synthesize',{text:'我想去公园散步',rate:16000});
  const pcm=Buffer.concat([pcmFromWav(first.wav,16000),Buffer.alloc(16000*2*0.4),pcmFromWav(second.wav,16000)]);
  const result=await voice.recognize({packets:encodePcm(pcm,16000),audio:UPLINK});
  const text=normalized(result.clean_utterance);
  assert.ok(text.includes('今天我很开心'),text);assert.ok(text.includes('公园散步'),text);assert.ok(result.segments.length>=2);
  await mkdir(evidence,{recursive:true});await writeFile(resolve(evidence,'sentence-pause.json'),JSON.stringify(result,null,2)+'\n');
});
test('real speech subprocess respects an already cancelled task',async()=>{
  const voice=new WindowsVoice(),controller=new AbortController();controller.abort();
  await assert.rejects(voice.run('synthesize',{text:'不会播放',rate:24000,signal:controller.signal}));
  assert.equal(voice.active,0);
});
