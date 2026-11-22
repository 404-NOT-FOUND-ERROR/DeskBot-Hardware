'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),qa=path.join(root,'tmp/deskbot-studio-e3f8-20261111/qa');
const python=process.env.DESKBOT_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe';
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let browser,server;
async function start(){
  const folder=fs.mkdtempSync(path.join(root,'tmp/style-e3f8-'));
  const child=spawn(python,['scripts/serve-deskbot-studio-e3f8.py','--port','0','--state-path',path.join(folder,'state.json')],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let output='';child.stdout.on('data',part=>output+=part);child.stderr.on('data',part=>output+=part);
  const limit=Date.now()+15000;
  while(Date.now()<limit&&!/http:\/\/127\.0\.0\.1:\d+/.test(output)&&child.exitCode===null)await wait(50);
  const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);assert.ok(match,output);return {child,url:match[0]};
}
async function stop(){if(!server)return;server.child.kill();await new Promise(resolve=>server.child.once('exit',resolve));server=null;}
(async()=>{
  fs.mkdirSync(qa,{recursive:true});server=await start();
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  const errors=[],deviceRequests=[];let checks=0;const check=(ok,label)=>{assert.ok(ok,label);checks++;};
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(/\/api\/(studio\/command|body\/connect)/.test(request.url()))deviceRequests.push(request.url());});
  const runtime=()=>page.evaluate(()=>DeskBotStudio.runtimeRoles());
  const waitRole=id=>page.waitForFunction(id=>{const s=DeskBotStudio.runtimeRoles();return !s.working&&!s.resolving&&s.runtime?.activation?.role_id===id&&s.result?.activation?.role_id===id;},id);
  async function settled(){
    await page.waitForFunction(()=>!document.documentElement.matches(':active-view-transition'));
    await page.evaluate(()=>Promise.all(document.getAnimations().filter(a=>['studio-arrive','studio-leave','page-enter'].includes(a.animationName)).map(a=>a.finished.catch(()=>{}))));
  }
  async function navigate(route){
    await page.locator(`#main-nav [data-view=${route}]`).click();
    await page.locator(`#main-nav [data-view=${route}][aria-current=page]`).waitFor();
    await settled();
  }

  await page.goto(server.url+'/#roles');await waitRole('xiaozhi-e145');
  const plan=await page.evaluate(()=>JSON.parse(document.querySelector('#role-pack-data').textContent));
  check(plan.projected_bytes===7229956&&plan.partition_bytes===8388608,'capacity projection is manifest-backed');
  check((await page.locator('.pack-stats strong').nth(2).textContent()).startsWith('6.90'),'dual-role capacity display');
  check((await page.locator('.pack-footnote').textContent()).includes('107.5 KiB'),'remaining soft-budget display');
  check((await page.locator('.pack-scenarios').textContent()).includes('回读通过'),'sync steps distinguish install and activation');
  check(await page.locator('[data-role-enable]').nth(0).locator('.runtime-choice-check').textContent()==='电脑当前','computer choice is not called device activation');
  check((await page.locator('.runtime-choice').nth(1).textContent()).includes('尚未安装'),'candidate role installation status is explicit');
  check(await page.locator('.runtime-stage').count()===1,'role preview visible');
  check(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor)==='rgb(243, 245, 249)','light node-workbench background');
  await page.screenshot({path:path.join(qa,'style-roles-desktop.png'),fullPage:true});

  const routes=['overview','editor','library','roles','connection','publish'];
  for(const route of routes){
    await navigate(route);
    check(await page.locator('#view-content').count()===1,'route '+route);
    check(await page.locator('#workspace').isVisible(),'workspace visible '+route);
    await page.screenshot({path:path.join(qa,'style-'+route+'-desktop.png'),fullPage:true});
  }
  check(await page.locator('.role-pack-plan').count()===1,'capacity explanation also appears on publish route');
  await page.locator('#main-nav [data-view=roles]').click();await waitRole('xiaozhi-e145');
  await page.keyboard.press('Tab');await page.locator('[data-role-semantic=happy]').focus();
  check(await page.locator('[data-role-semantic=happy]').evaluate(el=>el.matches(':focus-visible')),'keyboard focus remains visible');
  await page.keyboard.press('Enter');
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='happy');
  check(await page.locator('[data-role-semantic=happy]').evaluate(el=>el===document.activeElement),'semantic selection retains keyboard focus');

  await page.locator('[data-role-enable=grok-round-personal-v1]').click();await waitRole('grok-round-personal-v1');
  await page.locator('[data-role-semantic=happy]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='happy');
  await settled();await page.screenshot({path:path.join(qa,'style-grok-desktop.png'),fullPage:true});
  let delayed=false;
  await page.route('**/api/roles/expression',async route=>{
    if(route.request().postDataJSON().semantic==='confused'&&!delayed){delayed=true;const response=await route.fetch();await wait(550);await route.fulfill({response});}
    else await route.continue();
  });
  const scene=await page.locator('#runtime-expression').elementHandle(),frameBefore=await page.locator('.runtime-screen').getAttribute('data-frame');
  await page.evaluate(()=>{window.styleFlowBefore=document.getAnimations().find(a=>a.animationName==='studio-current');});
  await page.locator('[data-role-semantic=confused]').click();await wait(80);
  check((await runtime()).resolving,'new expression is visibly resolving');
  check(await scene.evaluate(node=>node.isConnected),'same role retains the exact stage and node DOM');
  check(await page.evaluate(()=>document.getAnimations().includes(window.styleFlowBefore)),'flow animation instance survives semantic selection');
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='confused');
  await page.unroute('**/api/roles/expression');
  check((await page.locator('.runtime-screen').getAttribute('data-frame'))!==frameBefore,'preview clock continued during resolution');
  check(await page.locator('#runtime-expression [data-runtime-node]').count()===3,'phase nodes remain in place');

  await page.route('**/api/roles/expression',route=>{
    if(route.request().postDataJSON().semantic==='sleepy')route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'模拟表情解析失败'})});
    else route.continue();
  });
  await page.locator('[data-role-semantic=sleepy]').click();
  await page.waitForFunction(()=>!!DeskBotStudio.runtimeRoles().previewError);
  check((await runtime()).result.expression.semantic==='confused','failed resolve preserves last successful expression');
  check(await page.locator('.runtime-stage').isVisible()&&await page.locator('#runtime-expression canvas').count()===4,'failed resolve keeps the full visual chain');
  check((await page.locator('#runtime-message').textContent()).includes('模拟表情解析失败'),'failure is explained beside preview');
  await page.unroute('**/api/roles/expression');
  await page.locator('[data-runtime-retry]').click();
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='sleepy'&&!DeskBotStudio.runtimeRoles().resolving);
  await page.locator('[data-runtime-pause]').click();
  check(await page.evaluate(()=>document.getAnimations().find(a=>a.animationName==='studio-current')?.playState)==='paused','pause also pauses flow decoration');
  await page.locator('[data-runtime-pause]').click();

  let activationDelayed=false;
  await page.route('**/api/roles/activate',async route=>{
    const reply=await route.fetch();
    if(!activationDelayed){activationDelayed=true;await wait(500);}
    await route.fulfill({response:reply});
  });
  const oldStage=await page.locator('#runtime-expression').elementHandle();
  await page.locator('[data-role-enable=xiaozhi-e145]').click();await wait(80);
  check(await oldStage.evaluate(node=>node.isConnected),'old role preview stays visible during role save');
  await waitRole('xiaozhi-e145');await page.unroute('**/api/roles/activate');
  check(await page.locator('#runtime-expression .runtime-single .runtime-node').count()===1,'new role preview switches as a complete scene');
  check((await page.locator('.runtime-now').textContent()).includes('原有角色'),'new scene label matches its owner');
  await page.screenshot({path:path.join(qa,'style-roles-desktop.png'),fullPage:true});

  const darkToggle=page.locator('#theme-toggle');
  await darkToggle.click();check(await page.locator('body').getAttribute('data-theme')==='dark','dark theme remains available');
  check(await page.evaluate(()=>getComputedStyle(document.body).colorScheme)==='dark','dark theme color scheme applied');
  await page.evaluate(()=>Promise.all(document.getAnimations().filter(a=>a instanceof CSSTransition&&!a.effect?.target?.closest?.('.runtime-progress')).map(a=>a.finished.catch(()=>{}))));
  await page.screenshot({path:path.join(qa,'style-dark-desktop.png'),fullPage:true});
  await darkToggle.click();check(await page.locator('body').getAttribute('data-theme')!=='dark','light theme restored');
  await navigate('editor');
  await page.locator('[data-action=select-semantic][data-semantic=eat]').first().click();
  const before=await page.locator('#node-resource>.node-canvas canvas').getAttribute('data-resource');
  await page.locator('[data-action=pick-resource]').click();
  const replacement=before==='winking.eaf'?'Happy.eaf':'winking.eaf';
  await page.locator(`[data-action=choose-resource][data-resource="${replacement}"]`).click();
  check(await page.locator('#node-resource>.node-canvas canvas').getAttribute('data-resource')===replacement,'resource picker still reconnects the active node');
  check(await page.locator('#node-old canvas').getAttribute('data-resource')===before,'old resource remains as a disconnected preview');
  await page.locator('[data-action=undo]').click();
  check(await page.locator('#node-resource>.node-canvas canvas').getAttribute('data-resource')===before,'undo restores the previous resource connection');
  for(const width of [390,320]){
    await page.setViewportSize({width,height:960});
    for(const route of routes){
      await navigate(route);
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal page overflow '+width+' '+route);
    }
    await navigate('roles');await waitRole('xiaozhi-e145');
    await page.locator('[data-role-enable=grok-round-personal-v1]').click();await waitRole('grok-round-personal-v1');await settled();
    await page.screenshot({path:path.join(qa,'style-roles-mobile-'+width+'.png'),fullPage:true});
    await page.locator('[data-role-enable=xiaozhi-e145]').click();await waitRole('xiaozhi-e145');
  }
  await page.locator('#main-nav [data-view=roles]').click();
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>DeskBotStudio.runtimeRoles().paused===true);
  check((await runtime()).paused,'reduced motion pauses expression animation');
  check(await page.locator('[data-role-semantic]').count()===6,'all role controls remain usable');
  check(errors.length===0,'no browser errors '+errors.join('\n'));
  check(deviceRequests.length===0,'style and preview interactions send no device commands');
  const broken=await context.newPage();
  await broken.route('**/role-media/idle.png',route=>route.abort('failed'));
  await broken.goto(server.url+'/#roles');
  await broken.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.activation?.role_id==='xiaozhi-e145');
  await broken.locator('[data-role-semantic=happy]').click();
  await broken.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.expression.semantic==='happy');
  check(await broken.evaluate(()=>!DeskBotStudio.runtimeRoles().error),'inactive avatar failure does not block the current role');
  await broken.locator('[data-role-enable=grok-round-personal-v1]').click();
  await broken.waitForFunction(()=>!!DeskBotStudio.runtimeRoles().previewError);
  check(await broken.locator('[data-runtime-retry]').isVisible(),'active preview media failure has an explicit retry');
  await broken.unroute('**/role-media/idle.png');await broken.locator('[data-runtime-retry]').click();
  await broken.waitForFunction(()=>DeskBotStudio.runtimeRoles().result?.activation?.role_id==='grok-round-personal-v1');
  check(await broken.evaluate(()=>!DeskBotStudio.runtimeRoles().previewError),'retry restores the requested role after media recovers');
  await broken.close();
  await context.close();await stop();
  fs.writeFileSync(path.join(qa,'style-browser-report.json'),JSON.stringify({checks,errors,device_requests:deviceRequests,
    capacity_projection:plan,hardware_accessed:false},null,2)+'\n');
  console.log(JSON.stringify({checks,errors,hardware_accessed:false}));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();await stop();});
