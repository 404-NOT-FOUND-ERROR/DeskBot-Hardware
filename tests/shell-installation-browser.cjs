/* Verify the production UI through real HTTP and the memory ControlLink owner. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts/shell-installation-20261117');
const pause=ms=>new Promise(r=>setTimeout(r,ms)),checks=[],errors=[];
let server,browser,folder,origin;
const check=(v,name)=>{assert.ok(v,name);checks.push(name);};
async function state(){return fetch(origin+'/api/shell-installation/state').then(r=>r.json());}
async function until(test){const end=Date.now()+15000;while(Date.now()<end){if(await test())return;await pause(80);}throw Error('condition timed out');}
(async()=>{
 folder=fs.mkdtempSync(path.join(root,'tmp/installation-browser-'));
 server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_shell_installation_fixture.py',folder,path.join(out,'preview/index.html')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
 for(let i=0;i<300&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
 origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
 browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(origin+'/#shells');await page.waitForSelector('#si-shell');
 await until(async()=>(await page.locator('#si-shell option').count())===3);
 check(await page.locator('.si-card h2').innerText()==='我换好外壳了','manual installation entry is visible');
 check(await page.locator('#si-actor').inputValue()==='same-individual','client identity is retained');
 check(await page.locator('#si-actor').isDisabled(),'bound identity is read only');
 check((await state()).records.length===0,'polling does not confirm an installation');
 await page.locator('#si-shell').selectOption('b');
 await page.locator('[name="shell_name"]').fill('保留我的草稿');
 await page.locator('[name="shell_name"]').focus();await pause(1300);
 check(await page.locator('[name="shell_name"]').inputValue()==='保留我的草稿','installation polling preserves shell draft');
 check(await page.locator('[name="shell_name"]').evaluate(el=>el===document.activeElement),'installation polling preserves focus');
 await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>(await state()).records[0]?.phase==='saved');
 await until(async()=>(await page.locator('.si-card .sc-chip').innerText())==='设备表情已保存');
 check((await state()).records[0].shell_id==='b','selected physical shell is saved');
 check((await state()).device.actual==='old','actual device expression matches selected role');
 check((await state()).records[0].completion_source==='correlated_device_reply','completion uses correlated production receipt');
 check((await fetch(origin+'/__fixture/installation').then(r=>r.json())).activations===1,'one confirmation sends one activation');
 await page.reload();await page.waitForSelector('#si-shell');
 await until(async()=>(await page.locator('.si-card .sc-chip').innerText())==='设备表情已保存');
 check((await state()).records.length===1,'reload keeps existing installation');
 await page.locator('#si-shell').selectOption('a');await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>(await state()).records.length===2&&(await state()).records[1].phase==='saved');
 check((await state()).device.actual==='round','second shell returns to installed round role');
 check(new Set((await state()).records.map(r=>r.character_id)).size===1,'shell replacement preserves the individual');
 const conflictRequests=[];
 await page.locator('#si-shell').selectOption('b');
 await page.route('**/api/shell-installation/confirm',async route=>{
   conflictRequests.push(route.request().postDataJSON());
   if(conflictRequests.length===1)await fetch(origin+'/__fixture/rebind',{method:'POST'});
   const response=await route.fetch();
   if(conflictRequests.length===1)check(response.status()===409,'stale binding receives production conflict');
   await route.fulfill({response});
 });
 await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>conflictRequests.length===1&&await page.locator('[data-si-action="confirm"]').isEnabled());
 check(!(await state()).records.some(r=>r.installation_id===conflictRequests[0].installation_id),'rejected stale binding creates no installation');
 await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>{const s=await state();return s.records.length===3&&s.records[2].phase==='saved';});
 check(conflictRequests[0].installation_id!==conflictRequests[1].installation_id,'confirmed conflict renews request without changing selection');
 check((await state()).records[2].binding.role_id==='profile-a','renewed confirmation uses updated binding');
 await until(async()=>(await page.locator('.si-card .sc-chip').innerText())==='设备表情已保存'&&
   (await page.locator('.si-card .sr-flow strong').first().innerText())==='外壳B');
 await page.unroute('**/api/shell-installation/confirm');
 let blockState=true;const lostRequests=[];
 await page.route('**/api/shell-installation/state',route=>blockState?route.abort():route.continue());
 await page.route('**/api/shell-installation/confirm',async route=>{
   lostRequests.push(route.request().postDataJSON());
   const response=await route.fetch();
   if(lostRequests.length===1)return route.abort('failed');
   blockState=false;await route.fulfill({response});
 });
 await page.locator('#si-shell').selectOption('a');
 await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>lostRequests.length===1&&await page.locator('[data-si-action="confirm"]').isEnabled());
 await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>{const s=await state();return s.records.length===4&&s.records[3].phase==='saved';});
 check(lostRequests.length===2&&lostRequests[0].installation_id===lostRequests[1].installation_id,'lost POST response reuses original installation ID');
 check((await state()).records.filter(r=>r.installation_id===lostRequests[0].installation_id).length===1,'lost response produces one durable installation');
 await page.unroute('**/api/shell-installation/confirm');await page.unroute('**/api/shell-installation/state');
 await until(async()=>(await page.locator('.si-card .sc-chip').innerText())==='设备表情已保存');
 await page.screenshot({path:path.join(out,'desktop.png'),fullPage:false});
 await page.setViewportSize({width:390,height:844});await pause(600);
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile layout fits viewport');
 check(await page.locator('[data-si-action="confirm"]').isVisible(),'mobile confirmation remains available');
 await page.screenshot({path:path.join(out,'mobile.png'),fullPage:false});
 check(errors.length===0,'no browser script errors');
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({status:'passed',checks,errors,scope:'production HTTP and memory device',hardware_accessed:false},null,2)+'\n');
 console.log(JSON.stringify({status:'passed',checks:checks.length}));
})().catch(e=>{fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({status:'failed',checks,errors,error:e.stack},null,2));console.error(e.stack);process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();
 if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}
 if(folder)fs.rmSync(folder,{recursive:true,force:true});
});
