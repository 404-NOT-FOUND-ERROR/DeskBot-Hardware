'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {createHash}=require('node:crypto');
const {chromium}=require(process.argv[2]||'playwright');
(async()=>{
  const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:8782/#roles');
    await page.waitForFunction(()=>globalThis.DESKBOT_LAYERED_FACE===true);
    const iframe=page.frameLocator('iframe[title="高清分层表情预览"]');
    await iframe.locator('[data-expression="crying"]').click();
    const frame=page.frames().find(frame=>frame.url().endsWith('/layered-face/'));
    await frame.waitForFunction(()=>facePlayer.current==='crying');
    assert.equal(await iframe.locator('[data-expression]').count(),29);
    await iframe.locator('#mirror').check();
    assert.equal(await frame.evaluate(()=>facePlayer.clips.crying.mirror),true);
    await iframe.locator('#cancel').click();
    await frame.waitForFunction(()=>facePlayer.phase==='idle');
    await page.screenshot({path:'artifacts/layered-face-20261122/workbench-desktop.png',fullPage:true});
    await page.goto('http://127.0.0.1:8782/#publish');
    const download=page.locator('a[download][href="/layered-face/assets-8m.bin"]');
    assert.equal(await download.count(),1);
    const response=await page.request.get('http://127.0.0.1:8782/layered-face/assets-8m.bin');
    assert.equal(response.status(),200);
    const bytes=await response.body(),report=JSON.parse(await fs.readFile('artifacts/layered-face-20261122/report.json','utf8'));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),report.image_sha256);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    const result={embedded_preview:true,semantics:29,mirror:true,cancel:true,download_matches:true,mobile:true,errors};
    await fs.writeFile('artifacts/layered-face-20261122/workbench-tests.json',JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
