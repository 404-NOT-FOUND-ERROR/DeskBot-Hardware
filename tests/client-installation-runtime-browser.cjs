/* Real production installation UI plus original client HTTP/SQLite, isolated. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=process.env.DESKBOT_RUNTIME_OUTPUT;
if(!out)throw Error('explicit_output_required');
let server,browser,folder,origin;
const checks=[],errors=[],pause=ms=>new Promise(done=>setTimeout(done,ms));
function check(value,name){assert.ok(value,name);checks.push(name);}
async function until(fn){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await pause(70);}throw Error('condition_timeout');}
(async()=>{
 folder=fs.mkdtempSync(path.join(root,'tmp/installation-runtime-browser-'));
 server=spawn(process.env.DESKBOT_RUNTIME_PYTHON,['tests/serve_client_installation_runtime_fixture.py',folder,path.join(out,'page/index.html')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
 await until(async()=>/http:\/\/127.0.0.1:\d+/.test(log)||server.exitCode!==null);
 origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
 browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(origin+'/#shells');await page.waitForSelector('#si-shell');
 await until(async()=>(await page.locator('#si-shell option').count())===3);
 check(await page.locator('#si-actor').inputValue()==='shaping-001','original individual is retained');
 check((await page.locator('[data-si-client-sync]').innerText()).includes('等待'),'unconfirmed install waits for sync');
 const state=()=>fetch(origin+'/api/shell-installation/state').then(r=>r.json());
 for(const shell of ['a','b','a']){
  await page.locator('#si-shell').selectOption(shell);await page.locator('[data-si-action="confirm"]').click();
  await until(async()=>{const s=await state();return s.client_sync.verified&&s.records.find(r=>r.installation_id===s.current).shell_id===shell;});
  await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端已同步');
  const s=await state(),current=await fetch(origin+'/__fixture/current').then(r=>r.json());
  check(current.current.installation_id===s.current,`${shell} client and workbench share installation ID`);
  check(s.client_sync.last_receipt.installation_id===s.current,`${shell} current receipt belongs to current installation`);
 }
 await page.locator('[name="shell_name"]').fill('保留草稿');await page.locator('[name="shell_name"]').focus();await pause(1300);
 check(await page.locator('[name="shell_name"]').inputValue()==='保留草稿','sync polling preserves editing draft');
 check(await page.locator('[name="shell_name"]').evaluate(el=>el===document.activeElement),'sync polling preserves draft focus');
 let blockRead=true;
 await page.route('**/api/shell-installation/state',route=>blockRead?route.abort():route.continue());
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())!=='客户端已同步');
 check((await page.locator('[data-si-client-sync]').innerText()).includes('等待'),'workbench read failure invalidates old verified UI');
 blockRead=false;await page.unroute('**/api/shell-installation/state');
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端已同步');
 await fetch(origin+'/__fixture/offline',{method:'POST'});
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端待重试');
 check(!(await state()).client_sync.verified,'offline client invalidates current verification');
 await page.locator('#si-shell').selectOption('b');await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>(await state()).records.at(-1).phase==='saved');
 check(!(await state()).client_sync.verified,'new device save is separate from client reception');
 await fetch(origin+'/__fixture/online',{method:'POST'});
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端已同步');
 check((await state()).client_sync.verified,'client recovery resumes original installation');
 let lost=true;
 await page.route('**/api/shell-installation/state',route=>lost?route.abort():route.continue());
 let posted=false;
 await page.route('**/api/shell-installation/confirm',async route=>{await route.fetch();await route.abort();posted=true;});
 await page.locator('#si-shell').selectOption('a');await page.locator('[data-si-action="confirm"]').click();
 await until(async()=>posted&&(await page.locator('[data-si-client-sync]').innerText())!=='客户端已同步');
 check((await page.locator('[data-si-client-sync]').innerText()).includes('等待'),'uncertain confirm response waits for fresh state');
 lost=false;await page.unroute('**/api/shell-installation/confirm');await page.unroute('**/api/shell-installation/state');
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端已同步');
 await page.reload();await page.waitForSelector('[data-si-client-sync]');
 await until(async()=>(await page.locator('[data-si-client-sync]').innerText())==='客户端已同步');
 check((await state()).records.length===5,'page reload does not create installation');
 await page.screenshot({path:path.join(out,'desktop.png')});
 await page.setViewportSize({width:390,height:844});await pause(400);
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile view fits viewport');
 check(await page.locator('[data-si-client-sync]').isVisible(),'mobile client result remains visible');
 await page.screenshot({path:path.join(out,'mobile.png')});
 check(errors.length===0,'no browser script errors');
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({status:'passed',checks,errors,scope:'original client SQLite and production UI; memory device'},null,2)+'\n');
 console.log(JSON.stringify({status:'passed',checks:checks.length}));
})().catch(e=>{fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({status:'failed',checks,errors,error:e.stack},null,2));console.error(e.stack);process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();
 if(server&&server.exitCode===null){server.kill();await new Promise(done=>server.once('exit',done));}
 if(folder)fs.rmSync(folder,{recursive:true,force:true});
});
