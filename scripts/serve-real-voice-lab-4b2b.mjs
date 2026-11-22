import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {parseArgs} from 'node:util';
import {resolve} from 'node:path';
import {randomBytes} from 'node:crypto';
import {ROOT} from '../integrations/client-4b2b/runtime.mjs';
import {createLabClient} from '../integrations/client-4b2b/lab-client.mjs';
import {WindowsVoice} from '../integrations/client-4b2b/windows-voice.mjs';
import {createVoiceGateway} from '../integrations/client-4b2b/websocket-gateway.mjs';
import {OriginalClientFixture} from '../integrations/client-4b2a/original-client-fixture.mjs';

const {values}=parseArgs({options:{port:{type:'string',default:'8890'},'state-dir':{type:'string',default:'tmp/real-voice-lab-4b2b'}}});
const port=Number(values.port);if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('invalid_port');
const directory=resolve(ROOT,values['state-dir']);mkdirSync(directory,{recursive:true});
const filename=resolve(directory,'connection.json');
let config;
try{config=JSON.parse(readFileSync(filename,'utf8'));}
catch(error){if(error.code!=='ENOENT')throw error;
  config={token:randomBytes(32).toString('hex'),device_header:'lab-head-4b2b',client_header:'lab-owner-4b2b'};
  writeFileSync(filename,JSON.stringify(config,null,2)+'\n',{flag:'wx'});
}
const lab=await createLabClient({database:resolve(directory,'life.sqlite')});
const client=new OriginalClientFixture(lab.url),voice=new WindowsVoice();
const gateway=createVoiceGateway({binding:{character_id:'shaping-001',device_id:'lab-head-4b2b',role_revision:'local-voice-4b2b'},
  client,voice,token:config.token,deviceHeader:config.device_header,clientHeader:config.client_header,
  onOutcome:result=>process.stdout.write(JSON.stringify({event:'voice_turn',...result})+'\n')});
try{
  await new Promise((resolve,reject)=>{gateway.server.once('error',reject);gateway.server.listen(port,'127.0.0.1',resolve);});
  process.stdout.write(JSON.stringify({status:'ready',url:`ws://127.0.0.1:${port}/xiaozhi/v1`,
    health:`http://127.0.0.1:${port}/health`,connection_file:filename,
    asr_tts:'windows-system-speech',model:'fixed_reply',device_migrated:false})+'\n');
}catch(error){await gateway.close();await lab.close();throw error;}
let closing=false;
async function close(){if(closing)return;closing=true;await gateway.close();await lab.close();if(process.connected)process.disconnect();}
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>void close());
if(process.send)process.on('message',message=>{if(message?.type==='shutdown')void close();});
