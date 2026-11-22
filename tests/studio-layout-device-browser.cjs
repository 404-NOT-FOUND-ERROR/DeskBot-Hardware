'use strict';
// The real HTTP/UI runs against an explicit memory bridge; no serial is opened.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),http=require('node:http');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts/studio-layout-e3f4c-device/browser');
const checks=[],sequence=[],manifest=JSON.parse(fs.readFileSync(path.join(root,'artifacts/studio-layout-e3f4c-device/expected-directory.json')));
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-layout-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:20,can_submit:true,display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:['neutral','eat']},catalog:{status:'complete',target_id:device,boot_id:boot,session_id:session,manifest,received_entries:37,total:37,reason:'verified'},receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
const body=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-09T04:00:00.000Z',hardware_accessed:false,device_written:false,connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-09T04:00:00.000Z',last_seen_age_ms:20,application_state:null,internal_memory:null,events:[],dropped_lines:0,identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-09T04:00:00.000Z',age_ms:20,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
let browser,server,origin,previewServer,previewOrigin;
const sources=['scripts/deskbot-studio-e3c.js','scripts/deskbot-studio-play-readiness.js',
  'tests/serve_studio_layout_fixture.py','tests/studio-layout-device-browser.cjs','scripts/expression-layout-preview.html',
  'scripts/prepare-expression-layout-e3f4c.py','protocol/studio_expression_layout.py'];
