'use strict';
// Production workbench and HTTP service; serial ownership is replaced by memory.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/studio-play-e3f4b/browser');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+ 'b'.repeat(32)+'-1';
let browser,server,origin;const checks=[];
const complete=()=>({status:'complete',target_id:device,boot_id:boot,session_id:session,manifest:structuredClone(manifest),received_entries:36,total:36,reason:'verified'});
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:50,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:['neutral','eat']},catalog:complete(),receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
const body=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-08T04:00:00.000Z',hardware_accessed:false,device_written:false,connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-08T04:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-08T04:00:00.000Z',age_ms:50,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function fixture(value){const response=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(response.status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){
  server=spawn(process.env.DESKBOT_TEST_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe',[path.join(root,'tests/serve_studio_play_fixture.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  origin=await new Promise((resolve,reject)=>{let log='';const limit=setTimeout(()=>reject(Error(log)),15000);server.stderr.on('data',s=>log+=s);server.stdout.on('data',s=>{log+=s;const match=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(limit);resolve(match[0]);}});server.once('error',reject);server.once('exit',code=>{clearTimeout(limit);reject(Error(code+' '+log));});});
}
async function exercise(width){
  let live=ready();live.catalog={status:'idle',reason:'not_requested'};
  await fixture({live,body:body()});const countBefore=(await requests()).length;
  const context=await browser.newContext({viewport:{width,height:1050},reducedMotion:width===320?'reduce':'no-preference'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(origin+'/#connection');const button=page.locator('[data-e3c-action="eat"]');
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.status==='complete');
  await page.waitForTimeout(1600);
  let calls=(await requests()).slice(countBefore);
  assert.equal(calls.filter(r=>r.operation==='read_catalog').length,1);
  assert.equal(calls.filter(r=>r.operation==='eat').length,0);checks.push('one_automatic_read_no_expression_'+width);
  assert.equal(await button.isEnabled(),true);assert.match(await page.locator('#e3c-eat-binding').textContent(),/eat\.eaf/);
  assert.match(await page.locator('#e3d2-expressions').textContent(),/进食/);checks.push('correct_binding_and_capability_label_'+width);

  live=ready();live.display=null;live.can_submit=false;live.age_ms=3500;live.reason='state_stale';live.status='waiting';
  await fixture({live});await page.waitForTimeout(1300);
  assert.equal(await button.isEnabled(),true);assert.match(await page.locator('#e3c-age').textContent(),/上次查询/);
  assert.equal(await page.locator('#e3c-app').textContent(),'待机');checks.push('last_query_does_not_flicker_or_block_click_'+width);
  const before=(await requests()).length;
  await button.click();await page.waitForTimeout(900);assert.equal((await requests()).length,before);
  await page.evaluate(()=>document.querySelector('[data-e3c-action="eat"]').click());
  await fixture({live:ready()});await page.waitForFunction(()=>DeskBotStudio.control().device?.receipts?.some(r=>r.status==='dispatched'));
  calls=(await requests()).slice(before);assert.equal(calls.length,1);assert.equal(calls[0].operation,'eat');
  assert.match(await page.locator('#e3c-outcome').textContent(),/已提交播放器/);checks.push('slow_query_click_sends_once_and_correlates_receipt_'+width);

  const cases=[
    ['busy',s=>{s.display.application.value='speaking';s.can_submit=false;}],
    ['menu',s=>{s.display.page.value='other';s.can_submit=false;}],
    ['boot',s=>{s.boot_id='boot-'+'c'.repeat(32);}],
    ['session',s=>{s.session_id='session-other';}],
  ];
  for(const [name,change] of cases){
    await fixture({live:ready()});await page.waitForTimeout(1100);
    await fixture({live:{...ready(),can_submit:false,age_ms:3500,display:null}});await page.waitForTimeout(1100);
    await button.click();const startCount=(await requests()).length;
    const changed=ready();change(changed);await fixture({live:changed});
    await page.waitForFunction(()=>!document.querySelector('[data-e3c-action="cancel"]')||document.querySelector('[data-e3c-action="cancel"]').style.display==='none');
    assert.equal((await requests()).length,startCount);checks.push(name+'_cancels_without_post_'+width);
  }
  await fixture({live:ready()});await page.waitForTimeout(1100);
  await fixture({live:{...ready(),can_submit:false,age_ms:3500,display:null}});await page.waitForTimeout(1100);
  await button.click();const canceledCount=(await requests()).length;
  await page.locator('[data-e3c-action="cancel"]').click();await fixture({live:ready()});await page.waitForTimeout(1100);
  assert.equal((await requests()).length,canceledCount);checks.push('explicit_cancel_zero_post_'+width);

  await fixture({live:{...ready(),can_submit:false,age_ms:3500,display:null}});await page.waitForTimeout(1100);
  await button.click();const navigationCount=(await requests()).length;
  await page.evaluate(()=>{location.hash='#library';});await page.waitForTimeout(300);
  await fixture({live:ready()});await page.waitForTimeout(1100);
  assert.equal((await requests()).length,navigationCount);checks.push('navigation_cancels_before_send_'+width);
  await page.evaluate(()=>{location.hash='#connection';});await page.waitForTimeout(1100);
  await fixture({live:ready()});await page.waitForTimeout(1000);
  let posted;const entered=new Promise(resolve=>posted=resolve),release={};
  const hold=new Promise(resolve=>release.done=resolve);
  await page.route('**/api/studio/command',async route=>{
    if(route.request().postDataJSON().operation==='eat'){posted();await hold;}
    await route.continue();
  });
  await button.click();await entered;
  assert.equal(await page.locator('[data-e3c-action="cancel"]').isVisible(),false);
  assert.match(await page.locator('#e3c-outcome').textContent(),/正在发送/);
  release.done();await page.waitForTimeout(1000);await page.unroute('**/api/studio/command');
  checks.push('send_phase_has_no_misleading_cancel_'+width);

  // A new session gets its own read-only directory request, exactly once.
  live=ready();live.session_id='session-new';live.catalog={status:'idle',reason:'not_requested'};
  await fixture({live});const rebootCount=(await requests()).length;
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.status==='complete'&&DeskBotStudio.control().device?.session_id==='session-new');
  await page.waitForTimeout(1000);assert.equal((await requests()).slice(rebootCount).filter(r=>r.operation==='read_catalog').length,1);
  checks.push('new_session_reads_current_directory_once_'+width);
  await page.screenshot({path:path.join(out,'connection-'+width+'.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);checks.push('responsive_no_errors_'+width);await context.close();
}
async function legacy(){
  const live=ready();live.catalog={status:'idle',reason:'not_requested'};delete live.display.expression_semantics;
  await fixture({live,body:body()});const before=(await requests()).length;
  const context=await browser.newContext(),page=await context.newPage();
  await page.route(origin+'/',async route=>{
    const response=await route.fetch();const html=(await response.text()).replace('automaticDirectory:true','automaticDirectory:false');
    await route.fulfill({response,body:html});
  });
  await page.goto(origin+'/#connection');await page.waitForFunction(()=>window.DeskBotStudio?.control().device?.display);
  await page.waitForTimeout(1800);assert.equal((await requests()).length,before);
  assert.equal(await page.locator('[data-e3c-action="neutral"]').isEnabled(),true);
  checks.push('legacy_page_no_automatic_catalog');await context.close();
}
(async()=>{fs.mkdirSync(out,{recursive:true});try{await start();const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';browser=await chromium.launch({headless:true,...(fs.existsSync(edge)?{executablePath:edge}:{})});await exercise(1440);await exercise(390);await exercise(320);await legacy();const report={result:'passed',checks,checks_passed:checks.length,production_http:true,bridge:'memory',hardware_accessed:false,device_written:false};const source='scripts/deskbot-studio-e3c.js';report.source_sha256=Object.fromEntries([source,'scripts/deskbot-studio-play-readiness.js','tests/studio-play-browser.cjs','tests/serve_studio_play_fixture.py'].map(name=>[name,require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex')]));fs.writeFileSync(path.join(out,'validation-2.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(error=>{console.error(error);process.exitCode=1;});
