// Test-only isolated original client server. No credentials or real providers.
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const [checkout,filename]=process.argv.slice(2);
const {createDeskBotServer}=await import(pathToFileURL(resolve(checkout,'apps/deskbot-service/src/app.mjs')));
const {createSqlitePersistence}=await import(pathToFileURL(resolve(checkout,'apps/deskbot-service/src/persistence.mjs')));
const now=()=>new Date('2026-11-16T01:00:00Z');
const persistence=createSqlitePersistence({filename,now});
const load=async name=>(await import(pathToFileURL(resolve(checkout,'apps/deskbot-service/src/'+name+'.mjs'))));
const {createChatOrchestrator}=await load('chat-orchestrator'), {createInputStore}=await load('input-store'),
  {createStateEngine}=await load('state-engine'),{createWorldContext}=await load('world-context'),
  {createOutputRouter}=await load('output-router'),{createPersistentWorld}=await load('persistent-world');
const components={inputStore:createInputStore({persistence,now}),stateEngine:createStateEngine({persistence,now}),
  worldContext:createWorldContext({persistence,now}),outputRouter:createOutputRouter({persistence,now}),
  persistentWorld:createPersistentWorld({persistence,now})};
const llm={async complete(){const calls=(persistence.get('fixture','model-calls')?.calls??0)+1;
  persistence.put('fixture','model-calls',{calls});return {
  text:'我很开心，想再试做一道菜。',provider:'4b1a-fixed',model:'offline',trace:{calls}};}};
const chatOrchestrator=createChatOrchestrator({...components,persistence,now,llm,
  recoveryCheckpoint:phase=>{if(process.env.DESKBOT_4B1A_CRASH===phase)process.exit(73);}});
const server=createDeskBotServer({...components,persistence,now,llm,chatOrchestrator});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({url:`http://127.0.0.1:${server.address().port}`})+'\n'));
process.stdin.resume();
process.stdin.on('data',()=>server.close(()=>{persistence.close();process.exit(0);}));
