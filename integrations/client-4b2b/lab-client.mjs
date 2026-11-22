import {execFileSync} from 'node:child_process';
import {readFileSync,mkdirSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {ROOT} from './runtime.mjs';

export async function createLabClient({database}){
  const clientRoot=resolve(ROOT,'tmp/deskbot-client-capability-4a-20261116');
  const baseline='0d8d4c5daf261446735b049c6f385981ffd96b21';
  if(execFileSync('git',['-C',clientRoot,'rev-parse','HEAD'],{encoding:'utf8',windowsHide:true}).trim()!==baseline)
    throw new Error('client_baseline_mismatch');
  const reviewed=JSON.parse(readFileSync(resolve(ROOT,'artifacts/client-expression-loop-4b1a-20261116/report.json'),'utf8'));
  for(const [file,expected] of Object.entries(reviewed.source_sha256)){
    if(createHash('sha256').update(readFileSync(resolve(clientRoot,file))).digest('hex')!==expected)
      throw new Error('client_patch_mismatch');
  }
  const load=name=>import(pathToFileURL(resolve(clientRoot,'apps/deskbot-service/src',name+'.mjs')));
  const {createDeskBotServer}=await load('app'),{createSqlitePersistence}=await load('persistence'),
    {bindRuntimeStore}=await load('runtime-profile');
  mkdirSync(dirname(database),{recursive:true});
  const persistence=createSqlitePersistence({filename:database});bindRuntimeStore(persistence,{scope:'lab'});
  let calls=0;
  const server=createDeskBotServer({persistence,websocket:false,timeMode:'simulation',
    llm:{async complete(){calls++;return {text:'听到你分享开心，我也很高兴。',provider:'4b2b-fixed-reply',model:'offline'};}}});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  return {server,persistence,url:`http://127.0.0.1:${server.address().port}`,modelCalls:()=>calls,
    async close(){await new Promise(resolve=>server.close(resolve));persistence.close();}};
}
