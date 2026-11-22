'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3f9d-20261112/qa');
const reference=fs.readFileSync(path.join(root,'tests/studio-common-e3f7-browser.cjs'),'utf8');
const html=fs.readFileSync(path.join(root,'tmp/deskbot-studio-e3f9d-20261112/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">(.*?)<\/script>/s)[1]);
const device='device-'+'a'.repeat(32),boot='boot-'+'b'.repeat(32),session='session-'+'b'.repeat(32)+'-1';
const semantics=['neutral','embarrassed','eat','happy','sad','sleepy','confused','surprised'];
// Share the established body/identity fixture; the server still uses production HTTP.
const ready=eval(reference.match(/const ready=\(\)=>\((.*?)\);\r?\nconst body/s)[0].split(';\nconst body')[0].replace(/^const ready=/,'').replace(/;\r?\nconst body$/,''));
const body=eval(reference.match(/const body=\(\)=>\((.*?)\);\r?\nasync function fixture/s)[0].replace(/^const body=/,'').replace(/;\r?\nasync function fixture$/,''));
const pause=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,temp,origin;const errors=[],checks=[];
function check(value,name){assert.ok(value,name);checks.push(name);}
async function fixture(value){const r=await fetch(origin+'/__fixture/state',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Fixture':'memory'},body:JSON.stringify(value)});assert.equal(r.status,200);}
async function requests(){return (await(await fetch(origin+'/__fixture/state')).json()).requests;}
async function state(){return await(await fetch(origin+'/api/studio/state')).json();}
(async()=>{
  fs.mkdirSync(out,{recursive:true});temp=fs.mkdtempSync(path.join(root,'tmp/device-role-browser-'));
  server=spawn((process.env.DESKBOT_PYTHON||'python'),['tests/serve_studio_device_roles_e3f9d_fixture.py',temp],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let log='';server.stdout.on('data',x=>log+=x);server.stderr.on('data',x=>log+=x);
  for(let i=0;i<600&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
  origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXECUTABLE});
  const page=await browser.newPage({viewport:{width:1480,height:1050},reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.stack));
  await fixture({live:ready(),body:body()});
  await page.goto(origin+'/#roles');
  await page.waitForFunction(()=>document.querySelector('[data-device-role-index="0"]')&&!document.querySelector('[data-device-role-index="0"]').disabled);
  check(await page.locator('.edition').textContent()==='E3f·9d','current edition');
  check((await requests()).filter(r=>r.operation==='read_roles').length===1,'one read per new session');
  check((await requests()).every(r=>!r.operation.startsWith('role.activate.')),'opening page never activates');
  check(await page.locator('[data-device-role-index]').count()===2,'two installed choices');
  check(await page.locator('[data-device-role-play]').count()===6,'six daily play buttons');
  for(const index of [0,1,0]){
    const before=(await requests()).length;
    await page.locator(`[data-device-role-index="${index}"]`).click();
    await page.waitForFunction(i=>DeskBotStudio.control().device?.roles?.reply?.saved_index===i,index);
    const sent=(await requests()).slice(before).filter(r=>r.operation.startsWith('role.activate.'));
    check(sent.length===1&&sent[0].operation===`role.activate.${index}.`+'a'.repeat(64),'one correlated activation '+index+' '+before);
  }
  for(const semantic of ['happy','sad','embarrassed']){
    await page.locator(`[data-device-role-play="${semantic}"]`).click();
    await page.waitForFunction(()=>!DeskBotStudio.control().busy);
    check((await requests()).at(-1).operation===semantic,'play '+semantic);
  }
  const beforeEdit=(await requests()).length;
  await page.locator('[data-pack-role="grok-round-personal-v1"]').click();
  await page.locator('[data-pack-semantic="sad"]').click();
  check((await requests()).length===beforeEdit,'draft editing does not activate device');
  let live=await state();
  live.roles.reply.activation={request_id:'rq-test-5',status:'switching'};live.roles.reply.target_index=1;live.roles.reply.active_index=0;live.roles.reply.phase='exit';
  await fixture({live});
  await page.waitForFunction(()=>document.querySelector('#device-roles').textContent.includes('正在完成过渡动画'));
  check(await page.locator('[data-device-role-play="happy"]').isDisabled(),'transition blocks play');
  check((await page.locator('.device-role-facts').textContent()).includes('圆团')&&(await page.locator('.device-role-facts').textContent()).includes('原有角色'),'target differs from actual');
  live.roles.reply.activation.status='cancelled';live.roles.reply.reason='expression_interrupted';
  await fixture({live});await page.waitForFunction(()=>document.querySelector('#device-roles').textContent.includes('其他表情已接管'));
  check(await page.locator('[data-device-role-index="0"]').isEnabled(),'interrupted selection can retry');
  live.roles.reply.activation.status='applied';live.roles.reply.save_state='failed';live.roles.reply.reason='first_frame_executed';
  await fixture({live});await page.waitForFunction(()=>document.querySelector('#device-roles').textContent.includes('角色已切换，待保存'));
  check(await page.locator('[data-device-role-index="0"]').isEnabled(),'save failure allows retry');
  await page.screenshot({path:path.join(out,'device-role-desktop.png'),fullPage:true});
  await page.setViewportSize({width:820,height:1100});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'narrow viewport fits');
  await page.screenshot({path:path.join(out,'device-role-narrow.png'),fullPage:true});
  live.roles={status:'stale',reply:null,age_ms:null,pending:null,error:null};
  await fixture({live});await page.waitForFunction(()=>!document.querySelector('[data-device-role-index]'));
  check(await page.locator('[data-device-role-play]').count()===0,'stale facts disable role operations');
  const before=(await requests()).length;await pause(1700);
  check((await requests()).length===before,'stale state never automatically activates');
  check(errors.length===0,errors.join('\n')||'no page errors');
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({result:'passed',checks,errors,hardware_accessed:false},null,2));
  console.log(JSON.stringify({result:'passed',checks:checks.length,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server){server.kill();await new Promise(r=>server.once('exit',r));}
  if(temp&&fs.existsSync(temp))fs.rmSync(temp,{recursive:true,force:true});
});
