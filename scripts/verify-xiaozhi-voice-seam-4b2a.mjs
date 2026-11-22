// Reproducible, isolated protocol/real-client tests. No serial or device writes.
import {spawnSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const inputs=[
  'integrations/client-4b2a/xiaozhi-codec.mjs',
  'integrations/client-4b2a/voice-session.mjs',
  'integrations/client-4b2a/original-client-fixture.mjs',
  'integrations/client-4b2a/voice-session.test.mjs',
  'integrations/client-4b2a/original-client.test.mjs',
  'scripts/verify-xiaozhi-voice-seam-4b2a.mjs',
  'docs/xiaozhi-voice-seam-4b2a.md',
  'integrations/client-4b2a/README.md',
];
const sha=path=>createHash('sha256').update(readFileSync(resolve(root,path))).digest('hex');
const before=Object.fromEntries(inputs.map(path=>[path,sha(path)]));
const run=spawnSync(process.execPath,['--test','--test-reporter=tap',
  ...inputs.filter(path=>path.endsWith('.test.mjs')).map(path=>resolve(root,path))],
  {cwd:root,encoding:'utf8',windowsHide:true,timeout:120000});
const output=(run.stdout??'')+(run.stderr??'');
const raw='tmp/xiaozhi-voice-seam-4b2a-20261117/validation.tap';
mkdirSync(resolve(root,'tmp/xiaozhi-voice-seam-4b2a-20261117'),{recursive:true});
writeFileSync(resolve(root,raw),output);
const count=name=>Number(output.match(new RegExp(`^# ${name} (\\d+)$`,'m'))?.[1]??-1);
const changed=inputs.filter(path=>before[path]!==sha(path));
const passed=run.status===0 && count('tests')>0 && count('pass')===count('tests')
  && count('fail')===0 && count('skipped')===0 && changed.length===0;
const report={
  schema:'deskbot.xiaozhi-voice-seam/v1',date:'2026-11-17',recorded_at:new Date().toISOString(),
  status:passed?'protocol_verified':'failed',stage:'4b2a',
  test_environment:{node:process.version,executable:process.execPath,clock:'current',
    client_baseline:'0d8d4c5daf261446735b049c6f385981ffd96b21+4b1a',
    client_source:'tmp/deskbot-client-capability-4a-20261116',database:'temporary_isolated_sqlite'},
  tests:{total:count('tests'),passed:count('pass'),failed:count('fail'),skipped:count('skipped'),
    unit_file:'integrations/client-4b2a/voice-session.test.mjs',
    original_http_sqlite_file:'integrations/client-4b2a/original-client.test.mjs'},
  verified:['xiaozhi_v1_v2_v3_framing','manual_listen_and_stop','original_voice_ingress_and_world',
    'immutable_turn_timestamp_and_retry','one_saved_reply_for_subtitle_and_synthesis',
    'generation_cancellation_and_old_connection_suppression','exact_vocat_queue_cleanup',
    'packet_count_byte_and_capture_time_budgets','unknown_http_result_recovery_without_replay',
    'lost_outbox_ack_recovery_without_replay','stalled_provider_deadlines_and_pending_job_limit'],
  transport:'in_memory',asr:'fixed_test_transcript',llm:'fixed_test_reply',tts:'fixed_test_bytes',
  ws_endpoint:false,real_asr_tts:false,physical_voice_acceptance:'pending',
  firmware_changed:false,device_configuration_changed:false,live_services_restarted:false,
  completion_scope:'protocol_fixture',playback_completion:'unobserved',
  next:'4b2b: real WebSocket transport, ASR/TTS, Opus and endpoint detection',
  source_sha256:before,changed_during_tests:changed,
  raw_evidence:{[raw]:sha(raw)},review_file:'review.md',review_status:'pending',
};
const destination='artifacts/xiaozhi-voice-seam-4b2a-20261117';
mkdirSync(resolve(root,destination),{recursive:true});
writeFileSync(resolve(root,destination,'report.json'),JSON.stringify(report,null,2)+'\n');
process.stdout.write(JSON.stringify({status:report.status,tests:report.tests,
  report:relative(root,resolve(root,destination,'report.json')).replaceAll('\\','/')} )+'\n');
if(!passed){process.stderr.write(output);process.exitCode=1;}
