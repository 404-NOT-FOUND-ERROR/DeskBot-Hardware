'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e2a-browser-20261104');
const edge=(process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe');
let browser;
async function navigate(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);await page.waitForTimeout(450);}
async function screenshot(page,name){
  await page.evaluate(()=>document.getElementById('toast').classList.remove('show'));
  const height=await page.evaluate(()=>document.documentElement.scrollHeight);
  for(let y=0;y<height;y+=700){await page.evaluate(y=>scrollTo(0,y),y);await page.waitForTimeout(90);}
  await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(180);
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
}
async function noOverflow(page){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page horizontal overflow');}
async function smoke(local){
  const context=await browser.newContext({viewport:{width:1440,height:1040},acceptDownloads:true,reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(!/^(file:|data:|blob:)/.test(r.url()))requests.push(r.url());});
  const relative=(local?'tmp':'artifacts')+'/deskbot-studio-e2a-20261104/index.html';
  await page.goto(pathToFileURL(path.join(root,relative)).href);await page.waitForFunction(()=>window.DeskBotStudio);
  assert.equal(await page.locator('#main-nav a').count(),5);assert.equal(await page.locator('.workflow-rail .flow-stop').count(),6);
  assert.deepEqual(await page.evaluate(()=>DeskBotStudio.catalog()),{semantics:29,assets:19});
  await page.waitForFunction(()=>document.querySelector('.screen').toDataURL().length>2000);await noOverflow(page);
  if(local)await screenshot(page,'overview-desktop');
  await navigate(page,'editor');
  assert.equal(await page.locator('.semantic-item').count(),29);
  assert.match(await page.locator('#node-resource').innerText(),/Happy.eaf/);
  assert.match(await page.locator('#node-action').innerText(),/蹭头/);
  assert.match(await page.locator('.graph-note').innerText(),/动态/);
  await page.locator('[data-action="pick-resource"]').click();await page.locator('[data-action="choose-resource"][data-resource="shy_candidate.eaf"]').click();
  assert.equal(await page.evaluate(()=>DeskBotStudio.snapshot().roles[0].bindings.embarrassed),'shy_candidate.eaf');
  assert.equal(await page.locator('#node-old').count(),1);assert.match(await page.locator('#node-old').innerText(),/Happy.eaf/);
  const circle=await page.locator('#node-resource canvas').boundingBox();assert.ok(Math.abs(circle.width-circle.height)<1,'node preview stays square');
  assert.equal(await page.locator('#graph-connections path').count(),4);
  if(local){await page.waitForFunction(()=>document.querySelector('#node-resource canvas').toDataURL().length>3000);await screenshot(page,'editor-rewired');}
  await navigate(page,'overview');
  assert.equal(await page.locator('.screen').getAttribute('data-resource'),'shy_candidate.eaf');
  await page.locator('[data-action="preview-mode"][data-mode="baseline"]').click();
  assert.equal(await page.locator('.screen').getAttribute('data-resource'),'Happy.eaf');
  await page.locator('[data-action="preview-mode"][data-mode="draft"]').click();
  assert.equal(await page.locator('.screen').getAttribute('data-resource'),'shy_candidate.eaf');
  await navigate(page,'editor');
  await page.locator('[data-action="undo"]').click();assert.equal(await page.locator('#node-old').count(),0);
  await page.locator('[data-action="redo"]').click();assert.equal(await page.locator('#node-old').count(),1);
  const before=await page.locator('#node-semantic').boundingBox();const h=page.locator('#node-semantic [data-drag-handle]');
  const hb=await h.boundingBox();await page.mouse.move(hb.x+20,hb.y+8);await page.mouse.down();await page.mouse.move(hb.x+45,hb.y+26,{steps:5});await page.mouse.up();
  const after=await page.locator('#node-semantic').boundingBox();assert.ok(after.x>before.x+15,'node moved');
  await page.locator('[data-action="reset-graph"]').click();assert.equal(await page.locator('#node-semantic').evaluate(e=>e.style.transform),'');
  await page.locator('#route-picker').selectOption('pet-head');assert.match(await page.locator('.graph-note').innerText(),/动态/);
  await page.locator('#route-picker').selectOption('semantic-embarrassed');assert.match(await page.locator('.graph-note').innerText(),/固定/);
  await page.locator('[data-action="step"]').click();assert.match(await page.locator('.frame-readout').innerText(),/02/);
  await page.locator('#frame-control').fill('187');assert.match(await page.locator('.frame-readout').innerText(),/188/);
  await navigate(page,'publish');assert.match(await page.locator('.capacity-big').innerText(),/5.70/);
  assert.match(await page.locator('#view-content').innerText(),/匹配 E1.45/);
  assert.equal(await page.locator('.changes-table tbody tr').count(),1);
  const handoffEvent=page.waitForEvent('download');await page.locator('[data-action="handoff"]').click();const hd=await handoffEvent;
  const hp=path.join(out,local?'handoff-local.json':'handoff-public.json');await hd.saveAs(hp);
  const handoff=JSON.parse(fs.readFileSync(hp));assert.equal(handoff.device_written,false);assert.equal(handoff.budget.state,'verified_candidate');
  if(local)await screenshot(page,'publish-desktop');
  await navigate(page,'library');assert.equal(await page.locator('#workspace .asset-card').count(),19);
  await page.locator('[data-filter="candidate"]').click();assert.equal(await page.locator('#workspace .asset-card').count(),1);
  await page.locator('#workspace .asset-card').click();assert.equal(await page.locator('#studio-dialog').isVisible(),true);
  if(!local){assert.equal(await page.locator('#dialog-content canvas').evaluate(c=>{const ctx=c.getContext('2d');return ctx.getImageData(180,180,1,1).data[3];}),255);}
  await page.keyboard.press('Escape');await page.locator('[data-filter="all"]').click();
  await page.locator('#library-search').fill('聆听');assert.equal(await page.locator('#workspace .asset-card').count(),1);
  await page.locator('#library-search').fill('');if(local)await screenshot(page,'library-desktop');
  await page.locator('[data-filter="actions"]').click();assert.equal(await page.locator('#workspace .action-card').count(),5);
  await page.locator('[data-action="action-detail"][data-motion="cat_nuzzle"]').click();
  assert.match(await page.locator('#dialog-content').innerText(),/C61/);assert.match(await page.locator('#dialog-content').innerText(),/害羞/);
  await page.keyboard.press('Escape');if(local)await screenshot(page,'actions-desktop');
  await page.locator('[data-filter="all"]').click();
  await navigate(page,'roles');assert.equal(await page.locator('.role-card').count(),3);
  await page.locator('[data-action="clone-role"]').click();assert.equal(await page.locator('.role-card').count(),4);
  await page.locator('#role-name').fill('我的新角色');await page.locator('#role-name').blur();
  assert.equal(await page.evaluate(()=>DeskBotStudio.snapshot().roles.at(-1).name),'我的新角色');
  await page.reload();await page.waitForFunction(()=>window.DeskBotStudio);assert.equal(await page.evaluate(()=>DeskBotStudio.snapshot().roles.at(-1).name),'我的新角色');
  const downloadEvent=page.waitForEvent('download');await page.locator('[data-action="export"]').first().click();const download=await downloadEvent;
  const dp=path.join(out,local?'draft-local.json':'draft-public.json');await download.saveAs(dp);
  const draft=JSON.parse(fs.readFileSync(dp));assert.equal(draft.roles.length,4);assert.equal(draft.scope,'offline_draft');
  await page.locator('#import-file').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{"invalid":true}')});
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('导入未完成'));assert.equal(await page.evaluate(()=>DeskBotStudio.snapshot().roles.length),4);
  await page.locator('#import-file').setInputFiles(dp);await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('已导入'));
  for(const viewport of [{width:390,height:844},{width:320,height:740}]){
    await page.setViewportSize(viewport);
    for(const v of ['overview','editor','library','roles','publish']){await navigate(page,v);await noOverflow(page);if(local&&viewport.width===390)await screenshot(page,v+'-mobile');}
  }
  await page.setViewportSize({width:1440,height:1040});await navigate(page,'overview');
  await page.locator('.flow-stop').first().click();assert.match(await page.locator('#studio-dialog').innerText(),/已保存记录/);await page.keyboard.press('Escape');
  await page.evaluate(()=>{document.body.dataset.theme='dark';});if(local)await screenshot(page,'overview-dark');
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true,executablePath:edge});
  await smoke(false);await smoke(true);
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'no-preference'}),page=await context.newPage();
  await page.goto(pathToFileURL(path.join(root,'tmp/deskbot-studio-e2a-20261104/index.html')).href);await page.waitForFunction(()=>window.DeskBotStudio);
  await page.waitForTimeout(200);const first=await page.locator('.screen').evaluate(c=>c.toDataURL());await page.waitForTimeout(300);const second=await page.locator('.screen').evaluate(c=>c.toDataURL());assert.notEqual(first,second,'animated EAF preview changes frames');
  await navigate(page,'editor');await navigate(page,'library');await context.close();await browser.close();
  const result={result:'passed',editions:['public','local'],checks:['five_connected_views','source_backed_catalog','animated_previews','square_node_previews','baseline_draft_switch','body_action_catalog','layout_compatibility','single_semantic_rewire','before_after_nodes','drag_and_wires','undo_redo','accurate_candidate_budget','role_isolation','autosave_restore','import_export','invalid_import_preserves_draft','draft_handoff','dynamic_route_labels','320px_390px_1440px','light_dark','reduced_motion','no_external_requests','no_page_errors']};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
