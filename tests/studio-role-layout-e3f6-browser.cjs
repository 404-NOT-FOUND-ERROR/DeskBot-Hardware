'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/studio-role-layout-e3f6/browser');
const html=fs.readFileSync(path.join(root,'tmp/deskbot-studio-e3f6-20261110/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">(.*?)<\/script>/s)[1]);
const manifest=data.role_reference,device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
const checks=[];let server,browser,origin;
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:50,can_submit:true,
  display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:['neutral','embarrassed','eat']},
  catalog:{status:'complete',reason:'verified',target_id:device,boot_id:boot,session_id:session,manifest:structuredClone(manifest),received_entries:37,total:37},receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
const body=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-10T09:00:00.000Z',hardware_accessed:false,device_written:false,
  connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-10T09:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,
  identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-10T09:00:00.000Z',age_ms:50,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,
    firmware:{edition:'E3f-5-memory',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function fixture(value){const r=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(r.status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){
  server=spawn(process.env.DESKBOT_TEST_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe',[path.join(root,'tests/serve_studio_role_layout_e3f6_fixture.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  origin=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',s=>log+=s);server.stdout.on('data',s=>{log+=s;const m=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);server.once('exit',c=>{clearTimeout(timer);reject(Error(c+' '+log));});});
}
async function settled(page){await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.total===37&&!DeskBotStudio.control().busy&&DeskBotStudio.observation().device?.identity?.status==='confirmed');}
async function selectPlanner(page,semantic,source='device'){
  if(!page.url().endsWith('#connection')){await page.goto(origin+'/#connection');await settled(page);}
  await page.locator('#e3e-source').selectOption(source);await page.locator('#e3e-semantic').selectOption(semantic);
  await page.locator('[data-e3e-analyze]').click();
  await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy&&DeskBotStudio.roleExpression().result!==null);
}
async function editor(page,semantic){
  await page.goto(origin+'/#editor');await settled(page);
  await page.locator(`[data-action="select-semantic"][data-semantic="${semantic}"]`).first().click();
  await page.waitForFunction(s=>DeskBotStudio.state().semantic===s,semantic);
}
async function pixels(page,selector,mode){
  // Compare actual node pixels with the source atlas at frame 0 and declared geometry.
  const actual=await page.evaluate(async({selector,mode})=>{
    const canvas=document.querySelector(selector),D=JSON.parse(document.getElementById('studio-data').textContent),asset=D.assets[canvas.dataset.resource];
    const image=new Image();image.src=asset.atlas;await image.decode();
    const expected=document.createElement('canvas');expected.width=expected.height=360;const c=expected.getContext('2d');
    c.fillStyle='#000';c.fillRect(0,0,360,360);c.save();c.beginPath();c.arc(180,180,180,0,Math.PI*2);c.clip();
    const w=asset.frame_width,h=asset.frame_height,top=Math.floor((360-h)/2)+10;
    if(mode==='eye_anim'){c.drawImage(image,0,0,w,h,10,top,w,h);c.translate(350,0);c.scale(-1,1);c.drawImage(image,0,0,w,h,0,top,w,h);}
    else c.drawImage(image,0,0,w,h,(360-w)/2,(360-h)/2,w,h);
    c.restore();const a=canvas.getContext('2d').getImageData(0,0,360,360).data,b=c.getImageData(0,0,360,360).data;
    return {layout:canvas.dataset.layout,equal:a.every((v,i)=>v===b[i])};
  },{selector,mode});
  assert.equal(actual.layout,mode);assert.equal(actual.equal,true,'node pixels '+mode);
}
async function exercise(width){
  const initialRequests=(await requests()).length;
  await fixture({live:ready(),body:body()});
  const context=await browser.newContext({viewport:{width,height:1050},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  const draft=structuredClone(data.initial);draft.roles[0].bindings.embarrassed='shy_candidate.eaf';
  await page.addInitScript(({draft,key})=>localStorage.setItem(key,JSON.stringify(draft)),{draft,key:'deskbot.studio.e2a.'+data.catalog_id.slice(0,16)});
  await page.goto(origin+'/#connection');await settled(page);
  await page.locator('#e3f2-source').selectOption('saved_snapshot');await page.locator('[data-e3f2-refresh]').click();
  await page.waitForFunction(()=>DeskBotStudio.roleOverview().report?.resource_matches===29);
  assert.match(await page.locator('.e3f2-row[data-semantic="eat"]').textContent(),/完整画面 · 无镜像/);
  checks.push('current_37_saved_overview_'+width);
  await page.locator('[data-e3d-select="eat"]').click();
  await page.waitForTimeout(200);
  await pixels(page,'.e3d-comparison>div:first-child canvas','eye_anim');
  await pixels(page,'.e3d-comparison>div:last-child canvas','full_frame');
  assert.match(await page.locator('.e3d-comparison>div:last-child').textContent(),/布局与历史快照不同/);
  checks.push('historical_directory_keeps_eye_pixels_and_marks_layout_difference_'+width);
  await selectPlanner(page,'eat');
  assert.equal(await page.locator('#e3e-previews canvas').count(),2);
  assert.deepEqual(await page.locator('#e3e-previews canvas').evaluateAll(xs=>xs.map(c=>c.dataset.layout)),['full_frame','full_frame']);
  checks.push('verified_current_and_draft_eat_full_frame_'+width);
  assert.equal((await requests()).length,initialRequests);
  checks.push('overview_and_analysis_emit_no_command_'+width);
  const postedBefore=(await requests()).length;
  for(let i=0;i<2;i++){
    await page.locator('[data-e3e-send]').click();
    await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
    await settled(page);
    assert.match(await page.locator('#e3e-message').textContent(),/已交给设备连接/);
  }
  const posted=(await requests()).slice(postedBefore);assert.equal(posted.length,2);assert.notEqual(posted[0].command_id,posted[1].command_id);
  checks.push('two_clicks_two_distinct_requests_'+width);

  await editor(page,'eat');await page.waitForTimeout(300);
  await pixels(page,'#node-resource canvas','full_frame');
  await page.locator('[data-action="pick-resource"]').click();await page.locator('[data-action="choose-resource"][data-resource="winking.eaf"]').click();
  await pixels(page,'#node-resource canvas','eye_anim');await pixels(page,'#node-old canvas','eye_anim');
  assert.equal(await page.locator('#node-old canvas').getAttribute('data-resource'),'eat.eaf');
  checks.push('replacement_disconnects_historical_node_and_uses_eye_pixels_'+width);
  await page.locator('[data-action="undo"]').click();await pixels(page,'#node-resource canvas','full_frame');
  checks.push('undo_restores_full_frame_pixels_'+width);
  const before=(await requests()).length;
  await page.locator('[data-e3f6-node]').click();await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
  await settled(page);assert.equal((await requests()).length,before+1);assert.equal((await requests()).at(-1).operation,'eat');
  checks.push('node_uses_existing_owner_'+width);
  await editor(page,'embarrassed');await page.waitForTimeout(250);await pixels(page,'#node-resource canvas','eye_anim');
  checks.push('shy_200_height_mirrored_pixels_'+width);
  await page.screenshot({path:path.join(out,'nodes-'+width+'.png'),fullPage:true});

  await selectPlanner(page,'embarrassed');
  const waiting=ready();waiting.can_submit=false;waiting.display=null;waiting.age_ms=3500;waiting.status='waiting';waiting.reason='state_stale';
  await fixture({live:waiting});await page.waitForFunction(()=>DeskBotStudio.control().device?.display===null);
  const b=(await requests()).length;await page.locator('[data-e3e-send]').click();
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().busy);
  await page.waitForTimeout(200);assert.equal((await requests()).length,b);
  await fixture({live:ready()});await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
  assert.equal((await requests()).length,b+1);checks.push('wait_then_fresh_query_one_request_'+width);

  const old=ready();old.display.expression_semantics=['neutral','eat'];await fixture({live:old});
  await page.waitForFunction(()=>DeskBotStudio.control().device?.display?.expression_semantics?.length===2);
  await selectPlanner(page,'embarrassed');assert.equal(await page.locator('[data-e3e-send]').isHidden(),true);
  checks.push('installed_E4e_hides_unavailable_shy_'+width);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
  checks.push('responsive_without_errors_'+width);await context.close();
}
async function cancellation(){
  for(const change of ['semantic','source','page','role','draft','catalog','session','boot','connection','cancel']){
    await fixture({live:ready(),body:body()});
    const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
    if(change==='role'||change==='draft'){
      await page.goto(origin+'/#editor');await settled(page);await page.waitForFunction(()=>DeskBotStudio.state().view==='editor');
      await page.locator('[data-action="select-semantic"][data-semantic="eat"]').first().click();
      await page.locator('[data-action="undo"]').waitFor({state:'visible'});
    } else {await page.goto(origin+'/#connection');await settled(page);await selectPlanner(page,'eat');}
    const waiting=ready();waiting.can_submit=false;waiting.display=null;waiting.age_ms=3500;
    await fixture({live:waiting});await page.waitForFunction(()=>DeskBotStudio.control().device?.display===null);
    const n=(await requests()).length;
    if(change==='role'||change==='draft')await page.locator('[data-e3f6-node]').click();
    else await page.locator('[data-e3e-send]').click();
    await page.waitForFunction(()=>DeskBotStudio.roleExpression().busy);
    if(change==='semantic')await page.locator('#e3e-semantic').selectOption('neutral');
    if(change==='source')await page.locator('#e3e-source').selectOption('saved_snapshot');
    if(change==='page')await page.evaluate(()=>{location.hash='#library';});
    if(change==='role')await page.locator('#role-picker').selectOption(data.initial.roles[1].id);
    else if(change==='draft'){await page.locator('[data-action="pick-resource"]').click();await page.locator('[data-action="choose-resource"][data-resource="winking.eaf"]').click();}
    if(change==='cancel')await page.locator('[data-e3f6-cancel]').click();
    const next=ready();
    if(change==='catalog'){next.catalog.manifest=structuredClone(data.installed_directory.manifest);next.catalog.total=next.catalog.received_entries=36;}
    if(change==='session'){next.session_id='session-'+'c'.repeat(32)+'-1';next.catalog.session_id=next.session_id;}
    if(change==='boot'){next.boot_id='boot-'+'c'.repeat(32);next.catalog.boot_id=next.boot_id;}
    if(change==='connection')next.connection_id='connection-other';
    await fixture({live:next});await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy,null,{timeout:18000});
    assert.equal((await requests()).length,n,'cancel '+change);checks.push('cancel_'+change+'_zero_requests');await context.close();
  }
}
(async()=>{fs.mkdirSync(out,{recursive:true});try{await start();browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});for(const width of [1440,390,320])await exercise(width);await cancellation();
  const report={result:'passed',checks,checks_passed:checks.length,production_http:true,installed_entries:37,bridge:'memory',hardware_accessed:false,device_written:false};fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(e=>{console.error(e);process.exitCode=1;});
