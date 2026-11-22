'use strict';
// Production workbench and HTTP service; serial ownership is replaced by memory.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/studio-eat-e3f4a/browser');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+ 'b'.repeat(32)+'-1';
let browser,server,origin;const checks=[];
const complete=()=>({status:'complete',target_id:device,boot_id:boot,session_id:session,manifest:structuredClone(manifest),received_entries:36,total:36,reason:'verified'});
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:50,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:['neutral','eat']},catalog:complete(),receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
const body=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-08T04:00:00.000Z',hardware_accessed:false,device_written:false,connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-08T04:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-08T04:00:00.000Z',age_ms:50,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function fixture(value){const response=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(response.status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){
  server=spawn(process.env.DESKBOT_TEST_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe',[path.join(root,'tests/serve_studio_eat_fixture.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  origin=await new Promise((resolve,reject)=>{let log='';const limit=setTimeout(()=>reject(Error(log)),15000);server.stderr.on('data',s=>log+=s);server.stdout.on('data',s=>{log+=s;const match=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(limit);resolve(match[0]);}});server.once('error',reject);server.once('exit',code=>{clearTimeout(limit);reject(Error(code+' '+log));});});
}
async function exercise(width){
  let live=ready();await fixture({live,body:body()});
  const context=await browser.newContext({viewport:{width,height:1050},reducedMotion:width===320?'reduce':'no-preference'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(origin+'/#connection');
  const button=page.locator('[data-e3c-action="eat"]');
  await page.waitForFunction(()=>DeskBotStudio.control().device?.display?.expression_semantics?.includes('eat'));
  await button.scrollIntoViewIfNeeded();await page.waitForFunction(()=>!document.querySelector('[data-e3c-action="eat"]').disabled);
  assert.match(await page.locator('#e3c-eat-binding').textContent(),/eat\.eaf/);
  assert.match(await button.locator('..').textContent(),/单次播放/);checks.push('current_binding_and_action_'+width);
  const before=(await requests()).length;await button.click();
  await page.waitForTimeout(300);assert.equal((await requests()).length,before+1);assert.equal((await requests()).at(-1).operation,'eat');checks.push('one_explicit_eat_command_'+width);
  live=ready();delete live.display.expression_semantics;await fixture({live});
  await page.waitForFunction(()=>!DeskBotStudio.control().device?.display?.expression_semantics);await page.waitForTimeout(300);
  assert.equal(await button.isDisabled(),true);assert.equal(await page.locator('[data-e3c-action="neutral"]').isDisabled(),false);checks.push('legacy_neutral_only_'+width);
  live=ready();live.catalog={status:'reading'};await fixture({live});await page.waitForTimeout(1300);
  assert.equal(await button.isDisabled(),true);assert.doesNotMatch(await page.locator('#e3c-eat-binding').textContent(),/eat\.eaf|eat/);checks.push('partial_directory_not_current_'+width);
  live=ready();live.catalog.session_id='session-other';await fixture({live});await page.waitForTimeout(1300);
  assert.equal(await button.isDisabled(),true);assert.doesNotMatch(await page.locator('#e3c-eat-binding').textContent(),/eat\.eaf/);checks.push('other_session_directory_hidden_'+width);
  live=ready();const eating=live.catalog.manifest.entries.find(r=>r.kind==='expression'&&r.id==='eat');eating.resource_id='eat.eaf';eating.fps=25;
  await fixture({live});await page.waitForTimeout(1300);assert.match(await page.locator('#e3c-eat-binding').textContent(),/eat\.eaf.*25 fps/);checks.push('installed_name_updates_'+width);
  await fixture({live:ready()});await page.waitForTimeout(1300);
  const panel=page.locator('.e3e-planner');await panel.scrollIntoViewIfNeeded();await panel.locator('#e3e-source').selectOption('device');await panel.locator('#e3e-semantic').selectOption('eat');await panel.locator('[data-e3e-analyze]').click();
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='ready');assert.match(await panel.locator('[data-e3e-send]').textContent(),/进食/);checks.push('role_analysis_eat_'+width);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);checks.push('responsive_no_errors_'+width);
  await page.screenshot({path:path.join(out,'connection-'+width+'.png'),fullPage:true});await context.close();
}
(async()=>{fs.mkdirSync(out,{recursive:true});try{await start();const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';browser=await chromium.launch({headless:true,...(fs.existsSync(edge)?{executablePath:edge}:{})});await exercise(1440);await exercise(390);await exercise(320);const report={result:'passed',checks,checks_passed:checks.length,production_http:true,bridge:'memory',hardware_accessed:false,device_written:false};fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(error=>{console.error(error);process.exitCode=1;});
