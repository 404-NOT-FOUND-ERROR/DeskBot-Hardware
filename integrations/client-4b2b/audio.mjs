import {OpusScript} from './runtime.mjs';
import {requireThat} from '../client-4b2a/xiaozhi-codec.mjs';

export function wavFromPcm(pcm,rate) {
  requireThat(Buffer.isBuffer(pcm) && pcm.length%2===0 && pcm.length<=1920000,'invalid_pcm');
  requireThat([16000,24000].includes(rate),'unsupported_sample_rate');
  const header=Buffer.alloc(44);
  header.write('RIFF',0);header.writeUInt32LE(36+pcm.length,4);header.write('WAVEfmt ',8);
  header.writeUInt32LE(16,16);header.writeUInt16LE(1,20);header.writeUInt16LE(1,22);
  header.writeUInt32LE(rate,24);header.writeUInt32LE(rate*2,28);header.writeUInt16LE(2,32);
  header.writeUInt16LE(16,34);header.write('data',36);header.writeUInt32LE(pcm.length,40);
  return Buffer.concat([header,pcm]);
}
export function pcmFromWav(wav,rate) {
  requireThat(Buffer.isBuffer(wav)&&wav.length>=44&&wav.length<=1924096
    &&wav.toString('ascii',0,4)==='RIFF'&&wav.toString('ascii',8,12)==='WAVE'
    &&wav.readUInt32LE(4)+8===wav.length,'invalid_wav');
  let format=false,data=null,offset=12;
  while(offset+8<=wav.length){
    const type=wav.toString('ascii',offset,offset+4),size=wav.readUInt32LE(offset+4),start=offset+8;
    requireThat(start+size<=wav.length,'invalid_wav_chunk');
    if(type==='fmt '){
      requireThat(!format&&size>=16&&wav.readUInt16LE(start)===1&&wav.readUInt16LE(start+2)===1
        &&wav.readUInt32LE(start+4)===rate&&wav.readUInt16LE(start+14)===16
        &&wav.readUInt16LE(start+12)===2&&wav.readUInt32LE(start+8)===rate*2,'unsupported_wav');
      format=true;
    }
    if(type==='data'){requireThat(data===null&&size%2===0,'invalid_wav_data');data=Buffer.from(wav.subarray(start,start+size));}
    offset=start+size+(size%2);
  }
  requireThat(format&&data!==null&&offset===wav.length,'invalid_wav');
  return data;
}
export function rms(pcm) {
  let sum=0;for(let i=0;i<pcm.length;i+=2){const value=pcm.readInt16LE(i)/32768;sum+=value*value;}
  return pcm.length ? Math.sqrt(sum/(pcm.length/2)) : 0;
}
export function makeDecoder(rate) {
  requireThat([16000,24000].includes(rate),'unsupported_sample_rate');
  const opus=new OpusScript(rate,1,OpusScript.Application.VOIP);
  return {decode(payload){
    requireThat(Buffer.isBuffer(payload)&&payload.length>0&&payload.length<=OpusScript.MAX_PACKET_SIZE,'invalid_opus');
    const pcm=opus.decode(payload);
    requireThat(pcm.length===rate*0.06*2,'unsupported_packet_duration');
    return Buffer.from(pcm);
  },close(){opus.delete();}};
}
export function decodePackets(packets,rate) {
  requireThat(Array.isArray(packets)&&packets.length>0&&packets.length<=500,'invalid_packet_count');
  const decoder=makeDecoder(rate);
  try{return Buffer.concat(packets.map(packet=>decoder.decode(packet.payload??packet)));}
  finally{decoder.close();}
}
export function encodePcm(pcm,rate) {
  requireThat(Buffer.isBuffer(pcm)&&pcm.length>0&&pcm.length%2===0&&pcm.length<=rate*2*30,'invalid_pcm');
  const frameSamples=rate*0.06,frameBytes=frameSamples*2,packets=[];
  const encoder=new OpusScript(rate,1,OpusScript.Application.VOIP);
  try{
    encoder.encoderCTL(4002,24000); // OPUS_SET_BITRATE; fixed mobile speech bandwidth.
    for(let offset=0;offset<pcm.length;offset+=frameBytes){
      const frame=Buffer.alloc(frameBytes);pcm.copy(frame,0,offset,Math.min(offset+frameBytes,pcm.length));
      packets.push(Buffer.from(encoder.encode(frame,frameSamples)));
    }
    return packets;
  }finally{encoder.delete();}
}
