import {VoiceSession} from '../client-4b2a/voice-session.mjs';
import {requireThat,decodeAudio,VoiceProtocolError} from '../client-4b2a/xiaozhi-codec.mjs';
import {makeDecoder,rms} from './audio.mjs';

export class SilenceEndpoint {
  constructor({threshold=0.012,minimumVoiceMs=180,silenceMs=900,maximumMs=30000,noSpeechMs=8000}={}){
    this.threshold=threshold;this.minimumVoiceMs=minimumVoiceMs;this.silenceMs=silenceMs;
    this.maximumMs=maximumMs;this.noSpeechMs=noSpeechMs;this.elapsed=0;this.voiced=0;this.silent=0;
  }
  push(level){
    requireThat(Number.isFinite(level)&&level>=0&&level<=1,'invalid_audio_level');
    this.elapsed+=60;
    if(level>=this.threshold){this.voiced+=60;this.silent=0;}else this.silent+=60;
    if(this.elapsed>=this.maximumMs)return 'maximum';
    if(this.voiced>=this.minimumVoiceMs&&this.silent>=this.silenceMs)return 'speech_end';
    if(this.voiced<this.minimumVoiceMs&&this.elapsed>=this.noSpeechMs)return 'no_speech';
    return null;
  }
}

// Auto mode reuses the reviewed manual-turn state machine. Only the endpoint
// detector and receive idle timers sit around it; replies retain its generation.
export class NetworkSession extends VoiceSession {
  constructor(options){
    super(options);this.onOutcome=options.onOutcome??(()=>{});
    this.helloTimeoutMs=options.helloTimeoutMs??10000;
    this.audioIdleMs=options.audioIdleMs??3000;
    this.mode=null;this.decoder=null;this.endpoint=null;this.receiveTimer=null;this.clips=new WeakSet();
    this.receiveTimer=setTimeout(()=>this.protocolFailure('hello_timeout'),this.helloTimeoutMs);
    this.receiveTimer.unref();
  }
  protocolFailure(code){
    this.onOutcome({status:code,completion_scope:'network_protocol',session_id:this.sessionId});
    this.close();this.peer.close?.(1008,code);
  }
  endFailedTurn(code){
    // Firmware leaves Listening on TTS state changes or a closed connection.
    // Closing makes a failed capture observable and permits a fresh listen;
    // a local reset alone would silently discard the continuing microphone.
    this.close();this.peer.close?.(4001,code);
  }
  clearReceive(){
    clearTimeout(this.receiveTimer);this.receiveTimer=null;
    this.decoder?.close();this.decoder=null;this.endpoint=null;
  }
  track(job){
    if(job&&typeof job.then==='function'&&!this.clips.has(job)){
      this.clips.add(job);const turn=this.active;
      job.then(result=>{
        this.onOutcome({...result,session_id:this.sessionId});
        // An older cancelled/settling request must not close a newer turn.
        if(this.current(turn)&&!['sent','turn_cancelled'].includes(result.status))this.endFailedTurn(result.status);
      },
        ()=>this.protocolFailure('voice_job_failed'));
    }
    return job;
  }
  receiveJson(message){
    if(this.state==='awaiting_hello'){
      const result=super.receiveJson(message);clearTimeout(this.receiveTimer);this.receiveTimer=null;return result;
    }
    if(message?.type==='listen'&&message.state==='detect'){
      requireThat(message.session_id===this.sessionId,'stale_session');
      // A wake word opens listening; it is not a second owner conversation.
      return;
    }
    if(message?.type==='listen'&&message.state==='start'){
      requireThat(['manual','auto'].includes(message.mode),'unsupported_listen_mode');
      const mode=message.mode;
      const result=super.receiveJson({...message,mode:'manual'});
      this.clearReceive();this.mode=mode;this.decoder=makeDecoder(16000);
      if(mode==='auto')this.endpoint=new SilenceEndpoint();
      this.armAudioIdle();return result;
    }
    if(message?.type==='listen'&&message.state==='stop'){
      this.clearReceive();return this.track(super.receiveJson(message));
    }
    if(message?.type==='abort')this.clearReceive();
    return super.receiveJson(message);
  }
  armAudioIdle(){
    clearTimeout(this.receiveTimer);
    this.receiveTimer=setTimeout(()=>{
      if(this.state!=='listening')return;
      // Network stalls are distinct from trailing PCM silence. Discard that
      // incomplete capture instead of asking the model about truncated speech.
      this.onOutcome({status:'capture_idle_timeout',session_id:this.sessionId});
      this.endFailedTurn('capture_idle_timeout');
    },this.audioIdleMs);this.receiveTimer.unref();
  }
  receiveAudio(bytes){
    // Firmware may have queued a few last microphone frames before it receives
    // tts.start. Validate those packets, then discard them after endpointing.
    if(this.state!=='listening'){
      requireThat(this.version!==null&&this.state!=='closed','not_listening');
      decodeAudio(bytes,this.version);return;
    }
    try{
      const payload=decodeAudio(bytes,this.version).payload;
      const pcm=this.decoder.decode(payload); // Check real 60 ms Opus before retaining it.
      super.receiveAudio(bytes);this.armAudioIdle();
      const reason=this.endpoint?.push(rms(pcm));
      if(reason){
        this.clearReceive();
        if(reason==='no_speech'){
          this.onOutcome({status:'no_speech',session_id:this.sessionId});this.endFailedTurn('no_speech');return;
        }
        return this.track(super.receiveJson({type:'listen',state:'stop',session_id:this.sessionId}));
      }
    }catch(error){this.clearReceive();this.invalidate();throw error;}
  }
  close(){this.clearReceive();super.close();}
}

