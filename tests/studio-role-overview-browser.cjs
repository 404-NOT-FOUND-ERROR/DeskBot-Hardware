'use strict';
// Production page, HTTP and planner; all USB state is an injected memory object.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/studio-role-overview-e3f2/browser');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
let browser,server,origin;const checks=[];
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:50,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:['neutral','embarrassed']},catalog:{status:'complete',target_id:device,boot_id:boot,session_id:session,manifest:structuredClone(manifest),received_entries:36,total:36,reason:'verified'},receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
async function fixture(value){const response=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(response.status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){
  server=spawn(process.env.DESKBOT_OFFLINE_PYTHON||'python',['-u',path.join(root,'tests/serve_studio_e3f2_fixture.py'),...(process.env.DESKBOT_PUBLIC_BROWSER?['--public']:[])],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  origin=await new Promise((resolve,reject)=>{let log='';const limit=setTimeout(()=>reject(Error(log)),15000);server.stderr.on('data',s=>log+=s);server.stdout.on('data',s=>{log+=s;const match=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(limit);resolve(match[0]);}});server.once('error',reject);server.once('exit',code=>{clearTimeout(limit);reject(Error(code+' '+log));});});
}
async function exercise(width){
  let live=ready();await fixture({live,body:{hardware_accessed:false},fail_resolve:false});
  const context=await browser.newContext({viewport:{width,height:1050},reducedMotion:width===320?'reduce':'no-preference'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(origin+'/#connection');
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.status==='complete');
  const panel=page.locator('.e3f2-overview'),refresh=panel.locator('[data-e3f2-refresh]');
  await panel.scrollIntoViewIfNeeded();assert.equal(await panel.locator('.e3f2-row').count(),0);
  await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report?.total===29);
  assert.equal(await panel.locator('.e3f2-row').count(),29);
  assert.match(await panel.locator('[data-semantic="listen"]').textContent(),/随语音状态播放/);
  assert.equal((await requests()).length,0);checks.push('snapshot_29_and_voice_state_'+width);
  await panel.locator('[data-e3f2-filter="attention"]').click();assert.equal(await panel.locator('.e3f2-row').count(),0);
  await panel.locator('[data-e3f2-filter="all"]').click();
  await panel.locator('[data-e3f2-edit="embarrassed"]').click();
  await page.waitForFunction(()=>DeskBotStudio.state().view==='editor');
  await page.locator('[data-action="pick-resource"]').click();
  await page.locator('#studio-dialog [data-action="choose-resource"][data-resource="shy_candidate.eaf"]').click();
  await page.waitForFunction(()=>DeskBotStudio.snapshot().roles.find(r=>r.id===DeskBotStudio.snapshot().selected_role_id).bindings.embarrassed==='shy_candidate.eaf');
  await page.locator('#main-nav [data-view="connection"]').click();
  await panel.scrollIntoViewIfNeeded();assert.equal(await panel.locator('.e3f2-row').count(),0);
  await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report?.counts.needs_publish===1);
  const shy=panel.locator('[data-semantic="embarrassed"]');
  assert.match(await shy.textContent(),/shy_candidate\.eaf/);assert.match(await shy.textContent(),/Happy\.eaf/);
  assert.match(await shy.textContent(),/25 fps/);assert.match(await shy.textContent(),/蹭蹭/);
  await panel.locator('[data-e3f2-filter="attention"]').click();assert.equal(await panel.locator('.e3f2-row').count(),1);
  checks.push('rewire_preview_and_filter_'+width);
  await panel.locator('[data-e3f2-inspect="embarrassed"]').click();
  assert.equal(await page.locator('#e3e-semantic').inputValue(),'embarrassed');
  assert.equal(await page.locator('#e3e-source').inputValue(),'saved_snapshot');
  await page.locator('[data-e3e-analyze]').click();await page.waitForFunction(()=>DeskBotStudio.roleExpression().result?.status==='needs_publish');
  assert.equal(await page.locator('.e3e-previews canvas').count(),2);
  checks.push('existing_comparison_navigation_'+width);
  await panel.scrollIntoViewIfNeeded();await panel.locator('#e3f2-source').selectOption('device');
  await panel.locator('[data-e3f2-filter="all"]').click();
  await page.waitForFunction(()=>DeskBotStudio.control().device?.catalog?.status==='complete');
  await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report?.source==='device');
  assert.equal(await panel.locator('[data-status="ready"]').count(),1);
  assert.equal(await panel.locator('[data-status="interface_pending"]').count(),26);
  assert.equal((await requests()).length,0);checks.push('live_capability_distinct_from_resource_'+width);
  await page.waitForFunction(()=>!document.getElementById('toast').classList.contains('show'));
  await panel.screenshot({path:path.join(out,'overview-'+width+'.png')});
  for(const change of ['busy','age','capability','reading','reconnect']){
    live=ready();
    if(change==='busy')live.can_submit=false;
    if(change==='age')live.age_ms=2000;
    if(change==='capability')live.display.expression_semantics=['neutral'];
    if(change==='reading')live.catalog.status='reading';
    if(change==='reconnect')live.connection_id='connection-next';
    await fixture({live});
    await page.waitForFunction(()=>DeskBotStudio.roleOverview().report===null);
    assert.equal(await panel.locator('.e3f2-row').count(),0);
    checks.push('invalidate_'+change+'_'+width);
    live=ready();await fixture({live});await page.waitForFunction(()=>DeskBotStudio.control().device?.connection_id==='connection-test'&&DeskBotStudio.control().device?.can_submit&&DeskBotStudio.control().device?.age_ms===50&&DeskBotStudio.control().device?.display?.expression_semantics?.length===2&&DeskBotStudio.control().device?.catalog?.status==='complete');
    await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report!==null);
  }
  // Delay an old response, switch source while it is in flight, then deliver it.
  let release,entered;
  const waiting=new Promise(resolve=>entered=resolve),released=new Promise(resolve=>release=resolve);
  await page.route('**/api/role-expression/overview',async route=>{const response=await route.fetch();entered();await released;await route.fulfill({response});});
  await refresh.click();await waiting;
  await panel.locator('#e3f2-source').selectOption('saved_snapshot');release();
  await page.waitForFunction(()=>!DeskBotStudio.roleOverview().busy);
  assert.equal(await page.evaluate(()=>DeskBotStudio.roleOverview().report),null);
  await page.unroute('**/api/role-expression/overview');checks.push('late_response_discarded_'+width);
  await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report!==null);
  await page.locator('#main-nav [data-view="roles"]').click();
  await page.locator('[data-action="select-role"][data-role="frog-chef"]').click();
  await page.locator('#main-nav [data-view="connection"]').click();
  await panel.scrollIntoViewIfNeeded();assert.equal(await panel.locator('.e3f2-row').count(),0);
  await refresh.click();await page.waitForFunction(()=>DeskBotStudio.roleOverview().report?.role_id==='frog-chef');
  checks.push('selected_role_reanalyzed_'+width);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  if(width===320)assert.equal(await panel.locator('.e3f2-row').first().evaluate(e=>getComputedStyle(e).animationName),'none');
  assert.deepEqual(errors,[]);assert.equal((await requests()).length,0);checks.push('responsive_reduced_motion_no_commands_'+width);
  await context.close();
}
(async()=>{fs.mkdirSync(out,{recursive:true});try{await start();const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',executablePath=process.env.DESKBOT_BROWSER_EXE||(process.platform==='win32'&&fs.existsSync(edge)?edge:undefined);browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});await exercise(1440);await exercise(390);await exercise(320);const report={result:'passed',checks,checks_passed:checks.length,production_http:true,bridge:'memory',hardware_accessed:false,device_written:false};fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(error=>{console.error(error);process.exitCode=1;});
