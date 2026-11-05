'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3d1-browser-20261105');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8771',checks=[];
const schema='deskbot.studio_control_link/v1';
let browser;
async function exercise(width=1440,reduced=false){
  const context=await browser.newContext({viewport:{width,height:1100},reducedMotion:reduced?'reduce':'no-preference'});
  const page=await context.newPage(),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/api/**',async route=>{
    const request=route.request(),p=new URL(request.url()).pathname;
    if(request.method()==='POST')posts.push(p);
    let data={};
    if(p==='/api/health')data={ready:true,device_written:false};
    else if(p==='/api/body/ports')data={available:false,reason:'offline_browser_fixture',ports:[]};
    else if(p==='/api/body/state')data={connection:{status:'disabled',port:null},identity:{status:'disconnected',hello:null},events:[],hardware_accessed:false,device_written:false};
    else if(p==='/api/studio/state')data={schema,source:'usb_session',connection_id:null,enabled:false,status:'off',reason:'enable_to_query',target_id:null,boot_id:null,session_id:null,display:null,age_ms:null,can_submit:false,pending:null,receipts:[],local_result:null,last_command:null,frames_sent:0,rejected_frames:0,host_command:null};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/#connection');
  const directory=page.locator('.e3d-directory');
  await directory.waitFor();
  await directory.scrollIntoViewIfNeeded();
  assert.equal(await directory.locator('.e3d-row').count(),29);
  assert.equal(await page.evaluate(()=>DeskBotStudio.directory().live),false);
  assert.equal(await directory.locator('.e3d-source-note b').textContent(),'当前版本的“惊讶” → shocked.eaf');
  assert.match(await directory.locator('.e3d-badges').textContent(),/资源快照.*待接入/);
  assert.equal(await directory.locator('.e3d-preview canvas').count(),2);
  await page.waitForFunction(()=>[...document.querySelectorAll('.e3d-preview canvas')].every(c=>{const p=c.getContext('2d').getImageData(0,0,360,360).data;let n=0;for(let i=0;i<p.length;i+=4)if(p[i]+p[i+1]+p[i+2]>70)n++;return n>1000;}));
  checks.push('directory_and_source_labels_'+width,'installed_and_draft_previews_'+width);
  await directory.locator('[data-e3d-filter="motion"]').click();
  assert.equal(await directory.locator('.e3d-row').count(),18);
  await directory.locator('[data-e3d-select="embarrassed"]').click();
  assert.match(await directory.locator('.e3d-preview').textContent(),/蹭头/);
  await directory.locator('[data-e3d-filter="visual"]').click();
  assert.equal(await directory.locator('.e3d-row').count(),10);
  await directory.locator('[data-e3d-filter="status"]').click();
  assert.equal(await directory.locator('.e3d-row').count(),1);
  await directory.locator('[data-e3d-select="listen"]').click();
  assert.match(await directory.locator('.e3d-preview').textContent(),/语音状态专用/);
  await directory.locator('[data-e3d-filter="all"]').click();
  const input=directory.locator('#e3d-search');await input.fill('shocked.eaf');
  assert.equal(await directory.locator('.e3d-row').count(),2);
  assert.equal(await input.evaluate(e=>document.activeElement===e),true);
  await input.fill('找不到');assert.equal(await directory.locator('.e3d-row').count(),0);
  await input.fill('');await directory.locator('[data-e3d-select="neutral"]').click();
  assert.match(await directory.locator('.e3d-preview-heading').textContent(),/USB 已接入.*待实测/);
  checks.push('motion_visual_status_filters_'+width,'search_empty_and_focus_'+width,'capability_distinct_from_installed_'+width);
  const canvas=await directory.locator('.e3d-preview canvas').first().boundingBox();
  assert.ok(Math.abs(canvas.width-canvas.height)<1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  if(width===1440){
    await directory.locator('[data-e3d-select="surprised"]').click();
    await directory.locator('[data-action="edit-semantic"]').click();
    await page.waitForFunction(()=>DeskBotStudio.state().view==='editor');
    await page.locator('[data-action="pick-resource"]').click();
    await page.locator('dialog [data-action="choose-resource"][data-resource="Happy.eaf"]').click();
    await page.locator('#main-nav [data-view="connection"]').click();
    await page.waitForFunction(()=>DeskBotStudio.state().view==='connection');
    const text=await directory.locator('.e3d-preview').textContent();
    assert.match(text,/shocked.eaf/);assert.match(text,/Happy.eaf/);assert.match(text,/草稿已改接/);
    assert.equal(await page.evaluate(()=>DeskBotStudio.directory().live),false);
    checks.push('node_editor_return','changed_draft_does_not_change_installed_snapshot');
    await page.locator('#theme-toggle').click();
    assert.equal(await page.locator('body').getAttribute('data-theme'),'dark');
    checks.push('dark_theme');
  }
  await directory.scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>{const toast=document.getElementById('toast');return !toast||getComputedStyle(toast).opacity==='0';});
  await directory.screenshot({path:path.join(out,`directory-${width}${reduced?'-reduced':''}.png`)});
  if(reduced){
    const transition=await directory.locator('.e3d-row').first().evaluate(e=>getComputedStyle(e).transitionDuration);
    assert.ok(transition.split(',').every(v=>parseFloat(v)===0));checks.push('reduced_motion');
  }
  assert.deepEqual(errors,[]);assert.deepEqual(posts,[]);
  checks.push('no_script_errors_or_device_posts_'+width,'circular_preview_and_no_overflow_'+width);
  await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try{await exercise();await exercise(390);await exercise(320,true);
    const report={edition:'E3d-1',result:'passed',source:'offline_browser_fixture',checks,checks_passed:checks.length,device_posts:0,hardware_accessed:false,device_written:false};
    fs.writeFileSync(path.join(out,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');
    process.stdout.write(JSON.stringify(report,null,2)+'\n');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
