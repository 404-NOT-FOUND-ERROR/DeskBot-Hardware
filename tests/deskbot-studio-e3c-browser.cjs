'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3c6-browser-20261105');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8770',checks=[];
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
let browser;
function body(connected=false){
  return {schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,
    generated_at:'2026-11-05T04:00:00.000Z',hardware_accessed:connected,device_written:false,
    connection:{status:connected?'connected':'disabled',port:connected?'COM4':null,reason:'synthetic_browser_fixture'},
    freshness:connected?'fresh':'disabled',fresh_for_ms:30000,live:connected,timeline_id:'observation-'+'a'.repeat(32),
    device_log_time_ms:10000,last_seen_at:connected?'2026-11-05T04:00:00.000Z':null,last_seen_age_ms:connected?500:null,
    application_state:null,internal_memory:null,events:[],dropped_lines:0,
    identity:{status:connected?'confirmed':'disconnected',source:'device_announcement',received_at:connected?'2026-11-05T04:00:00.000Z':null,
      age_ms:connected?500:null,reason:'synthetic_browser_fixture',rejected_messages:0,
      hello:connected?{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,
        firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},
        capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}:null}};
}
function control(enabled=false,changes={}){
  return {schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-'+'c'.repeat(32),
    enabled,status:enabled?'ready':'off',reason:enabled?'sleep_unknown':'enable_to_query',target_id:enabled?device:null,boot_id:enabled?boot:null,
    session_id:enabled?session:null,age_ms:enabled?500:null,can_submit:enabled,
    display:enabled?{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},sleep:'unknown'}:null,
    pending:null,receipts:[],local_result:null,last_command:null,frames_sent:enabled?2:0,rejected_frames:0,host_command:null,...changes};
}
function receipt(sequence,status,reason){return {target_id:device,boot_id:boot,session_id:session,sequence,status,reason,request_id:'expr-browser',source:'device'};}
async function shot(page,name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function nav(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);}
async function exercise(){
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage();
  const calls=[],errors=[];let observed=body(),live=control(),failure=false;
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/api/body/**',async route=>{
    const request=route.request(),p=new URL(request.url()).pathname;calls.push({p,method:request.method()});
    let data=observed;
    if(p==='/api/body/ports')data={available:true,ports:[{port:'COM4',usb_id:'303A:1001',key:'a'.repeat(64)}]};
    if(p==='/api/body/connect'){observed=body(true);data=observed;}
    if(p==='/api/body/disconnect'){observed=body();live=control();data=observed;}
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.route('**/api/studio/**',async route=>{
    const request=route.request(),p=new URL(request.url()).pathname;calls.push({p,method:request.method()});
    if(failure)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'本机服务暂不可用'})});
    if(request.method()==='POST'){
      assert.equal(request.headers()['x-deskbot-studio'],'e3c6');
      const command=request.postDataJSON();assert.equal(command.connection_id,live.connection_id);
      assert.equal(command.target_id,device);assert.equal(command.boot_id,boot);
      if(command.operation==='enable'){assert.equal(command.session_id,null);live=control(true);}
      else if(command.operation==='disable')live=control(false,{reason:'query_stopped'});
      else {assert.equal(command.operation,'neutral');assert.equal(command.session_id,session);
        live=control(true,{can_submit:false,status:'pending',pending:{request_id:'expr-browser',phase:'waiting',age_ms:50}});}
    }
    await route.fulfill({contentType:'application/json',body:JSON.stringify(live)});
  });
  await page.goto(origin+'/#connection');
  await page.waitForFunction(()=>DeskBotStudio?.control().device);
  assert.equal(calls.filter(r=>r.method==='POST').length,0);
  assert.equal(await page.locator('[data-e3c-action="enable"]').isDisabled(),true);
  assert.equal(await page.locator('[data-e3c-action="neutral"]').isDisabled(),true);
  await page.locator('[data-e3a-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='confirmed');
  await page.locator('[data-e3c-action="enable"]').click();
  await page.waitForFunction(()=>DeskBotStudio.control().device?.can_submit);
  assert.equal(await page.locator('#e3c-app').textContent(),'待机');
  assert.equal(await page.locator('#e3c-page').textContent(),'主页');
  assert.equal(await page.locator('#e3c-player').textContent(),'已就绪');
  await page.locator('[data-e3c-action="neutral"]').click();
  await page.waitForFunction(()=>DeskBotStudio.control().device?.pending?.phase==='waiting');
  assert.equal(await page.locator('[data-e3c-action="neutral"]').isDisabled(),true);
  live.pending.phase='accepted';live.receipts=[receipt(1,'accepted','queued_for_owner')];
  await page.waitForFunction(()=>document.querySelector('#e3c-outcome').textContent==='已受理');
  live.pending=null;live.receipts.push(receipt(2,'dispatched','submitted_to_display'));live.can_submit=true;
  await page.waitForFunction(()=>document.querySelector('#e3c-outcome').textContent==='已提交播放器');
  assert.match(await page.locator('#e3c-outcome-note').textContent(),/已调用原播放器入口/);
  await page.locator('.e3c-history summary').click();
  const play=page.locator('[data-e3c-action="neutral"]');await play.focus();
  await page.waitForTimeout(1800);
  assert.equal(await page.locator('.e3c-history').getAttribute('open'),'');
  assert.equal(await play.evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.locator('.e3c-control').count(),1);
  await shot(page,'neutral-device-receipts');
  checks.push('passive_page_load','explicit_enable','current_identity_binding','state_cards','one_neutral_post',
    'accepted_then_dispatched','device_reason_labels','details_and_focus_survive_poll','single_control_panel');
  for(const [channel,value,reason] of [['application','speaking','application_busy'],['page','other','not_home'],['player','unavailable','player_unavailable']]){
    live=control(true,{can_submit:false,reason});
    if(channel==='player')live.display.player.state=value;else live.display[channel].value=value;
    await page.waitForFunction(r=>DeskBotStudio.control().device?.reason===r,reason);
    assert.equal(await play.isDisabled(),true);
  }
  live=control(true,{can_submit:false,display:null,age_ms:2500,reason:'state_stale'});
  await page.waitForFunction(()=>DeskBotStudio.control().device?.reason==='state_stale');assert.equal(await play.isDisabled(),true);
  live=control(true,{can_submit:false,local_result:{request_id:'expr-browser',outcome:'timeout',reason:'device_receipt_timeout'}});
  await page.waitForFunction(()=>document.querySelector('#e3c-outcome').textContent==='等待回执超时');
  assert.equal(live.receipts.length,0);
  live=control(false,{reason:'session_reply_timeout'});
  await page.waitForFunction(()=>DeskBotStudio.control().device?.reason==='session_reply_timeout');
  assert.match(await page.locator('#e3c-reason').textContent(),/当前固件尚未响应/);
  live=control(true);observed.identity.hello.boot_id='boot-'+'d'.repeat(32);
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.hello.boot_id.endsWith('dddddddd'));
  await page.waitForTimeout(900);assert.equal(await play.isDisabled(),true);
  observed=body();live=control(false,{reason:'connection_lost'});
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='disabled');
  assert.equal(await play.isDisabled(),true);
  observed=body(true);live=control(true);failure=true;
  await page.waitForFunction(()=>DeskBotStudio.control().error);assert.equal(await play.isDisabled(),true);
  failure=false;await page.waitForFunction(()=>!DeskBotStudio.control().error&&DeskBotStudio.control().device?.can_submit);
  checks.push('voice_busy_gate','menu_gate','player_gate','stale_state_gate','host_timeout_separate_from_receipts',
    'older_firmware_hint','new_boot_disables_old_control','disconnect_gate','service_failure_and_recovery');
  await page.locator('[data-e3c-action="disable"]').click();
  await page.waitForFunction(()=>!DeskBotStudio.control().device?.enabled);
  await page.locator('.e3a-simulator>summary').click();
  await page.locator('[data-sim-action="connect"]').click();
  await page.locator('[data-sim-action="send"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().receipts.at(-1)?.status==='dispatched');
  assert.equal(live.receipts.length,0);
  await nav(page,'editor');assert.equal(await page.locator('#graph-world').count(),1);
  await nav(page,'roles');assert.equal(await page.locator('.role-card').count(),3);
  await nav(page,'publish');await page.waitForFunction(()=>DeskBotStudio.release().connection==='ready');
  await page.locator('[data-package-action="generate"]').first().click();
  await page.waitForFunction(()=>DeskBotStudio.release().current&&!DeskBotStudio.release().busy,{},{timeout:30000});
  assert.ok(await page.locator('a[download]').count()>=2);
  await nav(page,'connection');live=control(true,{receipts:[receipt(1,'accepted','queued_for_owner'),receipt(2,'dispatched','submitted_to_display')]});
  await page.waitForFunction(()=>DeskBotStudio.control().device?.enabled);
  for(const width of [320,390,1440]){
    await page.setViewportSize({width,height:1100});await page.waitForTimeout(300);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no overflow '+width);
    await shot(page,'connection-'+width);
  }
  await page.locator('#theme-toggle').click();await shot(page,'connection-dark');
  assert.deepEqual(errors,[]);
  checks.push('explicit_disable','simulator_keeps_device_receipts_separate','editor_roles_regression','real_package_generation',
    '320_390_1440_layout','dark_theme','no_page_errors');
  await context.close();
}
async function offline(){
  const context=await browser.newContext({viewport:{width:390,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
  await page.goto(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e3c6-20261105/index.html')).href+'#connection');
  await page.waitForFunction(()=>DeskBotStudio?.control().error);
  assert.match(await page.locator('#e3c-reason').textContent(),/本机工作台/);
  assert.equal(await page.locator('[data-e3c-action="enable"]').isDisabled(),true);
  assert.equal(await page.locator('.e3c-command .button').first().evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
  checks.push('public_offline_hint','reduced_motion');await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  await exercise();await offline();await browser.close();
  const result={edition:'E3c-6',result:'passed',checks,body_data_source:'synthetic_browser_fixture',hardware_accessed:false,device_written:false};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