const hashes=()=>Object.fromEntries(sources.map(name=>[name,require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex')]));
async function fixture(value){assert.equal((await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)})).status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',[path.join(root,'tests/serve_studio_layout_fixture.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});origin=await new Promise((resolve,reject)=>{let log='';const limit=setTimeout(()=>reject(Error(log)),15000);server.stdout.on('data',s=>{log+=s;const match=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(limit);resolve(match[0]);}});server.stderr.on('data',s=>log+=s);server.once('error',reject);server.once('exit',code=>{clearTimeout(limit);reject(Error(code+' '+log));});});}
async function startPreview(){const names=new Set(['index.html','eat-current.png','eat-target.png','neutral-current.png','neutral-target.png']);previewServer=http.createServer((request,response)=>{const name=request.url.slice(1)||'index.html';if(!names.has(name)){response.writeHead(404);response.end();return;}response.writeHead(200,{'Content-Type':name.endsWith('.png')?'image/png':'text/html; charset=utf-8'});response.end(fs.readFileSync(path.join(root,'artifacts/expression-layout-e3f4c',name)));});await new Promise(resolve=>previewServer.listen(0,'127.0.0.1',resolve));previewOrigin='http://127.0.0.1:'+previewServer.address().port;}
async function preview(width){
  const context=await browser.newContext({viewport:{width,height:1060},reducedMotion:width===320?'reduce':'no-preference'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(previewOrigin);
  await page.waitForFunction(()=>!document.getElementById('play').disabled);
  const pixels=()=>page.evaluate(()=>{const a=document.getElementById('current').getContext('2d').getImageData(0,0,360,360).data,b=document.getElementById('target').getContext('2d').getImageData(0,0,360,360).data;return {equal:a.every((v,i)=>v===b[i]),currentVisible:a.some((v,i)=>i%4===3&&v),targetVisible:b.some((v,i)=>i%4===3&&v)};});
  let actual=await pixels();assert.equal(actual.equal,false);assert.equal(actual.currentVisible,true);assert.equal(actual.targetVisible,true);checks.push('same_eat_different_layout_visible_'+width);
  await page.locator('#restart').click();await page.waitForFunction(()=>Number(document.getElementById('frame').value)>=2);
  await page.locator('#play').click();const frame=await page.locator('#frame').inputValue();await page.waitForTimeout(120);assert.equal(await page.locator('#frame').inputValue(),frame);checks.push('synchronized_play_and_pause_'+width);
  await page.getByRole('button',{name:'平静',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('play').disabled&&document.getElementById('frame').max==='13');
  actual=await pixels();assert.equal(actual.equal,true);assert.equal(actual.targetVisible,true);assert.equal(await page.locator('#target-mirror').textContent(),'自动镜像');checks.push('neutral_preserves_original_mirror_'+width);
  for(const label of ['进食','平静','进食'])await page.getByRole('button',{name:label,exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('play').disabled&&document.getElementById('frame').max==='74');
  assert.equal(await page.locator('#target-mirror').textContent(),'镜像关闭');assert.equal((await pixels()).equal,false);checks.push('rapid_switch_uses_latest_asset_'+width);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);checks.push('responsive_without_errors_'+width);
  if(width===1440)await page.screenshot({path:path.join(out,'preview-1440.png'),fullPage:true});
  await context.close();
}
async function mixed(){
  await start();await fixture({live:ready(),body:body()});
  const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));await page.goto(origin+'/#connection');
  await page.waitForFunction(()=>window.DeskBotStudio?.control().device?.display);
  await page.waitForFunction(()=>document.getElementById('e3d2-count')?.textContent.includes('37'));
  assert.equal(await page.evaluate(()=>DeskBotStudio.control().device.catalog.manifest.entries.find(r=>r.id==='eat').layout_id),'full_frame');checks.push('live_full_frame_catalog_37_entries');
  for(const [index,operation] of ['eat','eat','neutral','eat','neutral','eat'].entries()){
    const button=page.locator(`[data-e3c-action="${operation}"]`);await button.waitFor({state:'visible'});
    await page.waitForFunction(op=>!document.querySelector(`[data-e3c-action="${op}"]`).disabled,operation);
    const before=(await requests()).length;
    const previous=await page.evaluate(()=>DeskBotStudio.control().device?.last_command?.command_id||null);
    // Leave the preceding command and receipt in place while a query is stale.
    if(index===3){const state=await page.evaluate(()=>DeskBotStudio.control().device);await fixture({live:{...state,display:null,age_ms:3500,can_submit:false,status:'waiting'}});await page.waitForTimeout(900);await button.click();await page.waitForTimeout(350);assert.equal((await requests()).length,before);await fixture({live:{...state,...ready(),receipts:state.receipts,last_command:state.last_command}});}
    else await button.click();
    await page.waitForFunction(({operation,previous})=>DeskBotStudio.control().device?.last_command?.operation===operation&&DeskBotStudio.control().device.last_command.command_id!==previous&&DeskBotStudio.control().device?.receipts?.some(r=>r.status==='dispatched'&&r.request_id==='expression-'+DeskBotStudio.control().device.last_command.command_id),{operation,previous});
    const after=(await requests()).slice(before);assert.equal(after.length,1);assert.equal(after[0].operation,operation);
    const command=after[0],state=await page.evaluate(()=>DeskBotStudio.control().device);
    assert.equal(state.last_command.command_id,command.command_id);assert.equal(state.receipts[0].request_id,'expression-'+command.command_id);
    assert.match(await page.locator('#e3c-outcome').textContent(),/已提交播放器/);
    sequence.push({operation,command_id:command.command_id,receipt_request_id:state.receipts[0].request_id});
    checks.push('independent_click_and_dispatch_'+index+'_'+operation);
  }
  assert.equal(new Set(sequence.map(row=>row.command_id)).size,sequence.length);checks.push('all_six_click_ids_unique');
  assert.deepEqual(errors,[]);checks.push('mixed_ui_without_errors');
  await page.screenshot({path:path.join(out,'mixed-playback.png'),fullPage:true});await context.close();
}
(async()=>{fs.mkdirSync(out,{recursive:true});const before=hashes();try{await startPreview();browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});await preview(1440);await preview(320);await mixed();assert.deepEqual(hashes(),before);const report={schema:'deskbot.layout_browser_validation/v1',result:'passed',checks,checks_passed:checks.length,sequence,production_http:true,bridge:'memory',hardware_accessed:false,device_written:false,source_sha256:before,source_unchanged:true};fs.writeFileSync(path.join(out,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({result:report.result,checks:checks.length,sequence:sequence.map(row=>row.operation)}));}finally{if(browser)await browser.close();if(server)server.kill();if(previewServer)previewServer.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
