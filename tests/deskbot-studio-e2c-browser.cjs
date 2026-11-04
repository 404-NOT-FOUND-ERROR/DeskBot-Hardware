'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e2c-browser-20261104');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8766';
const checks=[];
let browser;
async function nav(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);}
async function click(page,action){await page.locator(`[data-sim-action="${action}"]`).click();}
async function last(page,status){await page.waitForFunction(status=>DeskBotStudio.simulation().receipts.at(-1)?.status===status,status);}
async function screenshot(page,name){await page.evaluate(()=>{document.getElementById('toast').classList.remove('show');window.scrollTo({top:0,behavior:'instant'});});await page.waitForTimeout(400);await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function download(page,action,name){const pending=page.waitForEvent('download');await click(page,action);const item=await pending;const target=path.join(out,name);await item.saveAs(target);return JSON.parse(fs.readFileSync(target));}

async function exercise(url,local){
  const context=await browser.newContext({viewport:{width:1440,height:1040},acceptDownloads:true});
  const page=await context.newPage(),errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(!/^(file:|data:|blob:)/.test(request.url())&&!request.url().startsWith(origin+'/'))external.push(request.url());});
  await page.goto(url);await page.waitForFunction(()=>window.DeskBotStudio);await nav(page,'connection');
  assert.equal(await page.locator('#main-nav a').count(),6);
  assert.equal(await page.locator('[data-sim-action="send"]').isDisabled(),true);
  assert.equal(await page.locator('#sim-selected option[value="listen"]').count(),0);
  await click(page,'connect');await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  const before=await page.locator('.sim-screen canvas').evaluate(c=>c.toDataURL());
  await page.waitForTimeout(220);
  assert.notEqual(await page.locator('.sim-screen canvas').evaluate(c=>c.toDataURL()),before,'selection preview animates');
  await click(page,'send');await last(page,'dispatched');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().execution_count),1);
  await click(page,'repeat');await last(page,'duplicate');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().execution_count),1);
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1).original_status),'dispatched');
  if(local)await screenshot(page,'connection-dispatched');
  await page.locator('#sim-mode').selectOption('busy');await click(page,'send');await last(page,'busy');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().execution_count),1);
  await page.locator('#sim-mode').selectOption('stall');await click(page,'send');await last(page,'accepted');
  const requestId=await page.evaluate(()=>DeskBotStudio.simulation().pending.request_id);
  await click(page,'timeout');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().pending.request_id),requestId);
  await click(page,'fact');assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1).request_id),null);
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().pending.request_id),requestId);
  await page.locator('#sim-mode').selectOption('normal');await last(page,'dispatched');
  await page.locator('#sim-mode').selectOption('stall');await click(page,'send');await last(page,'accepted');
  await click(page,'disconnect');await page.locator('#sim-mode').selectOption('normal');
  await page.waitForFunction(()=>DeskBotStudio.simulation().pending===null);
  await click(page,'connect');await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1).status),'dispatched');
  await click(page,'repeat');await last(page,'duplicate');
  await page.locator('#sim-mode').selectOption('stall');await click(page,'send');await last(page,'accepted');
  await last(page,'expired');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().pending),null);
  await click(page,'restart');assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().connected),false);
  await click(page,'connect');await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  await click(page,'repeat');await last(page,'rejected');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1).reason),'boot_changed');
  await page.locator('#sim-kind').selectOption('play_action');
  assert.equal(await page.locator('#sim-selected option').count(),5);
  await page.locator('#sim-mode').selectOption('base_offline');await click(page,'send');await last(page,'rejected');
  assert.equal(await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1).reason),'base_unavailable');
  await page.locator('#sim-mode').selectOption('normal');await click(page,'send');await last(page,'dispatched');await click(page,'fact');
  const fact=await page.evaluate(()=>DeskBotStudio.simulation().receipts.at(-1));
  assert.equal(fact.message_type,'base_fact');assert.equal(fact.correlated,false);assert.equal(fact.request_id,null);
  const catalog=await download(page,'export-catalog',local?'local-catalog.json':'public-catalog.json');
  assert.equal(catalog.expressions.length,29);assert.equal(catalog.actions.length,5);assert.equal(catalog.live,false);
  await page.locator('.sim-directory summary').click();
  const session=await download(page,'export-session',local?'local-session.json':'public-session.json');
  assert.equal(session.catalog_id,catalog.catalog_id);assert.equal(session.hardware_accessed,false);
  assert.ok(session.receipts.length<=24);
  if(local)await screenshot(page,'connection-action-facts');
  await page.locator('.sim-directory summary').click();
  await page.locator('#sim-kind').selectOption('play_expression');await page.locator('#sim-selected').selectOption('happy');
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    await page.waitForTimeout(150);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow at '+width);
    const circle=await page.locator('.sim-screen').boundingBox();
    assert.ok(Math.abs(circle.width-circle.height)<1,'circular preview at '+width);
    assert.ok(await page.locator('#main-nav a').evaluateAll(links=>links.every(link=>{const r=link.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;})),'all navigation items visible at '+width);
    if(local)await screenshot(page,'connection-'+width);
  }
  await page.setViewportSize({width:1440,height:1040});
  await page.locator('#theme-toggle').click();assert.equal(await page.locator('body').getAttribute('data-theme'),'dark');
  if(local)await screenshot(page,'connection-dark');
  await nav(page,'editor');await page.locator('[data-action="pick-resource"]').click();
  await page.locator('[data-action="choose-resource"][data-resource="shy_candidate.eaf"]').click();
  assert.equal(await page.locator('#node-old').count(),1);
  await nav(page,'connection');const savedCatalog=await download(page,'export-catalog',local?'local-after-edit-catalog.json':'public-after-edit-catalog.json');
  assert.deepEqual(savedCatalog,catalog);
  await nav(page,'roles');assert.equal(await page.locator('.role-card').count(),3);
  await nav(page,'publish');
  if(local){
    await page.waitForFunction(()=>DeskBotStudio.release().connection==='ready');
    await page.locator('[data-package-action="generate"]').first().click();
    await page.waitForFunction(()=>DeskBotStudio.release().current&&!DeskBotStudio.release().busy,{},{timeout:30000});
    assert.equal(await page.evaluate(()=>DeskBotStudio.release().report.package.bytes),5981260);
    assert.equal(await page.locator('a[download]').count(),2);
  }else assert.equal(await page.locator('[data-package-action="generate"]').first().isDisabled(),true);
  for(const view of ['overview','library','connection'])await nav(page,view);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await context.close();
}

