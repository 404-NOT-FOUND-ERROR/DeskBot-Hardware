'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {chromium}=require(process.argv[2]||'playwright');
(async()=>{
  const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
  try{
    const report=JSON.parse(await fs.readFile('artifacts/layered-face-20261122/report.json','utf8'));
    const page=await browser.newPage({viewport:{width:1360,height:1000}}),sent=[],errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    let state={enabled:true,can_submit:true,connection_id:'connection-test',target_id:'device-test',boot_id:'boot-test',session_id:'session-test',
      host_command:null,pending:null,receipts:[],roles:{status:'current',reply:{loaded:true,graph_sha256:report.graph_sha256}},
      display:{expression_semantics:['happy','crying','neutral']}};
    await page.route('**/api/studio/state',route=>route.fulfill({json:state}));
    await page.route('**/api/studio/command',async route=>{
      const command=route.request().postDataJSON();sent.push(command);
      if(command.operation!=='read_roles')state.receipts=[{target_id:state.target_id,boot_id:state.boot_id,session_id:state.session_id,
        request_id:command.command_id,status:'dispatched'}];
      await route.fulfill({json:state});
    });
    await page.goto('http://127.0.0.1:8782/layered-face/');
    await page.waitForFunction(()=>!document.querySelector('#device-play')?.disabled);
    await page.locator('[data-expression="crying"]').click();
    await page.locator('#device-play').click();
    await page.waitForFunction(()=>!document.querySelector('#device-cancel').disabled);
    await page.locator('#device-cancel').click();
    await page.waitForFunction(()=>!document.querySelector('#device-play').disabled);
    assert.deepEqual(sent.filter(c=>c.operation!=='read_roles').map(c=>c.operation),['crying','neutral']);
    assert(sent.every(c=>c.boot_id==='boot-test'&&c.session_id==='session-test'));
    assert.equal(new Set(sent.map(c=>c.command_id)).size,sent.length);
    state.roles.reply.graph_sha256='a'.repeat(64);
    await page.waitForFunction(()=>document.querySelector('#device-play').disabled);
    assert.match(await page.locator('#device-status').textContent(),/角色与此预览不同/);
    state.roles.reply.graph_sha256=report.graph_sha256;state.can_submit=false;
    await page.waitForFunction(()=>document.querySelector('#device-status').textContent.includes('回主页待机'));
    assert.equal(await page.locator('#device-cancel').isDisabled(),true);
    state.enabled=false;state.session_id=null;
    await page.waitForFunction(()=>document.querySelector('#device-status').textContent.includes('启用状态查询'));
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    const result={selected_semantic:true,device_cancel_uses_neutral:true,fresh_identity:true,unique_requests:true,
      graph_mismatch_blocked:true,application_busy_blocked:true,disconnected_blocked:true,mobile:true,hardware_accessed:false,errors};
    await fs.writeFile('artifacts/layered-face-20261122/live-controls-browser.json',JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
