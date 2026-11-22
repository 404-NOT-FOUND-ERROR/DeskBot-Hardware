import {spawnSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const {values}=parseArgs({options:{'client-root':{type:'string'},output:{type:'string',default:'artifacts/client-role-adoption-20261118'}}});
if(!values['client-root'])throw Error('explicit_patched_client_root_required');
const client=resolve(values['client-root']),output=resolve(values.output);
const manifest=JSON.parse(readFileSync(resolve(root,'integrations/client-role-adoption/manifest.json'),'utf8'));
const sha=path=>createHash('sha256').update(readFileSync(path)).digest('hex');
for(const [file,hash] of Object.entries(manifest.after))
  if(!existsSync(resolve(client,file))||sha(resolve(client,file))!==hash)throw Error('client_source_mismatch:'+file);
for(const [file,hash] of Object.entries(manifest.required))
  if(!existsSync(resolve(client,file))||sha(resolve(client,file))!==hash)throw Error('client_dependency_mismatch:'+file);
mkdirSync(output,{recursive:true});
const suiteDefinitions=[
  {name:'client_and_recovery',cwd:resolve(client,'apps/deskbot-service'),files:[
    'test/installed-presentation.test.mjs','test/chat-orchestrator.test.mjs','test/voice-pipeline.test.mjs',
    'test/voice-sidecar-client.test.mjs','test/multisource-prompt.test.mjs','recovery-4b1a.test.mjs']},
  {name:'installation_and_adoption',cwd:root,files:['tests/client-installation.test.mjs','tests/client-role-adoption.test.mjs',
    'tests/client-role-adoption-apply.test.mjs']},
  {name:'voice_session',cwd:root,files:['integrations/client-4b2a/voice-session.test.mjs','integrations/client-4b2a/original-client.test.mjs']},
];
const sourceFiles=['integrations/client-role-adoption/apply.mjs','integrations/client-role-adoption/manifest.json',
  'integrations/client-role-adoption/client-role-adoption.patch','integrations/client-role-adoption/installed-presentation.mjs',
  'integrations/client-role-adoption/installed-presentation.test.mjs','integrations/client-installation/receiver.mjs',
  'integrations/client-installation/server.mjs','integrations/client-4b2a/voice-session.mjs',
  'integrations/client-4b2a/original-client-fixture.mjs','integrations/client-4b2b/windows-voice.mjs',
  'integrations/client-4b2b/windows-speech.ps1','tests/client-role-adoption.test.mjs','tests/client-installation.test.mjs',
  'integrations/client-4b2a/voice-session.test.mjs','scripts/verify-client-role-adoption.mjs',
  'tests/client-role-adoption-apply.test.mjs','docs/client-role-adoption.md','docs/project-mainline.md',
  'docs/architecture.md','docs/development-plan-20261104.md','docs/development-pipeline-20261102.md',
  'integrations/client-role-adoption/README.md'];
const source_sha256=Object.fromEntries(sourceFiles.map(file=>[file,sha(resolve(root,file))]));
const suites=[];
for(const suite of suiteDefinitions){
  const result=spawnSync(process.execPath,['--test','--test-reporter=tap',...suite.files],{
    cwd:suite.cwd,env:{...process.env,DESKBOT_HANDOFF_CLIENT:client},encoding:'utf8',windowsHide:true,timeout:60000,maxBuffer:4*1024*1024});
  const log=(result.stdout??'')+(result.stderr??'');writeFileSync(resolve(output,suite.name+'.tap'),log);
  const count=name=>Number(log.match(new RegExp('^# '+name+' (\\d+)$','m'))?.[1]??-1);
  const row={name:suite.name,files:suite.files,tests:count('tests'),passed:count('pass'),failed:count('fail'),
    skipped:count('skipped'),cancelled:count('cancelled'),exit_code:result.status};
  row.status=row.exit_code===0&&row.tests>0&&row.tests===row.passed&&row.failed===0&&row.skipped===0&&row.cancelled===0?'passed':'failed';
  suites.push(row);process.stdout.write(JSON.stringify(row)+'\n');
  if(row.status==='failed')process.stderr.write(log);
}
const client_source_sha256=Object.fromEntries(Object.keys(manifest.after).map(file=>[file,sha(resolve(client,file))]));
const report={schema:'deskbot.client-role-adoption-verification.v1',recorded_at:new Date().toISOString(),
  status:suites.every(row=>row.status==='passed')?'offline_verified':'failed',
  tests:suites.reduce((sum,row)=>sum+row.tests,0),passed:suites.reduce((sum,row)=>sum+row.passed,0),suites,
  environment:{node:process.version,executable:process.execPath,client_root:client,client_base:manifest.base},
  source_sha256,client_source_sha256,hardware_accessed:false,running_services_changed:false,
  actual_synthesis:'Windows System.Speech / Microsoft Huihui Desktop',model:'fixed_test_replies',
  next_step:'original_launcher_composition_and_owner_acceptance'};
if(Object.entries(source_sha256).some(([file,hash])=>sha(resolve(root,file))!==hash)
  ||Object.entries(manifest.after).some(([file,hash])=>client_source_sha256[file]!==hash))throw Error('sources_changed_during_verification');
writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2)+'\n');
if(report.status!=='offline_verified')process.exitCode=1;
