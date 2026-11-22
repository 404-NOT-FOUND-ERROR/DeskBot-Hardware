'use strict';
// Real page + production HTTP/planner; USB is replaced by a memory bridge.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3e-browser-20261108');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
let browser,server,origin;const checks=[];
const complete=()=>({status:'complete',reason:'verified',target_id:device,boot_id:boot,session_id:session,received_entries:manifest.entries.length,total:manifest.entries.length,next_cursor:null,manifest:structuredClone(manifest)});
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-'+'c'.repeat(32),enabled:true,status:'ready',reason:'sleep_unknown',target_id:device,boot_id:boot,session_id:session,age_ms:50,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},sleep:'unknown'},pending:null,receipts:[],local_result:null,last_command:null,frames_sent:2,rejected_frames:0,host_command:null,catalog:complete()});
const observation=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-08T04:00:00.000Z',hardware_accessed:false,device_written:false,connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-08T04:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-08T04:00:00.000Z',age_ms:50,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function fixture(value){const response=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(response.status,200);}
async function recorded(){return (await fetch(origin+'/__fixture/state')).json();}
function seal(value){const sorted=v=>Array.isArray(v)?v.map(sorted):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])])):v;const raw={...value};delete raw.catalog_id;value.catalog_id=crypto.createHash('sha256').update(JSON.stringify(sorted(raw))).digest('hex');}
async function start(){
  server=spawn(process.env.DESKBOT_OFFLINE_PYTHON||'python',['-u',path.join(root,'tests/serve_studio_e3e_fixture.py'),...(process.env.DESKBOT_PUBLIC_BROWSER?['--public']:[])],{cwd:root,stdio:['ignore','pipe','pipe'],windowsHide:true});
  origin=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup: '+text)),15000);server.on('error',reject);server.stderr.on('data',s=>text+=s);server.stdout.on('data',s=>{text+=s;const m=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+' '+text));});});
}
async function exercise(width,reduced=false){
  let live=ready();await fixture({live,body:observation(),fail_resolve:false});
  const before=await recorded(),context=await browser.newContext({viewport:{width,height:1050},reducedMotion:reduced?'reduce':'no-preference'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(origin+'/#connection');
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.status==='complete');
  const panel=page.locator('.e3e-planner'),analyze=panel.locator('[data-e3e-analyze]'),send=panel.locator('[data-e3e-send]');
  await panel.scrollIntoViewIfNeeded();
  assert.equal(await panel.locator('#e3e-source option').count(),2);
  assert.equal(await send.isVisible(),false);
  assert.equal((await recorded()).requests.length,before.requests.length);checks.push('passive_load_'+width);
  await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='planned');
  assert.equal(await send.isVisible(),false);checks.push('snapshot_has_no_command_'+width);
  await panel.locator('#e3e-source').selectOption('device');
  await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='ready');
  assert.equal(await send.isVisible(),true);
  assert.equal(await panel.locator('.e3e-previews canvas').count(),2);
  const commandId=await page.evaluate(()=>DeskBotStudio.roleExpression().result.command.command_id);
  await send.click();await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
  await send.click();await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
  const sent=await recorded();assert.equal(sent.requests.length,before.requests.length+2);assert.equal(sent.submitted.filter(id=>id===commandId).length,1);
  assert.equal(sent.requests.at(-1).command_id,sent.requests.at(-2).command_id);checks.push('explicit_send_revalidated_and_deduplicated_'+width);
  // Change the real service state after the click but before its revalidation.
  await page.route('**/api/role-expression/resolve',async route=>{live.can_submit=false;await fixture({live});await route.continue();});
  await send.click();await page.waitForFunction(()=>!DeskBotStudio.roleExpression().busy&&DeskBotStudio.roleExpression().result===null);
  await page.unroute('**/api/role-expression/resolve');
  assert.equal((await recorded()).requests.length,sent.requests.length);checks.push('send_rechecks_busy_before_command_'+width);
  live.can_submit=true;await fixture({live});await page.waitForFunction(()=>DeskBotStudio.control().device?.can_submit);
  await panel.locator('#e3e-semantic').selectOption('embarrassed');
  await panel.locator('[data-e3e-edit]').click();await page.waitForFunction(()=>DeskBotStudio.state().view==='editor');
  await page.locator('[data-action="pick-resource"]').click();
  await page.locator('#studio-dialog [data-action="choose-resource"][data-resource="shy_candidate.eaf"]').click();
  await page.waitForFunction(()=>DeskBotStudio.snapshot().roles.find(r=>r.id===DeskBotStudio.snapshot().selected_role_id).bindings.embarrassed==='shy_candidate.eaf');
  assert.equal(await page.locator('#node-old').count(),1);
  assert.equal(await page.locator('#node-resource canvas').getAttribute('data-resource'),'shy_candidate.eaf');
  assert.equal(await page.locator('#node-old canvas').getAttribute('data-resource'),'Happy.eaf');
  await page.locator('#main-nav [data-view="connection"]').click();
  await panel.scrollIntoViewIfNeeded();assert.equal(await panel.locator('#e3e-source').inputValue(),'device');
  assert.equal(await panel.locator('#e3e-semantic').inputValue(),'embarrassed');
  await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='needs_publish');
  assert.equal(await panel.locator('.e3e-previews canvas').nth(0).getAttribute('data-resource'),'Happy.eaf');
  assert.equal(await panel.locator('.e3e-previews canvas').nth(1).getAttribute('data-resource'),'shy_candidate.eaf');
  assert.equal(await send.isVisible(),false);
  assert.equal((await recorded()).requests.length,sent.requests.length);checks.push('node_rewire_old_new_previews_'+width,'source_and_semantic_persist_'+width);
  await page.waitForFunction(()=>[...document.querySelectorAll('.e3e-chain>div')].every(e=>getComputedStyle(e).opacity==='1'));
  assert.equal(await panel.locator('.e3e-chain>div').count(),4);
  await panel.screenshot({path:path.join(out,`role-route-${width}${reduced?'-reduced':''}.png`)});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);checks.push('responsive_no_overflow_'+width);
  if(reduced){assert.equal(await panel.locator('.e3e-chain>div').first().evaluate(e=>getComputedStyle(e).animationName),'none');checks.push('reduced_motion');}
  // Return to a usable neutral analysis, then replace a live directory in the same session.
  await panel.locator('#e3e-semantic').selectOption('neutral');await analyze.click();
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='ready');
  live.catalog.manifest.catalog_id='f'.repeat(64);await fixture({live});
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().result===null&&DeskBotStudio.roleExpression().error.includes('目录'));
  assert.equal(await send.isVisible(),false);checks.push('same_session_catalog_invalidates_'+width);
  live=ready();await fixture({live});await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.manifest?.catalog_id!== 'f'.repeat(64));
  await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='ready');
  live.connection_id='connection-reconnected';await fixture({live});
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().result===null&&DeskBotStudio.roleExpression().error.includes('重连'));
  assert.equal(await send.isVisible(),false);checks.push('reconnect_invalidates_'+width);
  // Changed live playback/layout/content must never be drawn as the old device animation.
  for(const change of ['fps','loop','layout','content']){
    live=ready();const current=live.catalog.manifest,row=current.entries.find(r=>r.kind==='expression'&&r.id==='neutral');
    if(change==='fps')row.fps+=1;
    if(change==='loop')row.loop=!row.loop;
    if(change==='layout')current.entries.find(r=>r.kind==='layout'&&r.id===row.layout_id).x+=1;
    if(change==='content')for(const item of current.entries)if(item.resource_id===row.resource_id)item.resource_sha256='e'.repeat(64);
    seal(current);await fixture({live});
    await page.waitForFunction(id=>DeskBotStudio.control().device?.catalog?.manifest?.catalog_id===id,current.catalog_id);
    await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='needs_publish');
    assert.equal(await panel.locator('.e3e-previews>div').first().locator('canvas').count(),0);
    assert.match(await panel.locator('.e3e-previews>div').first().textContent(),/需导入预览/);
    assert.equal(await send.isVisible(),false);checks.push('changed_live_'+change+'_withholds_preview_'+width);
  }
  live=ready();live.catalog.manifest.catalog_id='f'.repeat(64);await fixture({live});
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.manifest?.catalog_id==='f'.repeat(64));
  await analyze.click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().error.includes('catalog_digest_changed'));
  assert.equal(await send.isVisible(),false);checks.push('invalid_directory_error_'+width);
  await fixture({live:ready(),fail_resolve:true});await analyze.click();
  await page.waitForFunction(()=>DeskBotStudio.roleExpression().error.includes('本机分析'));
  assert.equal(await send.isVisible(),false);checks.push('service_error_'+width);
  assert.deepEqual(errors,[]);checks.push('no_page_errors_'+width);
  await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  try{
    await start();
    const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',executablePath=process.env.DESKBOT_BROWSER_EXE||(process.platform==='win32'&&fs.existsSync(edge)?edge:undefined);
    browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
    await exercise(1440);await exercise(390);await exercise(320,true);
    const report={edition:'E3e',result:'passed',checks,checks_passed:checks.length,production_http_and_planner:true,bridge:'memory_fixture',hardware_accessed:false,device_written:false};
    fs.writeFileSync(path.join(out,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  }finally{if(browser)await browser.close();if(server)server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
