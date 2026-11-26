'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {pathToFileURL}=require('node:url');const {chromium}=require(process.argv[2]||'playwright');
(async()=>{const asset=process.env.DESKBOT_LAYERED_ASSET||'artifacts/layered-face-20261122';
  const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
  try{const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));await page.goto(pathToFileURL(path.resolve(asset,'index.html')).href);
    await page.waitForFunction(()=>window.facePlayer);await page.locator('#pause').click();
    const result=await page.evaluate(()=>{let p=facePlayer,seen=0;
      for(const name of p.names){p.request(name,true);p.advance(360);let clip=p.clips[name];
        if(name!=='neutral'&&name!=='idle'){const duration=clip.body*40;let elapsed=0;
          const samples=new Set([p.frame().toDataURL()]);
          for(let i=1;i<=8;i++){const target=Math.round(duration*i/8);p.advance(target-elapsed);elapsed=target;samples.add(p.frame().toDataURL());}
          if(samples.size<2)throw Error('动作不推进 '+name);}
        if(!clip.loop&&p.phase!=='exit')throw Error('未播放完整主体 '+name);
        p.cancel();p.advance(360);if(p.phase!=='idle')throw Error('未收尾 '+name);seen++;}
      p.request('crying',true);p.advance(600);p.request('happy',true);p.advance(120);p.cancel();p.advance(360);
      if(p.phase!=='idle')throw Error('取消未完成');return {semantics:seen,complete_body:true,interrupt:true,cancel:true};});
    assert.equal(result.semantics,29);
    if((await page.evaluate(()=>!!(facePlayer.flags&1))) &&
       (await page.locator('[data-expression="sleepy"]').count()!==0||await page.locator('[data-expression="tried"]').count()!==1))
      throw Error('困倦和疲惫应共用一个疲惫入口');
    await page.locator('[data-expression="winking"]').click();
    await page.locator('#mirror').check();assert.equal(await page.evaluate(()=>facePlayer.clips.winking.mirror),true);
    await page.locator('#mirror').uncheck();await page.locator('[data-expression="idle"]').click();
    if(await page.evaluate(()=>!!(facePlayer.flags&1))){
      await page.locator('[data-expression="tried"]').click();await page.locator('#mirror').check();
      assert.equal(await page.evaluate(()=>facePlayer.clips.sleepy.mirror),true);
      await page.locator('#mirror').uncheck();assert.equal(await page.evaluate(()=>facePlayer.clips.sleepy.mirror),false);
      await page.locator('[data-expression="idle"]').click();
    }
    await page.screenshot({path:path.join(asset,'preview-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(asset,'preview-mobile.png'),fullPage:true});assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(asset,'browser-tests.json'),JSON.stringify({...result,desktop:true,mobile:true,mirror:true,errors},null,2));console.log(JSON.stringify(result));
  }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
