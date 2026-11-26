'use strict';
const assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1024}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>errors.push(r.url()+': '+r.failure()?.errorText));
  assert.equal((await page.goto('http://127.0.0.1:8782/expressions/')).status(),200);
  await page.waitForFunction(()=>document.querySelector('#preview-tag')?.textContent.includes('当前已安装素材'));
  await page.locator('#search').fill('开心');
  await page.locator('.node.semantic').click();
  await page.locator('#cancel').click();
  await page.waitForFunction(()=>document.querySelector('#phase')?.textContent==='待机');
  await page.locator('#search').fill('');
  await page.locator('#fit').click();
  await page.screenshot({path:process.argv[4],fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.locator('#fit').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:process.argv[5],fullPage:true});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({current_resources:true,semantic_selection:true,cancel_to_idle:true,mobile:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
