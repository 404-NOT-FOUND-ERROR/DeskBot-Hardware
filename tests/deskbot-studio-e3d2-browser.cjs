'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3d2-browser-20261105');
let origin=process.env.DESKBOT_STUDIO_URL,server;
const checks=[];
const manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
let browser;
async function startServer(){
  if(origin)return;
  server=spawn(process.env.DESKBOT_OFFLINE_PYTHON||'python',['-u',path.join(root,'scripts/serve-deskbot-studio-e3d2.py'),'--public','--port','0'],{cwd:root,stdio:['ignore','pipe','pipe'],windowsHide:true});
  origin=await new Promise((resolve,reject)=>{
    let output='';const timeout=setTimeout(()=>reject(new Error('Offline preview server startup timed out: '+output)),15000);
    const finish=(error,value)=>{clearTimeout(timeout);error?reject(error):resolve(value);};
    server.on('error',error=>finish(error));
    server.on('exit',code=>finish(new Error('Offline preview server exited '+code+': '+output)));
    server.stderr.on('data',chunk=>{output+=chunk;});
    server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)finish(null,match[0]);});
  });
}
const empty=(status='idle',reason='not_requested')=>({status,reason,target_id:null,boot_id:null,session_id:null,received_entries:0,total:null,next_cursor:null,manifest:null});
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-'+'c'.repeat(32),enabled:true,status:'ready',reason:'sleep_unknown',target_id:device,boot_id:boot,session_id:session,age_ms:50,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},sleep:'unknown'},pending:null,receipts:[],local_result:null,last_command:null,frames_sent:2,rejected_frames:0,host_command:null,catalog:empty()});
const observation=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-05T04:00:00.000Z',hardware_accessed:true,device_written:false,connection:{status:'connected',port:'COM4',reason:'offline_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-05T04:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-05T04:00:00.000Z',age_ms:50,reason:'offline_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function exercise(width=1440,reduced=false){
  const context=await browser.newContext({viewport:{width,height:1100},reducedMotion:reduced?'reduce':'no-preference'}),page=await context.newPage();
  let live=ready(),body=observation(),failure=false;const errors=[],posts=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/api/**',async route=>{
    const req=route.request(),p=new URL(req.url()).pathname;
    let data={};
    if(p==='/api/health')data={ready:true,device_written:false};
    else if(p==='/api/body/ports')data={available:false,ports:[]};
    else if(p==='/api/body/state')data=body;
    else if(p.startsWith('/api/studio/')){
      if(failure)return route.fulfill({status:503,contentType:'application/json',body:'{"error":"本机服务暂不可用"}'});
      if(req.method()==='POST'){
        const value=req.postDataJSON();posts.push(value);
        assert.equal(req.headers()['x-deskbot-studio'],'e3c6');
        assert.equal(value.operation,'read_catalog');assert.equal(value.target_id,device);
        assert.equal(value.boot_id,boot);assert.equal(value.session_id,session);
        assert.equal(value.connection_id,live.connection_id);
        live.catalog={...empty('reading','reading_pages'),target_id:device,boot_id:boot,session_id:session,next_cursor:0};
        live.can_submit=false;live.last_command={command_id:value.command_id,operation:'read_catalog',status:'processed',reason:'command_processed'};
      }
      data=live;
    }
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/#connection');
  const panel=page.locator('.e3d2-live'),button=panel.locator('[data-e3d2-read]');
  await page.waitForFunction(()=>DeskBotStudio?.control().device?.session_id);
  await page.waitForFunction(()=>document.querySelector('[data-e3d2-read]')?.disabled===false);
  assert.equal(posts.length,0);checks.push('passive_load_'+width);
  await button.click();await page.waitForFunction(()=>DeskBotStudio.liveDirectory().status==='reading');
  assert.equal(posts.length,1);assert.equal(await button.isDisabled(),true);
  live.catalog.received_entries=2;live.catalog.total=36;live.catalog.next_cursor=2;
  await page.waitForFunction(()=>document.getElementById('e3d2-summary').textContent.includes('2 / 36'));
  assert.equal(await panel.locator('.e3d2-table').count(),0);
  await panel.locator('summary').click();
  live.catalog={status:'complete',reason:'verified',target_id:device,boot_id:boot,session_id:session,received_entries:36,total:36,next_cursor:null,manifest};live.can_submit=true;
  await page.waitForFunction(()=>document.querySelectorAll('.e3d2-table>div').length===36);
  assert.equal(await panel.locator('.e3d2-table>div').count(),36);
  assert.equal(await panel.locator('.e3d2-progress').getAttribute('aria-valuenow'),'100');
  await page.waitForFunction(()=>document.getElementById('e3d2-comparison').textContent==='与已验收快照一致');
  const neutralRow=panel.locator('.e3d2-table>div').filter({
    has:page.locator('b').getByText('neutral',{exact:true})
  });
  assert.equal(await neutralRow.count(),1);
  assert.equal(await neutralRow.locator('code').textContent(),
    manifest.entries.find(row=>row.kind==='expression'&&row.id==='neutral').resource_id);
  assert.equal(await page.evaluate(()=>DeskBotStudio.directory().live),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.equal(await panel.locator('.e3d2-digests dd').evaluateAll(nodes=>nodes.every(e=>e.scrollWidth<=e.clientWidth+1)),true);
  await panel.scrollIntoViewIfNeeded();
  if(width===1440){await page.locator('#theme-toggle').click();await panel.scrollIntoViewIfNeeded();}
  await panel.screenshot({path:path.join(out,`live-directory-${width}${reduced?'-reduced':''}.png`)});
  await page.waitForTimeout(900);assert.equal(await panel.locator('details').getAttribute('open'),'');
  checks.push('explicit_single_command_'+width,'partial_hidden_'+width,'complete_directory_'+width,'snapshot_and_control_distinct_'+width,'details_persist_'+width,'no_overflow_'+width,'digest_not_clipped_'+width);
  await button.click();await page.waitForFunction(()=>!DeskBotStudio.liveDirectory().live);
  live.catalog={...empty('failed','page_timeout'),target_id:device,boot_id:boot,session_id:session};live.can_submit=true;
  await page.waitForFunction(()=>document.getElementById('e3d2-message').textContent.includes('未收到回复'));
  assert.equal(await panel.locator('.e3d2-table').count(),0);
  assert.match(await page.locator('#e3d2-message').textContent(),/未收到回复/);
  live.catalog={status:'complete',reason:'verified',target_id:device,boot_id:boot,session_id:session,received_entries:36,total:36,next_cursor:null,manifest:{...manifest,catalog_id:'f'.repeat(64)}};
  await page.waitForFunction(()=>document.getElementById('e3d2-comparison').textContent.includes('版本不同'));
  failure=true;await page.waitForFunction(()=>DeskBotStudio.control().error);
  await page.waitForFunction(()=>document.querySelectorAll('.e3d2-table').length===0);
  assert.equal(await panel.locator('.e3d2-table').count(),0);
  failure=false;body.connection.status='disabled';
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='disabled');
  await page.waitForFunction(()=>document.querySelector('[data-e3d2-read]').disabled);
  assert.equal(await page.evaluate(()=>DeskBotStudio.liveDirectory().live),false);
  if(reduced){assert.equal(await panel.locator('.e3d2-progress>span').evaluate(e=>getComputedStyle(e).transitionDuration),'0s');checks.push('reduced_motion');}
  assert.deepEqual(errors,[]);assert.equal(posts.length,2);
  checks.push('reread_clears_old_'+width,'failed_page_'+width,'service_error_clears_'+width,'disconnect_clears_'+width,'no_page_errors_'+width);
  await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  try{
    await startServer();
    const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
    const executablePath=process.env.DESKBOT_BROWSER_EXE||(process.platform==='win32'&&fs.existsSync(edge)?edge:undefined);
    browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
    await exercise();await exercise(390);await exercise(320,true);
    const report={edition:'E3d-2',result:'passed',source:'offline_browser_fixture',checks,checks_passed:checks.length,hardware_accessed:false,device_written:false};
    const reportPath=process.env.DESKBOT_STUDIO_E3D2_REPORT_PATH||path.join(out,'browser-validation.json');
    fs.mkdirSync(path.dirname(reportPath),{recursive:true});
    fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  }finally{if(browser)await browser.close();if(server)server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
