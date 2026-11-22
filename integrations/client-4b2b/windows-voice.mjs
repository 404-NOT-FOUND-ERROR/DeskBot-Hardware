import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {RUNTIME} from './runtime.mjs';
import {wavFromPcm,pcmFromWav,decodePackets,encodePcm} from './audio.mjs';
import {UPLINK,DOWNLINK,checkAudio,requireThat,VoiceProtocolError} from '../client-4b2a/xiaozhi-codec.mjs';

const POWERSHELL='C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe';
const SCRIPT=fileURLToPath(new URL('./windows-speech.ps1',import.meta.url));

export class WindowsVoice {
  constructor({runtime=RUNTIME}={}){this.jobs=resolve(runtime,'speech-jobs');this.active=0;}
  async run(action,{text,wav,rate,voiceName,signal}={}){
    signal?.throwIfAborted();requireThat(this.active<2,'speech_busy');this.active++;
    let directory;
    try{
      await mkdir(this.jobs,{recursive:true});directory=await mkdtemp(join(this.jobs,'job-'));
      const input=join(directory,'input.wav'),output=join(directory,'output.wav'),
        request=join(directory,'request.json'),response=join(directory,'response.json');
      if(wav)await writeFile(input,wav);
      await writeFile(request,JSON.stringify({action,text,sample_rate:rate,voice_name:voiceName??null,input,output}));
      signal?.throwIfAborted();
      await new Promise((resolve,reject)=>{
        const child=spawn(POWERSHELL,['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
          '-File',SCRIPT,'-RequestPath',request,'-ResponsePath',response],
          {windowsHide:true,stdio:['ignore','pipe','pipe']});
        let settled=false,count=0;
        const finish=(error)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
          error?reject(error):resolve();};
        const abort=()=>{child.kill();};
        const timer=setTimeout(()=>child.kill(),40000);
        signal?.addEventListener('abort',abort,{once:true});
        if(signal?.aborted)abort();
        for(const stream of [child.stdout,child.stderr])stream.on('data',value=>{
          count+=value.length;if(count>32768)child.kill();
        });
        child.once('error',()=>finish(new VoiceProtocolError('speech_process_failed')));
        child.once('close',code=>finish(signal?.aborted?new VoiceProtocolError('turn_cancelled')
          :code===0?null:new VoiceProtocolError('speech_process_failed')));
      });
      const result=JSON.parse((await readFile(response,'utf8')).replace(/^\uFEFF/,''));
      return {...result,...(action==='synthesize'?{wav:await readFile(output)}:{})};
    }finally{this.active--;if(directory)await rm(directory,{recursive:true,force:true});}
  }
  async recognize({packets,audio,signal}){
    checkAudio(audio,UPLINK);
    const pcm=decodePackets(packets,16000);
    return this.run('recognize',{wav:wavFromPcm(pcm,16000),signal});
  }
  async synthesize({text,voice_adoption,signal}){
    requireThat(typeof text==='string'&&text.trim()&&text.length<=1000,'speech_text_too_large');
    const applied=voice_adoption?.status==='supported'&&voice_adoption.applied?.target==='windows-system-speech'
      ?voice_adoption.applied:null;
    const voiceName=applied?.voice_name??applied?.voice_id??null;
    const result=await this.run('synthesize',{text,rate:24000,voiceName,signal});
    const selected=voice_adoption?.status==='supported'?voice_adoption.applied:null;
    const expected=selected?.voice_name??selected?.voice_id??null;
    const synthesis={status:!selected?'existing':selected.target!==result.provider?'mismatch'
      :result.voice!==expected?'mismatch':'applied',
      requested_profile:selected,reported_profile:{voice_id:result.voice??null},
      provider:result.provider,actual_voice:result.voice??null,
      reason:!selected?voice_adoption?.reason??null:selected.target!==result.provider?'voice_target_mismatch'
        :result.voice!==expected?'voice_identity_mismatch':null,
      style_status:voice_adoption?.requested?.speaking_style?'unsupported':'not_configured'};
    return {audio:{...DOWNLINK},packets:encodePcm(pcmFromWav(result.wav,24000),24000),provider:result.provider,
      voice:result.voice??null,voice_adoption:{...(voice_adoption??{status:'preserve_existing'}),synthesis}};
  }
}
