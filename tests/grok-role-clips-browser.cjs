'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),qa=path.join(root,'tmp/grok-role-clips-20261111/qa');
let browser;
(async()=>{
  fs.mkdirSync(qa,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1200,height:950}});
  const errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(/^https?:/.test(request.url()))external.push(request.url())});
  await page.goto(pathToFileURL(path.join(root,'artifacts/grok-role-clips-20261111/index.html')).href);
  await page.waitForFunction(()=>document.querySelector('#play').disabled===false);
  assert.equal(await page.locator('.choice').count(),6);
  assert.equal(await page.locator('.node canvas').count(),3);
  const paused=async value=>{
    if(await page.evaluate(()=>rolePreview.snapshot().paused)!==value)await page.locator('#play').click();
    assert.equal(await page.evaluate(()=>rolePreview.snapshot().paused),value);
  };
  await paused(true);
  const first=await page.locator('#screen').getAttribute('data-frame');
  await page.waitForTimeout(160);
  assert.equal(await page.locator('#screen').getAttribute('data-frame'),first);
  await page.locator('[data-state=happy].choice').click();
  assert.equal(await page.evaluate(()=>rolePreview.snapshot().target),'happy');
  assert.match(await page.locator('#pending').innerText(),/开心/);
  await paused(false);
  await page.waitForFunction(()=>rolePreview.snapshot().current==='happy'&&rolePreview.snapshot().phase==='loop',
    null,{timeout:5500});
  await paused(true);
  assert.equal(await page.locator('.node[data-active=true]').getAttribute('data-phase'),'loop');
  assert.equal(await page.locator('#screen').getAttribute('data-state'),'happy');
  await page.screenshot({path:path.join(qa,'desktop.png'),fullPage:true});
  await page.locator('[data-state=sad].choice').click();
  await page.locator('[data-state=confused].choice').click();
  await page.locator('[data-state=shy].choice').click();
  assert.equal(await page.locator('.choice[aria-pressed=true]').getAttribute('data-state'),'shy');
  assert.equal(await page.evaluate(()=>rolePreview.snapshot().target),'shy');
  await paused(false);
  await page.waitForFunction(()=>rolePreview.snapshot().phase==='exit',null,{timeout:3000});
  await paused(true);
  assert.equal(await page.locator('.node[data-active=true]').getAttribute('data-phase'),'exit');
  // A request made while exiting is picked at H, rather than starting a stale target.
  await page.locator('[data-state=drowsy].choice').click();
  await paused(false);
  await page.waitForFunction(()=>rolePreview.snapshot().current==='drowsy'&&rolePreview.snapshot().phase==='entry',
    null,{timeout:1500});
  await paused(true);
  assert.equal(await page.locator('#screen').getAttribute('data-state'),'drowsy');
  assert.equal(await page.locator('.node[data-active=true]').getAttribute('data-phase'),'entry');
  const thumbnailFrames=await page.locator('.node canvas').evaluateAll(nodes=>nodes.map(n=>n.dataset.frame));
  await page.waitForTimeout(160);
  assert.deepEqual(await page.locator('.node canvas').evaluateAll(nodes=>nodes.map(n=>n.dataset.frame)),thumbnailFrames);
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal overflow at '+width);
    await page.screenshot({path:path.join(qa,'mobile-'+width+'.png'),fullPage:true});
  }
  await page.locator('#restart').click();
  assert.equal(await page.evaluate(()=>rolePreview.snapshot().current),'idle');
  await paused(true);
  assert.equal(await page.evaluate(()=>rolePreview.snapshot().target),'idle');
  assert.equal(await page.locator('#error').isVisible(),false);
  // File-origin canvas is intentionally readable only by the compositor.
  // Inspect the rendered screenshots; full RGBA content is checked by the EAF decoder tests.
  assert(await page.evaluate(()=>Object.values(images).every(image=>
    image.complete&&image.naturalWidth===1800&&image.naturalHeight===1440)));
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={result:'passed',scope:'six selectors, three animated nodes, pause/resume, latest target, entry/exit progression, reset, 390/320 layouts, offline EAF atlases',
    browser_errors:errors,external_requests:external};
  fs.writeFileSync(path.join(qa,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
  await browser.close();
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1});
