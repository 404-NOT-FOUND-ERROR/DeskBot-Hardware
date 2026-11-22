'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {pathToFileURL}=require('node:url');const {chromium}=require(process.argv[2]||'playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
  try{const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));await page.goto(pathToFileURL(path.resolve('artifacts/layered-face-20261122/index.html')).href);
    await page.waitForFunction(()=>window.facePlayer);await page.locator('#pause').click();
    const result=await page.evaluate(()=>{let p=facePlayer,seen=0;
      for(const name of p.names){p.request(name,true);p.advance(360);let clip=p.clips[name];
        if(name!=='neutral'&&name!=='idle'){p.advance(clip.body*20);let a=p.frame().toDataURL();p.advance(40);let b=p.frame().toDataURL();if(a===b)throw Error('动作不推进 '+name);p.advance(clip.body*20-40);}
        if(!clip.loop&&p.phase!=='exit')throw Error('未播放完整主体 '+name);
        p.cancel();p.advance(360);if(p.phase!=='idle')throw Error('未收尾 '+name);seen++;}
      p.request('crying',true);p.advance(600);p.request('happy',true);p.advance(120);p.cancel();p.advance(360);
      if(p.phase!=='idle')throw Error('取消未完成');return {semantics:seen,complete_body:true,interrupt:true,cancel:true};});
    assert.equal(result.semantics,29);await page.locator('[data-expression="winking"]').click();
    await page.locator('#mirror').check();assert.equal(await page.evaluate(()=>facePlayer.clips.winking.mirror),true);
    await page.locator('#cancel').click();await page.screenshot({path:'artifacts/layered-face-20261122/preview-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:'artifacts/layered-face-20261122/preview-mobile.png',fullPage:true});assert.deepEqual(errors,[]);
    await fs.writeFile('artifacts/layered-face-20261122/browser-tests.json',JSON.stringify({...result,desktop:true,mobile:true,mirror:true,errors},null,2));console.log(JSON.stringify(result));
  }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
