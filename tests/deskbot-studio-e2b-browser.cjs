'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e2b-browser-20261104');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8766';
const hash=raw=>crypto.createHash('sha256').update(raw).digest('hex');
let browser;
async function nav(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);}
async function wireCandidate(page){await nav(page,'editor');await page.locator('[data-action="pick-resource"]').click();await page.locator('[data-action="choose-resource"][data-resource="shy_candidate.eaf"]').click();}
async function generate(page){await page.locator('[data-package-action="generate"]').first().click();await page.waitForFunction(()=>!DeskBotStudio.release().busy&&DeskBotStudio.release().current,{},{timeout:30000});}
async function download(page,selector,name){const pending=page.waitForEvent('download');await page.locator(selector).click();const value=await pending;const target=path.join(out,name);await value.saveAs(target);return fs.readFileSync(target);}
async function shot(page,name){await page.evaluate(()=>document.getElementById('toast').classList.remove('show'));await page.waitForTimeout(350);await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function narrow(page){for(const width of [390,320]){await page.setViewportSize({width,height:844});await page.waitForTimeout(100);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'publish page overflow at '+width);await shot(page,'publish-'+width);}}
async function standalone(){
  const context=await browser.newContext({viewport:{width:1440,height:1040},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(!/^(file:|data:|blob:)/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e2b-20261104/index.html')).href);await page.waitForFunction(()=>window.DeskBotStudio);await nav(page,'publish');
  assert.equal(await page.locator('[data-package-action="generate"]').first().isDisabled(),true);
  await page.locator('#package-report-file').setInputFiles(path.join(root,'artifacts/deskbot-studio-e2b-20261104/default-package-report.json'));
  await page.waitForFunction(()=>DeskBotStudio.release().current);assert.equal(await page.locator('a[download]').count(),0);
  const bad=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2b-20261104/default-package-report.json')));bad.input_sha256='0'.repeat(64);
  await page.locator('#package-report-file').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('摘要'));assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),true);
  await wireCandidate(page);await nav(page,'publish');assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),false);assert.equal(await page.locator('.package-stale').count(),1);
  await page.locator('#package-report-file').setInputFiles(path.join(root,'artifacts/deskbot-studio-e2b-20261104/candidate-package-report.json'));
  await page.waitForFunction(()=>DeskBotStudio.release().current);assert.equal(await page.locator('a[download]').count(),0);
  await narrow(page);assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await context.close();
}
async function connected(){
  const context=await browser.newContext({viewport:{width:1440,height:1040},reducedMotion:'reduce',acceptDownloads:true}),page=await context.newPage(),errors=[],external=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:')&&!r.url().startsWith('blob:'))external.push(r.url());});
  await page.goto(origin);await page.waitForFunction(()=>window.DeskBotStudio&&DeskBotStudio.release().connection==='ready');await nav(page,'publish');
  await generate(page);let report=await page.evaluate(()=>DeskBotStudio.release().report);
  assert.equal(report.package.bytes,5538351);assert.equal(report.image.sha256,'8e9dae01b99060c313a76f2dfc7c2b854b80c4584d76f607a74dd2903d39fce4');
  assert.equal(await page.locator('a[download]').count(),2);
  await wireCandidate(page);await nav(page,'publish');assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),false);assert.equal(await page.locator('a[download]').count(),0);
  await generate(page);report=await page.evaluate(()=>DeskBotStudio.release().report);
  assert.equal(report.package.bytes,5981260);assert.equal(report.changes.length,1);assert.equal(report.changes[0].semantic,'embarrassed');
  assert.equal(await page.locator('.capacity-big').getAttribute('data-package-bytes'),'5981260');
  const raw=await download(page,'a[download][href$="expression-assets-8m.bin"]','candidate-assets-8m.bin');assert.equal(raw.length,8388608);assert.equal(hash(raw),report.image.sha256);
  const downloaded=JSON.parse(await download(page,'a[download][href$="report.json"]','report.json'));assert.deepEqual(downloaded,report);
  await shot(page,'publish-generated');await page.locator('.publish-evidence summary').first().click();await shot(page,'publish-audit');
  await page.locator('.publish-evidence summary').first().click();await narrow(page);
  await page.setViewportSize({width:1440,height:1040});await nav(page,'roles');
  await page.locator('[data-action="select-role"]').nth(1).click();await nav(page,'publish');
  assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),false);assert.equal(await page.locator('a[download]').count(),0);
  await nav(page,'roles');await page.locator('[data-action="select-role"][data-role="role-default"]').click();await nav(page,'publish');
  assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),true);
  await page.locator('#package-report-file').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{"invalid":true}')});
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('导入未完成'));
  assert.equal(await page.evaluate(()=>DeskBotStudio.release().current),true);assert.equal(await page.locator('a[download]').count(),2);
  await page.locator('#package-report-file').setInputFiles(path.join(out,'report.json'));await page.waitForFunction(()=>DeskBotStudio.release().source==='导入报告');
  assert.equal(await page.locator('a[download]').count(),0);await generate(page);
  const incomplete=await page.evaluate(()=>DeskBotStudio.snapshot());
  incomplete.roles.find(role=>role.id===incomplete.selected_role_id).bindings.embarrassed=null;
  await page.locator('#import-file').setInputFiles({name:'incomplete-draft.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(incomplete))});
  await page.waitForFunction(()=>DeskBotStudio.budget().disconnected===1);
  assert.equal(await page.locator('[data-package-action="generate"]').first().isDisabled(),true);assert.equal(await page.locator('a[download]').count(),0);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);await context.close();
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true,executablePath:(process.env.DESKBOT_BROWSER_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')});
  await standalone();await connected();await browser.close();
  const result={result:'passed',checks:['standalone_report_import','input_digest_verification','invalid_import_preserves_state','actual_package_generation','exact_E1_18_default','exact_E1_45_candidate','downloaded_image_hash','stale_report_hides_downloads','role_switch_invalidates_report','restoring_role_recovers_matching_report','imported_report_has_no_binary_download','incomplete_draft_disables_generation','320px_390px_1440px','no_external_requests','no_page_errors'],hardware_accessed:false};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