function pause(milliseconds,signal){
  signal?.throwIfAborted();
  return new Promise((resolve,reject)=>{
    const abort=()=>{clearTimeout(timer);reject(new VoiceProtocolError('turn_cancelled'));};
    const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},milliseconds);
    signal?.addEventListener('abort',abort,{once:true});
  });
}

export class PacedPeer {
  constructor(socket,{clock=()=>performance.now(),sleep=pause,firstFrameLeadMs=0}={}){
    this.socket=socket;this.clock=clock;this.sleep=sleep;this.nextFrame=0;this.firstFrameLeadMs=firstFrameLeadMs;
  }
  sendJson(value){
    if(value.type==='tts'&&value.state==='start')this.nextFrame=this.clock()+this.firstFrameLeadMs;
    if(value.type==='tts'&&value.state==='stop')this.nextFrame=0;
    if(this.socket.readyState!==1)return false;
    if(this.socket.bufferedAmount>128*1024)return false;
    this.socket.send(JSON.stringify(value));return true;
  }
  async sendAudio(bytes,{signal}={}){
    signal?.throwIfAborted();
    const wait=this.nextFrame-this.clock();if(wait>0)await this.sleep(wait,signal);
    signal?.throwIfAborted();requireThat(this.socket.readyState===1,'transport_closed');
    requireThat(this.socket.bufferedAmount<=128*1024,'transport_backpressure');
    await new Promise((resolve,reject)=>{
      const abort=()=>reject(new VoiceProtocolError('turn_cancelled'));
      signal?.addEventListener('abort',abort,{once:true});
      this.socket.send(bytes,{binary:true},error=>{
        signal?.removeEventListener('abort',abort);error?reject(new VoiceProtocolError('transport_failed')):resolve();
      });
    });
    signal?.throwIfAborted();
    // Wait after the final submitted packet too. tts.stop follows its nominal
    // duration; a device playback-complete receipt is still a separate fact.
    this.nextFrame=Math.max(this.nextFrame,this.clock())+60;
    await this.sleep(Math.max(0,this.nextFrame-this.clock()),signal);
    return true;
  }
  close(code,reason){this.socket.close(code,reason);}
}
