import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {once} from 'node:events';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {ROOT} from './runtime.mjs';

test('standalone launcher starts, reports its real scope and closes its own services',{timeout:20000},async()=>{
  const evidence=resolve(ROOT,'tmp/xiaozhi-real-voice-4b2b-20261117');await mkdir(evidence,{recursive:true});
  const directory=await mkdtemp(join(evidence,'launcher-'));
  const reservation=createServer();await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));
  const port=reservation.address().port;await new Promise(resolve=>reservation.close(resolve));
  const child=spawn(process.execPath,['scripts/serve-real-voice-lab-4b2b.mjs','--port',String(port),'--state-dir',directory],
    {cwd:ROOT,windowsHide:true,stdio:['ignore','pipe','pipe','ipc']});
  const exit=once(child,'exit');let stdout='',stderr='',ready;
  let timer;
  try{
    await new Promise((resolve,reject)=>{
      timer=setTimeout(()=>reject(new Error('launcher_start_timeout')),10000);
      child.once('error',reject);child.once('exit',code=>reject(new Error(`launcher_early_exit:${code}`)));
      child.stdout.on('data',chunk=>{
        stdout+=chunk.toString();if(stdout.length>65536){reject(new Error('launcher_stdout_limit'));return;}
        for(const line of stdout.split('\n')){
          try{const value=JSON.parse(line);if(value.status==='ready'){ready=value;resolve();}}catch{}
        }
      });
      child.stderr.on('data',chunk=>{stderr+=chunk.toString();if(stderr.length>65536)reject(new Error('launcher_stderr_limit'));});
    });clearTimeout(timer);
    assert.equal(ready.device_migrated,false);assert.equal(ready.model,'fixed_reply');
    const health=await (await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(3000)})).json();
    assert.equal(health.status,'ok');assert.equal(health.real_audio,true);assert.equal(health.device_migrated,false);
    assert.equal(health.device_connected,false);
    const config=JSON.parse(await readFile(ready.connection_file,'utf8'));assert.ok(config.token.length>=24);
    assert.ok(!stdout.includes(config.token));
    child.send({type:'shutdown'});
    const result=await Promise.race([exit,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('launcher_stop_timeout')),5000);})]);
    clearTimeout(timer);assert.equal(result[0],0,stderr);
    await assert.rejects(fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(1000)}));
    await writeFile(resolve(evidence,'launcher.json'),JSON.stringify({status:'passed',recorded_at:new Date().toISOString(),
      health,model:ready.model,credentials:'local_generated_token_not_archived',graceful_exit:result[0],
      port_released:true,device_accessed:false},null,2)+'\n');
  }finally{
    clearTimeout(timer);if(child.exitCode===null&&child.signalCode===null){child.kill();await exit;}
    await rm(directory,{recursive:true,force:true});
  }
});
