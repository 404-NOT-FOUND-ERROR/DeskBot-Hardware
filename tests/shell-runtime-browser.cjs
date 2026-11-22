/* Real scheduler + HTTP/UI over explicitly simulated ready bindings and a memory owner. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/shell-runtime-qa'),checks=[],errors=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let browser,server,folder,origin;
function check(value,name){assert.ok(value,name);checks.push(name);}
async function state(){return fetch(origin+'/api/shell-runtime/state').then(r=>r.json());}
async function fixture(value){const r=await fetch(origin+'/__fixture/runtime',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(r.status,200);}
async function until(test,timeout=12000){const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await test())return;await pause(80);}throw Error('condition timed out');}
(async()=>{
 folder=fs.mkdtempSync(path.join(root,'tmp/shell-runtime-browser-'));fs.mkdirSync(out,{recursive:true});
 server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_shell_switch_fixture.py',folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
 for(let i=0;i<400&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
 origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
 browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1460,height:1050},reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(origin+'/#shells');await page.waitForSelector('#sr-project');
 await until(()=>page.locator('[data-sr-action="start"]').isEnabled());
 check((await state()).phase==='stopped','new server defaults stopped');
 check((await fetch(origin+'/__fixture/state').then(r=>r.json())).requests.length===0,'status polling sends no device commands while stopped');
 await page.locator('[name="shell_name"]').fill('未保存的草稿');await page.locator('[name="shell_name"]').focus();await pause(2200);
 check(await page.locator('[name="shell_name"]').inputValue()==='未保存的草稿','runtime polling preserves shell draft');
 check(await page.locator('[name="shell_name"]').evaluate(el=>document.activeElement===el),'runtime polling preserves editor focus');
 await fixture({motion:null});await page.locator('[data-sr-action="start"]').click();
 await until(()=>page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('等待转动状态')));
 check((await state()).enabled,'start action reaches persistent scheduler');
 check((await fetch(origin+'/__fixture/state').then(r=>r.json())).requests.every(c=>!c.operation.startsWith('role.activate.')),'unknown movement holds activation');
 check(await page.locator('[data-sr-action="stop"]').isEnabled(),'pause control available while running');
 await fixture({motion:false});
 await until(async()=>(await state()).runtime?.adapter?.attempt?.status==='saved');
 await until(()=>page.locator('.sr-receipt').innerText().then(v=>v.includes('已保存')));
 check(await page.locator('#shell-runtime-root .sr-flow').innerText().then(v=>v.includes('桌宠首帧')&&v.includes('桌宠保存')),'recognition target first-frame and save appear separately');
 await page.locator('[data-sr-action="stop"]').click();await until(async()=>!(await state()).enabled);
 check((await state()).phase==='stopped','pause persists in server state');
 await page.reload();await page.waitForSelector('#sr-project');
 check((await state()).phase==='stopped','reloaded page retains paused state');
 await page.locator('[data-sr-action="start"]').click();await until(async()=>(await state()).enabled);
 await page.goto(origin+'/#characters');const ticks=(await state()).ticks;await pause(1200);
 check((await state()).ticks>ticks,'background scheduler continues outside shell page');
 await page.goto(origin+'/#shells');await page.waitForSelector('#sr-project');
 await fixture({config_edit:true});await until(async()=>(await state()).reason==='config_updated');
 await until(()=>page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('配置已更新')));
 check(!(await state()).enabled,'configuration edit pauses new activations');
 await page.route('**/api/shell-runtime/start',r=>r.fulfill({status:503,json:{error:'服务暂时不可用'}}));
 await page.locator('[data-sr-action="start"]').click();await until(()=>page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('服务暂时不可用')));
 check(await page.locator('[data-sr-action="start"]').isEnabled(),'failed start restores button');
 await page.unroute('**/api/shell-runtime/start');
 await until(()=>page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('配置已更新')));
 check(!await page.locator('#shell-runtime-root .sr-note').getAttribute('data-error').then(v=>v==='true'),'successful poll clears transient error');
 const latest=await state();
 await page.route('**/api/shell-runtime/start',r=>r.fulfill({status:409,json:{error:'运行状态已更新，请读取后重试',state:latest}}));
 await page.locator('[data-sr-action="start"]').click();await until(()=>page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('运行状态已更新')));
 check(await page.locator('[data-sr-action="start"]').isEnabled(),'version conflict adopts latest state and restores controls');
 await page.unroute('**/api/shell-runtime/start');
 await fixture({profile:0,fail_save:true});await page.locator('[data-sr-action="start"]').click();
 await until(async()=>(await state()).can_retry);await until(()=>page.locator('[data-sr-action="retry"]').isEnabled());
 check((await state()).runtime.adapter.attempt.first_frame,'save failure preserves completed first frame');
 await page.locator('[data-sr-action="retry"]').click();await until(async()=>(await state()).runtime?.adapter?.attempt?.status==='saved');
 check(!(await state()).can_retry,'explicit retry finishes saving through same production service');
 await page.locator('[data-sr-action="stop"]').click();await until(async()=>!(await state()).enabled);
 await fixture({ready:false});await until(()=>page.locator('[data-sr-action="start"]').isDisabled());
 check(await page.locator('#shell-runtime-root .sr-note').innerText().then(v=>v.includes('先完成采样验证')),'incomplete registration gives clear next action');
 await page.screenshot({path:path.join(out,'scheduler-desktop.png'),fullPage:true});
 for(const width of [820,390]){await page.setViewportSize({width,height:1050});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow at '+width);}
 await page.screenshot({path:path.join(out,'scheduler-mobile.png'),fullPage:true});
 check(errors.length===0,errors.join('\n')||'no browser errors');
 const result={checks:checks.length,passed:checks,browser_errors:errors,hardware_accessed:false,memory_device:true};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();server?.kill();await pause(700);if(folder)fs.rmSync(folder,{recursive:true,force:true});});