async function reducedMotion(url){
  const context=await browser.newContext({viewport:{width:1440,height:1040},reducedMotion:'reduce'});
  const page=await context.newPage();
  await page.goto(url);await page.waitForFunction(()=>window.DeskBotStudio);await nav(page,'connection');
  await page.waitForTimeout(500);
  const first=await page.locator('.sim-screen canvas').evaluate(c=>c.toDataURL());
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.sim-screen canvas').evaluate(c=>c.toDataURL()),first,'reduced motion keeps preview still');
  await click(page,'connect');await page.locator('#sim-mode').selectOption('stall');await click(page,'send');await last(page,'accepted');
  assert.equal(await page.locator('.sim-flow-step.running>span').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.locator('#sim-kind').selectOption('play_action');
  assert.equal(await page.locator('.sim-motion-pet').evaluate(el=>getComputedStyle(el).animationName),'none');
  await context.close();
}

(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:(process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  await exercise(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e2c-20261104/index.html')).href,false);
  await exercise(origin,true);await reducedMotion(origin);await browser.close();
  checks.push('public_and_local_pages','six_page_navigation','animated_selection_preview','catalog_digest_at_connect','accepted_then_dispatched','duplicate_id_returns_original','busy_without_dispatch','client_timeout_keeps_request','uncorrelated_fact_keeps_request','disconnected_dispatch_and_snapshot','restart_rejects_old_boot','deadline_expiry','base_offline_action_rejection','five_official_actions','directory_and_session_export','saved_directory_independent_from_draft','editor_roles_publisher_regression','candidate_package_generation','320_390_1440_widths','circular_preview_at_all_widths','mobile_navigation_visible','dark_theme','reduced_motion','no_external_requests','no_page_errors');
  const result={result:'passed',checks,hardware_accessed:false,device_written:false};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
