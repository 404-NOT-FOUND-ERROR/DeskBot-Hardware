'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),qa=path.join(root,'tmp/deskbot-studio-e3f8-20261111/qa');
const python=process.env.DESKBOT_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe';
let browser,server;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function start(state){
  const process=spawn(python,['scripts/serve-deskbot-studio-e3f8.py','--port','0','--state-path',state],{cwd:root,windowsHide:true,env:{...global.process.env,PYTHONIOENCODING:'utf-8'}});
  let output='';process.stdout.on('data',chunk=>output+=chunk);process.stderr.on('data',chunk=>output+=chunk);
  const deadline=Date.now()+15000;
  while(Date.now()<deadline&&!/http:\/\/127\.0\.0\.1:\d+/.test(output)&&process.exitCode===null)await pause(50);
  const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);assert.ok(match,output);return {process,url:match[0]};
}
async function stop(){if(!server)return;server.process.kill();await new Promise(resolve=>server.process.once('exit',resolve));server=null;}
(async()=>{
  fs.mkdirSync(qa,{recursive:true});
  const state=path.join(fs.mkdtempSync(path.join(root,'tmp/roles-browser-')),'state.json');
  server=await start(state);
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1450,height:1050}}),page=await context.newPage();
  const errors=[],commands=[];let checks=0;
  const check=(condition,message)=>{assert.ok(condition,message);checks++;};
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(/\/api\/(studio\/command|body\/connect)/.test(request.url()))commands.push(request.url());});
  const snapshot=()=>page.evaluate(()=>DeskBotStudio.runtimeRoles());
  async function waitRole(id){await page.waitForFunction(id=>{const s=DeskBotStudio.runtimeRoles();return !s.working&&!s.resolving&&s.runtime?.activation?.role_id===id&&s.result?.activation?.role_id===id;},id);}
  await page.goto(server.url+'/#roles');
  await waitRole('xiaozhi-e145');
  check(await page.locator('[data-role-enable]').count()===2,'two roles');
  check(await page.locator('[data-role-semantic]').count()===6,'six canonical semantics');
  check((await snapshot()).result.expression.resource_id==='winking.eaf','default real mapping');
  await page.locator('[data-role-semantic=embarrassed]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.resource_id==='shy_candidate.eaf');
  check((await snapshot()).result.expression.fps===25,'E1.45 shy fps');
  check(await page.locator('#runtime-expression canvas').first().getAttribute('data-layout')==='eye_anim','old role mirrored');
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();
  await waitRole('grok-round-personal-v1');
  check((await snapshot()).semantic==='neutral','role starts neutral');
  await page.locator('[data-runtime-pause]').click();
  const mainPause=await page.locator('.runtime-screen').getAttribute('data-frame');
  await pause(120);
  check(await page.locator('.runtime-screen').getAttribute('data-frame')===mainPause,'main round pause freezes pixels frame');
  await page.locator('[data-runtime-pause]').click();
  await page.locator('[data-role-semantic=happy]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().playback?.current==='happy'&&DeskBotStudio.runtimeRoles().playback?.phase==='loop');
  check(await page.locator('[data-runtime-node]').count()===3,'three phase nodes');
  check((await snapshot()).result.expression.mirror===false,'full face without mirror');
  await page.waitForFunction(()=>document.querySelector('.runtime-screen')?.getAttribute('data-playing-state')==='happy',null,{timeout:4000});
  check(await page.locator('.runtime-screen').getAttribute('data-playing-state')==='happy','decoded atlas displayed');
  const mainSamples=[];
  for(let i=0;i<5;i++){await pause(65);mainSamples.push(await page.locator('.runtime-screen').getAttribute('data-frame'));}
  check(new Set(mainSamples).size>1,'main round advances independently of DOM repaint');
  check(await page.evaluate(()=>{const s=DeskBotStudio.runtimeRoles().playback,c=document.querySelector('.runtime-screen');return c.dataset.frame===String(s.frame)&&c.dataset.playingPhase===s.phase&&c.dataset.playingState===s.current;}),'main round uses scheduler exact phase and frame');
  await page.locator('[data-role-semantic=sad]').click();
  await page.locator('[data-role-semantic=confused]').click();
  await page.locator('[data-role-semantic=embarrassed]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().playback?.target==='shy');
  check((await snapshot()).semantic==='embarrassed','rapid semantic keeps latest');
  await page.locator('[data-runtime-pause]').click();
  const frame=(await snapshot()).playback.frame;
  await pause(150);check((await snapshot()).playback.frame===frame,'pause freezes frame');
  await page.screenshot({path:path.join(qa,'desktop.png'),fullPage:true});
  await page.reload();await waitRole('grok-round-personal-v1');
  check((await snapshot()).runtime.storage_status==='saved','refresh restores saved role');
  check(JSON.parse(fs.readFileSync(state,'utf8')).role_id==='grok-round-personal-v1','disk really saved');

  // Delay only one activation: B->A must finish at the final click even while B's reply is late.
  await page.locator('[data-role-enable=xiaozhi-e145]').click();await waitRole('xiaozhi-e145');
  let delayed=false;
  await page.route('**/api/roles/activate',async route=>{
    if(!delayed){delayed=true;const reply=await route.fetch();await pause(450);await route.fulfill({response:reply});}
    else await route.continue();
  });
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();
  await page.locator('[data-role-enable=xiaozhi-e145]').click();
  await waitRole('xiaozhi-e145');await page.unroute('**/api/roles/activate');
  check(JSON.parse(fs.readFileSync(state,'utf8')).role_id==='xiaozhi-e145','latest reverse click persisted');

  // A stale expression reply must not paint after the active role changes.
  await page.route('**/api/roles/expression',async route=>{
    if(route.request().postDataJSON().semantic==='happy'){
      const reply=await route.fetch();await pause(500);await route.fulfill({response:reply});
    }else await route.continue();
  });
  await page.locator('[data-role-semantic=happy]').click();
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();
  await waitRole('grok-round-personal-v1');await pause(650);
  check((await snapshot()).result.activation.role_id==='grok-round-personal-v1','late expression ignored');
  check((await snapshot()).semantic==='neutral','old semantic does not restore');
  await page.unroute('**/api/roles/expression');

  const second=await context.newPage();await second.goto(server.url+'/#roles');
  await second.waitForFunction(()=>!!DeskBotStudio.runtimeRoles().result);
  await second.locator('[data-role-enable=xiaozhi-e145]').click();
  await second.waitForFunction(()=>!DeskBotStudio.runtimeRoles().working&&DeskBotStudio.runtimeRoles().runtime?.activation?.role_id==='xiaozhi-e145');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await waitRole('xiaozhi-e145');
  check((await snapshot()).runtime.activation.role_id==='xiaozhi-e145','focus syncs another page');
  await second.close();await page.bringToFront();

  // Save errors retain the previous role, and the retry entry stays visible.
  await page.route('**/api/roles/activate',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'角色选择暂未保存，请重试。'})}));
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();
  await page.waitForFunction(()=>!DeskBotStudio.runtimeRoles().working&&DeskBotStudio.runtimeRoles().error.includes('保存'));
  check((await snapshot()).runtime.activation.role_id==='xiaozhi-e145','failed save retains active role');
  check(await page.locator('[data-runtime-retry]').isVisible(),'save error retry visible');
  await page.unroute('**/api/roles/activate');
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();await waitRole('grok-round-personal-v1');

  // Existing authoring edits keep a separate selected role and a separate catalog.
  const draft=await page.evaluate(()=>DeskBotStudio.snapshot());
  await page.locator('.runtime-draft-editor > summary').click();
  const draftId=await page.locator('[data-action=select-role]').last().getAttribute('data-role');
  await page.locator(`[data-action=select-role][data-role="${draftId}"]`).click();await waitRole('grok-round-personal-v1');
  check((await snapshot()).runtime.activation.role_id==='grok-round-personal-v1','authoring role cannot activate runtime role');
  check((await page.evaluate(()=>DeskBotStudio.snapshot())).selected_role_id!==draft.selected_role_id,'draft selection actually changed');
  await page.locator('[data-view=editor]').click();
  await page.locator('#graph-world').waitFor();
  check(await page.locator('#graph-world').count()===1,'old node editor still mounted');
  if(await page.evaluate(()=>DeskBotStudio.state().playing))await page.locator('[data-action=play]').first().click();
  await page.locator('[data-view=roles]').click();await waitRole('grok-round-personal-v1');
  await page.locator('[data-role-enable=xiaozhi-e145]').click();await waitRole('xiaozhi-e145');
  if((await snapshot()).paused)await page.locator('[data-runtime-pause]').click();
  await page.locator('[data-role-semantic=happy]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='happy');
  const officialSamples=[];
  for(let i=0;i<5;i++){await pause(65);officialSamples.push(await page.locator('.runtime-screen').getAttribute('data-frame'));}
  check(new Set(officialSamples).size>1,'authoring pause does not freeze original role');
  await page.locator('[data-runtime-pause]').click();
  const officialFrame=await page.locator('.runtime-screen').getAttribute('data-frame');await pause(160);
  check(await page.locator('.runtime-screen').getAttribute('data-frame')===officialFrame,'original role pause controls its own clock');
  await page.locator('[data-role-enable=grok-round-personal-v1]').click();await waitRole('grok-round-personal-v1');

  for(const width of [390,320]){
    await page.setViewportSize({width,height:950});await pause(200);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow '+width);
    check(await page.locator('[data-role-enable]').count()===2,'mobile role choices '+width);
    await page.screenshot({path:path.join(qa,'mobile-'+width+'.png'),fullPage:true});
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().paused===true);
  check((await snapshot()).paused===true,'reduced motion pauses preview');
  check(errors.length===0,'no browser errors '+errors.join('\n'));
  check(commands.length===0,'role selection opens no USB and sends no command');
  await context.close();await stop();
  server=await start(state);
  const restored=await browser.newPage();await restored.goto(server.url+'/#roles');
  await restored.waitForFunction(()=>DeskBotStudio.runtimeRoles().runtime?.activation?.role_id==='grok-round-personal-v1');
  check(true,'server restart restores role');
  await restored.close();
  await stop();
  const offline=await browser.newPage();await offline.goto(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e3f8-20261111/index.html')).href+'#roles');
  await offline.locator('[data-runtime-retry]').waitFor();
  check(await offline.locator('[data-role-enable]').count()===2,'file view keeps role choices visible offline');
  check(await offline.locator('[data-role-enable]').first().isDisabled()&&await offline.locator('.runtime-toolbar h2').textContent()==='等待角色选择','file view waits for local server');
  check((await offline.locator('.runtime-message').textContent()).includes('本机工作台'),'offline entry explains how to start');
  await offline.close();
  const bad=Buffer.from('{"original":"saved selection needs repair"}');fs.writeFileSync(state,bad);
  server=await start(state);const recovery=await browser.newPage();await recovery.goto(server.url+'/#roles');
  await recovery.locator('[data-role-recover=xiaozhi-e145]').waitFor();
  check(await recovery.locator('[data-role-enable]').first().isDisabled(),'invalid file requires explicit recovery');
  await recovery.locator('[data-role-recover=xiaozhi-e145]').click();
  await recovery.waitForFunction(()=>DeskBotStudio.runtimeRoles().runtime?.activation?.role_id==='xiaozhi-e145'&&!DeskBotStudio.runtimeRoles().working);
  const backups=fs.readdirSync(path.dirname(state)).filter(name=>name.startsWith('state.previous-'));
  check(backups.length===1&&fs.readFileSync(path.join(path.dirname(state),backups[0])).equals(bad),'original selection kept during recovery');
  await recovery.close();
  fs.writeFileSync(path.join(qa,'browser-report.json'),JSON.stringify({checks,errors,device_commands:commands,hardware_accessed:false},null,2)+'\n');
  console.log(JSON.stringify({checks,errors,hardware_accessed:false}));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();await stop();});
