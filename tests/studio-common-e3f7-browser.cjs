'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/studio-common-e3f7/browser');
const html=fs.readFileSync(path.join(root,'tmp/deskbot-studio-e3f7-20261110/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">(.*?)<\/script>/s)[1]);
const semantics=['neutral','embarrassed','eat','happy','sad','sleepy','confused','surprised'];
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
const checks=[];let server,browser,origin,activePage;const pageErrors=[];
const ready=()=>({schema:'deskbot.studio_control_link/v1',source:'usb_session',connection_id:'connection-test',target_id:device,boot_id:boot,session_id:session,enabled:true,status:'ready',reason:'sleep_unknown',age_ms:50,can_submit:true,
 display:{application:{phase:'settled',value:'idle'},page:{phase:'settled',value:'home'},player:{state:'initialized'},expression_semantics:[...semantics]},
 catalog:{status:'complete',reason:'verified',target_id:device,boot_id:boot,session_id:session,manifest:structuredClone(data.role_reference),received_entries:37,total:37},receipts:[],pending:null,host_command:null,local_result:null,last_command:null});
const body=()=>({schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,generated_at:'2026-11-10T09:00:00.000Z',hardware_accessed:false,device_written:false,
 connection:{status:'connected',port:'COM4',reason:'memory_fixture'},freshness:'fresh',fresh_for_ms:30000,live:true,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,last_seen_at:'2026-11-10T09:00:00.000Z',last_seen_age_ms:50,application_state:null,internal_memory:null,events:[],dropped_lines:0,
 identity:{status:'confirmed',source:'device_announcement',received_at:'2026-11-10T09:00:00.000Z',age_ms:50,reason:'memory_fixture',rejected_messages:0,hello:{schema:'deskbot.device_link/v1',message_type:'hello',device_id:device,boot_id:boot,sequence:3,uptime_ms:31000,
 firmware:{edition:'E3f-7-memory',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,limits:{max_frame_bytes:2048,hello_interval_ms:10000}}}});
async function fixture(value){const r=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(r.status,200);}
async function requests(){return (await (await fetch(origin+'/__fixture/state')).json()).requests;}
async function start(){
 server=spawn(process.env.DESKBOT_TEST_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe',[path.join(root,'tests/serve_studio_common_e3f7_fixture.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
 origin=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',s=>log+=s);server.stdout.on('data',s=>{log+=s;const m=log.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);server.once('exit',c=>{clearTimeout(timer);reject(Error(c+' '+log));});});
}
async function settled(page){await page.waitForFunction(()=>{
 const state=DeskBotStudio.control(),semantics=state.device?.display?.expression_semantics??['neutral'];
 // Identity and control replies arrive separately; wait until the UI has painted both.
 return state.device?.catalog?.total===37&&!state.busy&&DeskBotStudio.observation().device?.identity?.status==='confirmed'&&
   (!document.querySelector('#studio-control-slot')||document.querySelector('#e3f7-capabilities')?.textContent.includes(`${semantics.length} 种`));
});}
async function screen(width){
 await fixture({live:ready(),body:body()});
 const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'}),p=await context.newPage(),errors=[];
 activePage=p;p.on('pageerror',e=>{errors.push(String(e));pageErrors.push(String(e));});
 const draft=structuredClone(data.initial);draft.roles[0].bindings.embarrassed='shy_candidate.eaf';
 await p.addInitScript(({draft,key})=>localStorage.setItem(key,JSON.stringify(draft)),{draft,key:'deskbot.studio.e2a.'+data.catalog_id.slice(0,16)});
 const initial=(await requests()).length;
 await p.goto(origin+'/#connection');await settled(p);
 assert.equal(await p.locator('.edition').textContent(),'E3f·7');
 assert.match(await p.locator('#e3f7-capabilities').textContent(),/8 种/);
 for(const s of semantics)assert.equal(await p.locator(`[data-e3c-action="${s}"]`).isEnabled(),true,s);
 assert.equal((await requests()).length,initial);checks.push('eight_buttons_no_automatic_play_'+width);
 for(const s of ['eat','neutral','embarrassed','eat',...semantics.slice(3)]){
   const before=(await requests()).length;
   await p.locator(`[data-e3c-action="${s}"]`).click();
   await p.waitForFunction(()=>!DeskBotStudio.control().busy);await settled(p);
   const commands=(await requests()).slice(before);assert.equal(commands.length,1);assert.equal(commands[0].operation,s);
 }
 checks.push('legacy_mix_and_five_common_buttons_send_once_'+width);
 for(const s of semantics){
   await p.goto(origin+'/#editor');await settled(p);
   await p.locator(`[data-action="select-semantic"][data-semantic="${s}"]`).first().click();
   await p.waitForFunction(s=>DeskBotStudio.state().semantic===s,s);
   assert.equal(await p.locator('#node-resource canvas').getAttribute('data-layout'),s==='eat'?'full_frame':'eye_anim');
   const before=(await requests()).length;await p.locator('[data-e3f6-node]').click();
   await p.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);await settled(p);
   const commands=(await requests()).slice(before);assert.equal(commands.length,1);assert.equal(commands[0].operation,s);
   checks.push('node_'+s+'_correct_layout_and_one_send_'+width);
 }
 await p.screenshot({path:path.join(out,'nodes-'+width+'.png'),fullPage:true});
 // Old E5 capabilities, explicit empty list and legacy absence have distinct UI states.
 for(const caps of [semantics.slice(0,3),[],null]){
   const state=ready();if(caps===null)delete state.display.expression_semantics;else state.display.expression_semantics=caps;
   await fixture({live:state});await p.goto(origin+'/#connection');await settled(p);
   await p.waitForFunction(caps=>JSON.stringify(DeskBotStudio.control().device?.display?.expression_semantics??null)===JSON.stringify(caps),caps);
   for(const s of semantics)assert.equal(await p.locator(`[data-e3c-action="${s}"]`).isEnabled(),(caps??['neutral']).includes(s),s);
 }
 checks.push('E5_empty_and_legacy_capability_UI_'+width);
 await fixture({live:ready()});await p.goto(origin+'/#connection');await settled(p);
 await p.waitForFunction(()=>DeskBotStudio.control().device?.display?.expression_semantics?.length===8);
 await p.screenshot({path:path.join(out,'connection-'+width+'.png'),fullPage:true});
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
 checks.push('responsive_and_no_script_errors_'+width);
 await context.close();
}
async function waitingAndReconnection(){
 await fixture({live:ready(),body:body()});
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),p=await context.newPage();
 await p.goto(origin+'/#connection');await settled(p);
 const wait=ready();wait.display=null;wait.can_submit=false;wait.age_ms=3500;wait.status='waiting';wait.reason='state_stale';
 await fixture({live:wait});await p.waitForFunction(()=>DeskBotStudio.control().device?.display===null);
 const before=(await requests()).length;
 await p.locator('[data-e3c-action="happy"]').click();await p.waitForFunction(()=>DeskBotStudio.control().busy);
 await p.waitForTimeout(180);assert.equal((await requests()).length,before);
 await fixture({live:ready()});await p.waitForFunction(()=>!DeskBotStudio.control().busy);assert.equal((await requests()).length,before+1);
 checks.push('new_button_waits_fresh_state_then_sends_once');
 await fixture({live:wait});await p.waitForFunction(()=>DeskBotStudio.control().device?.display===null);
 const cancelBefore=(await requests()).length;
 await p.locator('[data-e3c-action="sad"]').click();await p.waitForFunction(()=>DeskBotStudio.control().busy);
 await p.locator('[data-e3c-action="cancel"]').click();
 await fixture({live:ready()});await p.waitForFunction(()=>!DeskBotStudio.control().busy);assert.equal((await requests()).length,cancelBefore);
 checks.push('new_button_cancel_does_not_send');
 const newSession=ready();newSession.session_id='session-'+'b'.repeat(32)+'-2';newSession.catalog.session_id=newSession.session_id;
 await fixture({live:newSession});await p.waitForFunction(()=>DeskBotStudio.control().device?.session_id?.endsWith('-2'));
 await p.goto(origin+'/#editor');await settled(p);await p.locator('[data-action="select-semantic"][data-semantic="surprised"]').first().click();
 await p.waitForFunction(()=>DeskBotStudio.state().semantic==='surprised');
 const reconnected=(await requests()).length;await p.locator('[data-e3f6-node]').click();
 await p.waitForFunction(()=>!DeskBotStudio.roleExpression().busy);
 assert.equal((await requests()).length,reconnected+1);assert.equal((await requests()).at(-1).session_id,newSession.session_id);
 checks.push('first_common_node_click_after_new_session');
 await context.close();
}
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const previous=path.join(out,'validation.json');
 if(fs.existsSync(previous)){let n=1;while(fs.existsSync(path.join(out,`validation.attempt${n}.json`)))n++;fs.renameSync(previous,path.join(out,`validation.attempt${n}.json`));}
 try{
   const executablePath=process.env.DESKBOT_TEST_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
   if(!fs.existsSync(executablePath))throw Error('请先核对已安装浏览器路径：'+executablePath);
   await start();browser=await chromium.launch({headless:true,executablePath});
   for(const width of [1440,390,320])await screen(width);
   await waitingAndReconnection();
   const commands=await requests();assert.equal(new Set(commands.map(c=>c.command_id)).size,commands.length);checks.push('every_click_has_a_unique_command_id');
   fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify({result:'passed',checks_passed:checks.length,checks,hardware_accessed:false,device_written:false,requests:commands.length},null,2)+'\n');
   console.log(JSON.stringify({result:'passed',checks:checks.length,requests:commands.length}));
 }catch(e){
   const state=activePage&&!activePage.isClosed()?await activePage.evaluate(()=>({control:DeskBotStudio.control(),observation:DeskBotStudio.observation(),capabilities:[...document.querySelectorAll('#e3f7-capabilities')].map(x=>x.textContent)})).catch(()=>null):null;
   if(activePage&&!activePage.isClosed())await activePage.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
   fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify({result:'failed',checks_passed:checks.length,checks,error:String(e),page_errors:pageErrors,state,hardware_accessed:false},null,2)+'\n');throw e;
 }
 finally{if(browser)await browser.close();if(server)server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
