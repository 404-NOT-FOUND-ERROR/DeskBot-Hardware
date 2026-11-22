/* Daily connection card against the production service and a memory endpoint. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/body-network-workbench-20261121');
const checks=[],errors=[];let server,browser,folder;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function check(value,label){assert.ok(value,label);checks.push(label);}
(async()=>{
  fs.mkdirSync(out,{recursive:true});folder=fs.mkdtempSync(path.join(root,'tmp/network-browser-'));
  server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_body_network_fixture.py',folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
  for(let i=0;i<400&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
  const origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.stack));
  await page.goto(origin+'/#connection');await page.waitForFunction(()=>DeskBotStudio.bodyNetwork().data?.available);
  try{await page.waitForFunction(()=>document.querySelector('#bn-port')?.value==='COM4');}
  catch(error){console.error(JSON.stringify({errors,observation:await page.evaluate(()=>DeskBotStudio.observation()),network:await page.evaluate(()=>DeskBotStudio.bodyNetwork()),card:await page.locator('#body-network-slot').innerHTML()}));throw error;}
  check((await fetch(origin+'/__fixture/network').then(r=>r.json())).commands.length===0,'viewing and polling never opens maintenance');
  check(!(await page.locator('.bn-advanced').getAttribute('open')),'USB engineering tools start folded');
  await page.locator('[data-bn-action=get]').click();
  await page.waitForFunction(()=>DeskBotStudio.bodyNetwork().data?.task?.status==='completed');
  check(await page.locator('#bn-host').inputValue()==='10.0.0.9','reads actual saved host into form');
  await page.locator('#bn-host').fill('10.0.0.7');await pause(2600);
  check(await page.locator('#bn-host').inputValue()==='10.0.0.7','polling preserves edits');
  check(await page.locator('#bn-host').evaluate(el=>el===document.activeElement),'polling preserves input focus');
  await page.locator('#bn-save').click();
  await page.waitForFunction(()=>DeskBotStudio.bodyNetwork().data?.settings?.revision===1&&DeskBotStudio.bodyNetwork().data?.task?.status==='completed');
  check(await page.locator('#bn-feedback').innerText().then(t=>t.includes('已采用')),'shows device adoption separately');
  check((await fetch(origin+'/__fixture/network').then(r=>r.json())).commands.length===2,'one save produces one maintenance invocation');
  await page.locator('#bn-host').fill('10.0.0.8');
  await fetch(origin+'/__fixture/conflict',{method:'POST',body:'{}'});
  await page.locator('#bn-save').click();
  await page.waitForFunction(()=>DeskBotStudio.bodyNetwork().data?.task?.status==='failed');
  check(await page.locator('#bn-host').inputValue()==='10.0.0.8','conflict preserves address for correction');
  check(await page.locator('#bn-feedback').innerText().then(t=>t.includes('重新读取')),'conflict offers clear next action');
  await page.evaluate(()=>location.hash='roles');await page.waitForFunction(()=>DeskBotStudio.state().view==='roles');
  await page.evaluate(()=>location.hash='connection');await page.waitForSelector('#bn-host');
  check(await page.locator('#bn-host').inputValue()==='10.0.0.8','navigation preserves form draft');
  await page.reload();await page.waitForFunction(()=>DeskBotStudio.bodyNetwork().data?.settings?.revision===1);
  check(await page.locator('#bn-host').inputValue()==='10.0.0.7','reopening shows last actual read rather than rejected draft');
  for(const width of [1440,390,320])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:width===1440?1150:844});await page.evaluate(t=>document.body.dataset.theme=t,theme);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),`connection ${theme} fits ${width}px`);
    await page.screenshot({path:path.join(out,`network-${theme}-${width}.png`),fullPage:width===1440});
  }
  check(errors.length===0,'no browser runtime errors');
  const report={passed:true,checks,hardware_accessed:false};fs.writeFileSync(path.join(out,'browser-tests.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();server?.kill();});
