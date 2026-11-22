// Real audio, actual loopback transport and original-client regressions.
// Each suite owns its database and ports; the accepted device stays in place.
import {spawnSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync,copyFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const destination='artifacts/xiaozhi-real-voice-4b2b-20261117';
const rawDirectory='tmp/xiaozhi-real-voice-4b2b-20261117';
const sha=path=>createHash('sha256').update(readFileSync(resolve(root,path))).digest('hex');
const json=path=>JSON.parse(readFileSync(resolve(root,path),'utf8'));
const inputs=[
  'integrations/client-4b2b/runtime.mjs','integrations/client-4b2b/audio.mjs',
  'integrations/client-4b2b/windows-speech.ps1','integrations/client-4b2b/windows-voice.mjs',
  'integrations/client-4b2b/network-session.mjs','integrations/client-4b2b/websocket-gateway.mjs',
  'integrations/client-4b2b/lab-client.mjs','integrations/client-4b2b/audio-network.test.mjs',
  'integrations/client-4b2b/real-voice.test.mjs','integrations/client-4b2b/launcher.test.mjs',
  'integrations/client-4b2b/dependencies.lock.json','integrations/client-4b2b/README.md',
  'scripts/prepare-voice-runtime-4b2b.py','scripts/serve-real-voice-lab-4b2b.mjs',
  'scripts/verify-real-voice-4b2b.mjs','docs/xiaozhi-real-voice-4b2b.md','THIRD_PARTY.md',
  'docs/project-mainline.md','docs/architecture.md','docs/development-plan-20261104.md',
  'docs/development-pipeline-20261102.md','docs/joint-capability-assessment-4a.md',
];
const before=Object.fromEntries(inputs.map(path=>[path,sha(path)]));
const previous=json('artifacts/xiaozhi-voice-seam-4b2a-20261117/report.json');
const inherited={...previous.source_sha256,...previous.client_source_sha256};
for(const [path,expected] of Object.entries(inherited)){
  if(sha(path)!==expected)throw new Error(`previous_baseline_changed:${path}`);
}
const clientFiles=previous.original_client_regression.report;
const originalTests=json(clientFiles).files;
const suites=[
  {name:'real_audio_network_launcher',expected:25,files:[
    'integrations/client-4b2b/audio-network.test.mjs','integrations/client-4b2b/real-voice.test.mjs',
    'integrations/client-4b2b/launcher.test.mjs']},
  {name:'previous_voice_seam',expected:36,files:['integrations/client-4b2a/voice-session.test.mjs',
    'integrations/client-4b2a/original-client.test.mjs']},
  {name:'original_client_regression',expected:19,files:originalTests},
];
mkdirSync(resolve(root,destination),{recursive:true});mkdirSync(resolve(root,rawDirectory),{recursive:true});
const rawEvidence={};
for(const suite of suites){
  const result=spawnSync(process.execPath,['--test','--test-reporter=tap',...suite.files.map(path=>resolve(root,path))],
    {cwd:root,encoding:'utf8',windowsHide:true,timeout:120000,maxBuffer:4*1024*1024});
  const output=(result.stdout??'')+(result.stderr??'');
  const filename=`${rawDirectory}/${suite.name}.tap`;writeFileSync(resolve(root,filename),output);rawEvidence[filename]=sha(filename);
  const count=name=>Number(output.match(new RegExp(`^# ${name} (\\d+)$`,'m'))?.[1]??-1);
  suite.exit_code=result.status;suite.tests=count('tests');suite.passed=count('pass');suite.failed=count('fail');
  suite.skipped=count('skipped');suite.cancelled=count('cancelled');
  suite.status=result.status===0&&suite.tests===suite.expected&&suite.passed===suite.expected
    &&suite.failed===0&&suite.skipped===0&&suite.cancelled===0?'passed':'failed';
  process.stdout.write(JSON.stringify({suite:suite.name,status:suite.status,passed:suite.passed,total:suite.tests})+'\n');
  if(suite.status!=='passed')process.stderr.write(output);
}
const changed=inputs.filter(path=>before[path]!==sha(path));
const inheritedChanged=Object.entries(inherited).filter(([path,expected])=>sha(path)!==expected).map(([path])=>path);
const passed=suites.every(s=>s.status==='passed')&&changed.length===0&&inheritedChanged.length===0;
// Only a wholly successful run promotes its small factual summaries. Failed
// runs retain their TAP rather than presenting earlier audio as fresh evidence.
const summaries={};
if(passed){
  for(const name of ['real-voice.json','sentence-pause.json','launcher.json']){
    copyFileSync(resolve(root,rawDirectory,name),resolve(root,destination,name));
    summaries[`${destination}/${name}`]=sha(`${destination}/${name}`);
  }
  for(const name of ['input.wav','reply.wav'])rawEvidence[`${rawDirectory}/${name}`]=sha(`${rawDirectory}/${name}`);
}
const audio=passed?json(`${destination}/real-voice.json`):null;
const pause=passed?json(`${destination}/sentence-pause.json`):null;
const report={
  schema:'deskbot.xiaozhi-real-voice/v1',date:'2026-11-17',recorded_at:new Date().toISOString(),stage:'4b2b',
  status:passed?'computer_real_audio_verified':'failed',
  tests:{total:suites.reduce((sum,s)=>sum+s.tests,0),passed:suites.reduce((sum,s)=>sum+s.passed,0),suites},
  environment:{node:process.version,executable:process.execPath,platform:process.platform,
    powershell:'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
    client_baseline:'0d8d4c5daf261446735b049c6f385981ffd96b21+4b1a',
    database:'temporary_isolated_sqlite',network:'127.0.0.1_ephemeral_ports'},
  transport:'actual_loopback_websocket',audio_codec:'libopus_wasm',sample_rate_hz:{uplink:16000,downlink:24000},
  frame_duration_ms:60,asr:'windows-system-speech-general-zh-CN-dictation',tts:'Microsoft Huihui Desktop',
  llm:'fixed_reply',input_source:'tts_generated_recording',live_microphone:false,
  observed_transcript:audio?.transcript.clean_utterance??null,short_pause_transcript:pause?.clean_utterance??null,
  quality_observation:'short-pause capture retained both segments; general dictation heard 我 as 果',
  measured_ms:audio?.measured_ms??null,measurement_scope:'computer_generated_recording_sent_as_burst_to_loopback',
  voice_calls:audio?.voice_calls??null,model_calls:audio?.model_calls??null,
  verification_scope:'computer_network_audio_submission',physical_voice_acceptance:'pending',
  device_migrated:false,device_configuration_changed:false,firmware_changed:false,serial_accessed:false,
  established_services_restarted:false,playback_completion:'unobserved',
  recovery:'in_memory_original_request_recovery; persistent_restart_recovery_next',
  verified:['real_chinese_dictation','real_chinese_synthesis','opus_encode_decode_16k_24k',
    'actual_websocket_v1_v2_v3','one_original_http_sqlite_turn','same_reply_subtitle_and_synthesis',
    'auto_silence_endpoint','failed_turn_closure_and_reconnect','stale_result_suppression',
    'paced_cancellable_audio','isolated_launcher_graceful_shutdown'],
  source_sha256:before,previous_source_sha256:inherited,
  changed_during_tests:changed,previous_changed_during_tests:inheritedChanged,
  raw_evidence:rawEvidence,summary_sha256:summaries,review_file:'review.md',review_status:'pending',
  next:'owner binding, persistent recovery, device-network migration, real microphone/speaker acceptance, always-on service',
};
writeFileSync(resolve(root,destination,'report.json'),JSON.stringify(report,null,2)+'\n');
process.stdout.write(JSON.stringify({status:report.status,passed:report.tests.passed,total:report.tests.total,
  report:`${destination}/report.json`})+'\n');
if(!passed)process.exitCode=1;
