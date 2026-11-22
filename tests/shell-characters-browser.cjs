/* Entire local shell flow with a memory bridge and real host persistence. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/shell-characters-qa');
let browser,server,folder,origin;const errors=[],checks=[],posts=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function check(value,name){assert.ok(value,name);checks.push(name);}
const state=page=>page.evaluate(()=>DeskBotStudio.shellCharacters());
const wait=page=>page.waitForFunction(()=>!DeskBotStudio.shellCharacters().busy);
async function post(route,value,tag='shell-characters-v1'){
 const r=await fetch(origin+route,{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':tag},body:JSON.stringify(value)});
 const body=await r.json();assert.equal(r.status,200,JSON.stringify(body));return body;
}
(async()=>{
 folder=fs.mkdtempSync(path.join(root,'tmp/shell-characters-browser-'));fs.mkdirSync(out,{recursive:true});
 server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_shell_workbench_fixture.py',folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
 for(let i=0;i<400&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
 origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
 browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1460,height:1050},reducedMotion:'reduce'}),page=await context.newPage();
 context.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(origin+'/#shells');await page.waitForSelector('#sc-form');await wait(page);
 check(await page.locator('[name="character_id"] option').count()===1,'empty shell flow directs user to existing full character manager');
 check(await page.locator('[data-navigate="capture"]').count()>=1,'technical sampling remains a separate advanced tool');
 const catalog=await fetch(origin+'/api/characters/state').then(r=>r.json()),profiles=[];
 for(const [i,expression] of catalog.expression_catalog.roles.entries()){
  const profile={schema:'deskbot.role_profile.v2',role_id:'character-'+i,display_name:['圆团伙伴','老朋友'][i],persona:'好奇，简短回答',voice:{provider:'unconfigured',voice_id:'',speaking_style:''},expression_role_id:expression.id,expression_graph_sha256:catalog.expression_catalog.graph_sha256,preview_catalog_id:catalog.expression_catalog.catalog_id,profile_revision:0};
  profiles.push((await post('/api/characters/save',{profile,expected_revision:0},'characters-v1')).profile);
 }
 await page.locator('[data-sc-action="refresh"]').click();await wait(page);
 await page.locator('[name="shell_name"]').fill('白色圆团');await page.locator('[name="character_id"]').selectOption('character-0');
 await pause(1000);check(await page.locator('[name="shell_name"]').inputValue()==='白色圆团','input survives waiting');
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 const first=(await state(page)).data.shells[0];
 check(first.binding.role_id==='character-0'&&first.binding.expression_role_id!==first.binding.role_id,'shell pins full character and explicit expression identity');
 check(first.recognition==='not_registered'&&first.sync_status==='pending_device','saved choice keeps recognition and device sync separate');
 await page.reload();await page.waitForSelector('#sc-form');await wait(page);
 check(await page.locator('[name="shell_name"]').inputValue()==='白色圆团','shell and association reopen');
 await page.locator('[data-sc-action="add"]').click();await page.locator('[name="shell_name"]').fill('蓝色外壳');await page.locator('[name="character_id"]').selectOption('character-1');
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 let current=await state(page);const second=current.data.shells.find(s=>s.shell_id!==first.shell_id);
 check(current.data.shells.length===2,'second shell has its own saved association');
 check(current.a===first.shell_id&&current.b===second.shell_id,'two saved shells default to a visible character change');
 await page.locator('#sc-a').selectOption(first.shell_id);await page.locator('#sc-b').selectOption(second.shell_id);
 await page.locator('#sc-scenario').selectOption('speaking');await page.locator('[data-sc-action="simulate"]').click();await wait(page);
 let run=(await state(page)).data.last_run;
 check(run.state.actual.role_id==='character-1','speaking scenario switches after the sentence');
 check(run.timeline.some(r=>r.reason==='speaking')&&run.timeline.some(r=>r.event==='first_frame')&&run.timeline.some(r=>r.event==='saved'),'timing and separate receipts appear in real UI result');
 const labScenarios=Object.keys((await state(page)).data.session_scenarios);
 for(const scenario of labScenarios){
  await page.locator('.sc-session-lab summary').click();
  await page.locator('#sc-session-scenario').selectOption(scenario);await page.locator('[data-sc-action="session-lab"]').click();await wait(page);
  run=(await state(page)).data.last_run;
  const expected=['rapid','removed','unknown','stale_fact'].includes(scenario)?'character-0':'character-1';
  check(run.kind==='session_lab'&&run.state.actual.role_id===expected&&run.adapter.error===null,'production protocol memory session: '+scenario);
  check(run.device_commands===0&&run.hardware_accessed===false,'session stays offline: '+scenario);
 }
 check(await page.locator('.sc-lab-result').innerText().then(v=>v.includes('内存设备')&&v.includes('人格与声音待接入')),'memory results and voice implementation status are clear');
 await page.reload();await page.waitForSelector('#sc-form');await wait(page);
 check((await state(page)).data.last_run.kind==='session_lab','protocol integration result reopens');
 await page.route('**/api/shell-characters/session-lab',r=>r.fulfill({status:503,json:{error:'联测暂未完成'}}));
 await page.locator('.sc-session-lab summary').click();await page.locator('[data-sc-action="session-lab"]').click();await wait(page);
 check((await state(page)).error&&!await page.locator('[data-sc-action="session-lab"]').isDisabled(),'failed protocol run restores controls');
 await page.unroute('**/api/shell-characters/session-lab');
 for(const scenario of ['rapid','removed','unknown','moving','updating','reconnect','old_receipt']){
  await page.locator('#sc-scenario').selectOption(scenario);await page.locator('[data-sc-action="simulate"]').click();await wait(page);
  run=(await state(page)).data.last_run;
  check(run.current&&run.scenario===scenario,'scenario completes: '+scenario);
 }
 check((await state(page)).data.last_run.timeline.some(r=>r.reason==='old_receipt'),'late receipt is discarded in timeline');
 await page.locator('[data-sc-open="'+second.shell_id+'"]').click();await page.locator('[name="shell_name"]').fill('蓝色外壳 · 修改');
 await page.route('**/api/shell-characters/confirm',r=>r.fulfill({status:503,json:{error:'保存暂未完成'}}));
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 check((await state(page)).dirty&&await page.locator('[name="shell_name"]').inputValue()==='蓝色外壳 · 修改','failed association preserves complete form after name save');
 await page.unroute('**/api/shell-characters/confirm');await page.locator('#sc-form [type="submit"]').click();await wait(page);
 check(!(await state(page)).dirty,'retry completes association without a duplicate shell');
 const changed={...profiles[1],persona:'另一页改了性格'};const updated=(await post('/api/characters/save',{profile:changed,expected_revision:1},'characters-v1')).profile;
 await page.locator('[data-sc-action="refresh"]').click();await wait(page);
 check(!(await state(page)).data.shells.find(s=>s.shell_id===second.shell_id).role_current,'profile edit marks association as needing confirmation');
 check(await page.locator('.sc-update').count()>0,'version update has a visible next action');
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 check((await state(page)).data.shells.find(s=>s.shell_id===second.shell_id).binding.profile_revision===updated.profile_revision,'explicit confirm adopts latest full character revision');
 const history=await post('/api/shells/example',{},'shells-v1');await page.locator('[data-sc-action="refresh"]').click();await wait(page);
 let sources=(await state(page)).data.sources;
 check(sources.length===2&&sources.every(s=>s.recognition==='historical_only'),'history sources keep their qualification');
 await page.locator('[data-sc-open="'+first.shell_id+'"]').click();await page.locator('[name="registration_source"]').selectOption(sources[0].key);
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 await page.locator('[data-sc-open="'+second.shell_id+'"]').click();await page.locator('[name="registration_source"]').selectOption(sources[1].key);
 await page.locator('#sc-form [type="submit"]').click();await wait(page);
 await page.locator('.sc-replay summary').click();await page.locator('#sc-project').selectOption(history.session.id);await page.locator('[data-sc-action="replay"]').click();await wait(page);
 check((await state(page)).data.shells.every(s=>s.next_action==='continue_registration'),'historical examples direct the next step to real registration');
 run=(await state(page)).data.last_run;
 check(run.kind==='log_windows'&&run.rows.some(r=>r.role_id),'original logs reidentify into full characters');
 check(run.rows.filter(r=>r.observation.status!=='known').every(r=>r.role_id===null),'unknown log windows preserve current role');
 await page.locator('.sc-replay summary').click();await page.locator('[data-sc-action="replay-session"]').click();await wait(page);
 run=(await state(page)).data.last_run;
 check(run.kind==='runtime_replay'&&run.input_kind==='stored_samples_and_memory_device','original raw logs pass through production recognizer and memory session');
 const originals=history.session.tasks.filter(t=>t.use!=='train'&&t.recording);
 check(run.rows.length===originals.length&&run.total_samples===originals.reduce((sum,t)=>sum+t.recording.sample_count,0)&&run.segment_mode==='independent_recording_windows','recorded windows keep independent sessions and original samples');
 check(run.rows.some(r=>r.recognition.status==='known')&&run.rows.every(r=>!r.registration_ready),'history recognition retains its registration qualification');
 check(run.simulated_activation_count===0&&run.device_commands===0&&!run.hardware_accessed,'review-only history never activates a role');
 check(await page.locator('.sc-runtime-result').innerText().then(v=>v.includes('登记验证待完成')&&v.includes('内存设备')),'recognition and next registration step are visible');
 await page.screenshot({path:path.join(out,'runtime-replay-desktop.png'),fullPage:true});
 await page.reload();await page.waitForSelector('#sc-form');await wait(page);
 check((await state(page)).data.last_run.kind==='runtime_replay','raw replay result reopens with configuration version');
 await page.route('**/api/shell-characters/replay-session',r=>r.fulfill({status:503,json:{error:'原日志联测暂未完成'}}));
 await page.locator('.sc-replay summary').click();await page.locator('[data-sc-action="replay-session"]').click();await wait(page);
 check((await state(page)).error&&!await page.locator('[data-sc-action="replay-session"]').isDisabled(),'raw replay failure restores controls and saved result');
 await page.unroute('**/api/shell-characters/replay-session');
 for(const width of [820,390]){await page.setViewportSize({width,height:1050});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'raw replay no overflow at '+width);}
 await page.screenshot({path:path.join(out,'runtime-replay-mobile.png'),fullPage:true});await page.setViewportSize({width:1460,height:1050});
 await page.locator('.sc-session-lab summary').click();await page.locator('#sc-session-scenario').selectOption('save_failure');await page.locator('[data-sc-action="session-lab"]').click();await wait(page);
 check(await page.locator('.sc-timeline').innerText().then(v=>v.includes('保存待重试')&&v.includes('模拟选择已保存')),'failure and recovery are readable in the same timeline');
 await page.screenshot({path:path.join(out,'shells-desktop.png'),fullPage:true});
 for(const width of [820,390]){await page.setViewportSize({width,height:1050});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow at '+width);}
 await page.screenshot({path:path.join(out,'shells-mobile.png'),fullPage:true});
 await page.setViewportSize({width:1460,height:1050});await page.locator('.sc-heading [data-navigate="capture"]').click();await page.waitForSelector('#shell-session');
 await page.waitForFunction(()=>document.querySelectorAll('#shell-session option').length>1);
 check(await page.locator('#shell-session option').count()>1,'advanced sampling project remains available');
 await page.locator('[data-view="characters"]').click();await page.waitForSelector('#character-form');
 check(await page.locator('[data-view="shells"]').count()===1,'one everyday shell entry remains in the integrated workbench');
 check((await fetch(origin+'/__fixture/requests').then(r=>r.json())).requests.length===0,'no device commands during registration or rehearsal');
 check(posts.every(p=>p.startsWith('/api/shell-characters/')),'browser flow uses only local shell endpoints');
 check(errors.length===0,errors.join('\n')||'no browser errors');
 const report={checks:checks.length,passed:checks,browser_errors:errors,hardware_accessed:false,device_commands:0};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();server?.kill();await pause(700);if(folder)fs.rmSync(folder,{recursive:true,force:true});});
