// USB acceptance uses the original client API, current clock and isolated store.
import {parseArgs} from 'node:util';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {readFileSync,mkdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname} from 'node:path';

const {values}=parseArgs({options:{'client-root':{type:'string'},database:{type:'string'},port:{type:'string',default:'4314'}}});
if(!values['client-root']||!values.database)throw new Error('--client-root and --database are required');
const root=resolve(values['client-root']),database=resolve(values.database),port=Number(values.port);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Invalid port');
const head=execFileSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8',windowsHide:true}).trim();
if(head!=='0d8d4c5daf261446735b049c6f385981ffd96b21')throw new Error('Client baseline mismatch');
// HEAD alone does not include the reviewed working-tree integration patch.
const reviewed=JSON.parse(readFileSync(new URL("file:///D:/502%20Bad%20Gateway/Codex%20Project/DeskBot/tmp/github-publish-audit-20261023/artifacts/client-expression-loop-4b1a-20261116/report.json"),'utf8'));
for(const [name,sha] of Object.entries(reviewed.source_sha256)){
  const actual=createHash('sha256').update(readFileSync(resolve(root,name))).digest('hex');
  if(actual!==sha)throw new Error('Reviewed client integration mismatch: '+name);
}
const load=name=>import(pathToFileURL(resolve(root,'apps/deskbot-service/src/'+name+'.mjs')));
const {createDeskBotServer}=await load('app'),{createSqlitePersistence}=await load('persistence'),
  {createFakeLlm}=await load('llm'),{bindRuntimeStore}=await load('runtime-profile');
mkdirSync(dirname(database),{recursive:true});
const persistence=createSqlitePersistence({filename:database});
bindRuntimeStore(persistence,{scope:'lab'});
const server=createDeskBotServer({persistence,llm:createFakeLlm(),timeMode:'simulation',websocket:false});
server.listen(port,'127.0.0.1',()=>process.stdout.write(JSON.stringify({
  schema:'deskbot.client-usb-runtime/v1',url:`http://127.0.0.1:${port}`,mode:'fixed_reply',
  client_baseline:head,database})+'\n'));
let closed=false;const closeStore=()=>{if(!closed){closed=true;persistence.close();}};
server.on('error',error=>{process.stderr.write(error.message+'\n');closeStore();process.exitCode=1;});
server.once('close',closeStore);
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>server.close());
