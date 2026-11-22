'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=process.env.DESKBOT_EXPRESSION_BROWSER_OUTPUT
  ? path.resolve(process.env.DESKBOT_EXPRESSION_BROWSER_OUTPUT)
  : path.join(root,'artifacts/client-expression-loop-4b1b-20261116');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let browser,server,folder;const checks=[],errors=[];
function check(value,name){assert.ok(value,name);checks.push(name);}
(async()=>{
  fs.mkdirSync(out,{recursive:true});folder=fs.mkdtempSync(path.join(root,'tmp/4b1b-browser-'));
  server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_shell_workbench_fixture.py',folder],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let log='';server.stdout.on('data',data=>log+=data);server.stderr.on('data',data=>log+=data);
  for(let i=0;i<200&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
  const origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1280,height:980},reducedMotion:'reduce'});
  page.on('pageerror',error=>errors.push(error.stack));
  const control={enabled:true,target_id:'test-device',boot_id:'test-boot',session_id:'test-session',can_submit:true,
    roles:{status:'current',age_ms:30,reply:{loaded:true,roles:['round'],active_index:0,current_index:0,target_index:0,
      target_id:'test-device',boot_id:'test-boot',session_id:'test-session'}}};
  const state={schema:'deskbot.client_presentation/v1',device_id:'test-device',commands:[],inputs:[]};
  let ready=false,mode='normal',body=null;const posted=[];
  const fulfill=(route,value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  await page.route('**/api/studio/state',route=>fulfill(route,ready?control:{}));
  await page.route('**/api/client-presentation/state',route=>fulfill(route,state));
  await page.route('**/api/client-presentation/chat',route=>{
    body=route.request().postDataJSON();posted.push(body);
    if(mode==='lost'){mode='normal';return fulfill(route,{error:'client_unavailable'},503);}
    if(mode==='reject')return fulfill(route,{error:'binding_unavailable'},409);
    state.inputs=[{event_id:body.event_id,status:'completed'}];
    state.commands=[{command:{source_event_id:'reply-'+body.event_id,payload:{expression:body.message.includes('开心')?'happy':'neutral'}},phase:'claimed'}];
    return fulfill(route,{accepted:true,turn:{reply:'<img src=x onerror=alert(1)> 原始回应'}});
  });
  await page.goto(origin+'/client-expression');
  check(await page.locator('#greet').isDisabled(),'unavailable device disables input');
  ready=true;await page.waitForFunction(()=>!document.querySelector('#greet').disabled);
  await page.locator('#greet').click();
  await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('等待桌宠'));
  check(posted.length===1&&posted[0].message==='你好，和你打个招呼。','one click uses original greeting input');
  check(await page.locator('#greet').isDisabled(),'pending output blocks another input');
  check(await page.locator('#reply img').count()===0,'client reply renders as text');
  state.commands[0].phase='prepared';await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('等待设备回执'));
  check(true,'prepared state waits for physical receipt');
  state.commands[0].ack={status:'completed'};
  await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('交给播放器'));
  check((await page.locator('#result').textContent()).includes('请观察'),'dispatch asks for visual observation');
  await page.waitForFunction(()=>!document.querySelector('#happy').disabled);
  mode='lost';await page.locator('#happy').click();await page.waitForSelector('#retry:visible');
  check(await page.locator('#greet').isDisabled(),'ambiguous HTTP reply preserves pending input');
  const original=structuredClone(posted.at(-1));await page.locator('#retry').click();
  await page.waitForFunction(()=>document.querySelector('#reply').textContent.includes('原始回应'));
  check(JSON.stringify(posted.at(-1))===JSON.stringify(original),'retry reuses original event correlation and message');
  await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('开心'));
  state.commands[0].ack={status:'failed',error:{code:'expired'}};
  await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('expired'));
  check(true,'terminal failure is shown');
  await page.waitForFunction(()=>!document.querySelector('#greet').disabled);
  mode='lost';await page.locator('#greet').click();await page.waitForSelector('#retry:visible');
  const delayed=structuredClone(posted.at(-1));
  state.inputs=[{event_id:delayed.event_id,status:'completed'}];state.commands=[];
  await page.waitForFunction(()=>document.querySelector('#reply').textContent.includes('原始回应'));
  check(JSON.stringify(posted.at(-1))===JSON.stringify(delayed),'completed slow reply resumes automatically using the same event');
  state.commands[0].ack={status:'completed'};
  await page.waitForFunction(()=>!document.querySelector('#greet').disabled);
  mode='reject';await page.locator('#greet').click();
  state.inputs=[{event_id:body.event_id,status:'turn_recovery_pending'}];state.commands=[];
  await page.waitForFunction(()=>document.querySelector('#result').textContent.includes('turn_recovery_pending'));
  check(await page.locator('#greet').isEnabled(),'explicit input failure does not leave buttons stuck');
  control.roles.age_ms=4000;await page.waitForFunction(()=>document.querySelector('#greet').disabled);
  check(true,'stale role disables new input');control.roles.age_ms=30;
  for(const width of [1280,390]){
    await page.setViewportSize({width,height:980});
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page fits ${width}px`);
    await page.screenshot({path:path.join(out,`acceptance-${width}.png`),fullPage:true});
  }
  check(errors.length===0,'no browser script errors');
  fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({status:'passed',checks,hardware_accessed:false},null,2)+'\n');
  process.stdout.write(JSON.stringify({status:'passed',checks:checks.length,hardware_accessed:false})+'\n');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server){server.kill();await new Promise(resolve=>server.once('close',resolve));}
  if(folder)fs.rmSync(folder,{recursive:true,force:true});
});
