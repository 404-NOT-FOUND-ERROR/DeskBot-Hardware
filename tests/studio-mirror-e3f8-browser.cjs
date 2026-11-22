'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),qa=path.join(root,'tmp/deskbot-studio-e3f8-20261111/qa');
const python=process.env.DESKBOT_PYTHON||'D:/Program Files (x86)/Python/Python 3.13.0/python.exe';
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let server,browser,temp;const checks=[],errors=[],deviceRequests=[];
const check=(condition,label)=>{assert.ok(condition,label);checks.push(label);};
async function start(){
  temp=fs.mkdtempSync(path.join(root,'tmp/mirror-e3f8-'));
  server=spawn(python,['tests/serve_studio_mirror_fixture.py',temp],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let log='';server.stdout.on('data',s=>log+=s);server.stderr.on('data',s=>log+=s);
  const end=Date.now()+15000;while(Date.now()<end&&!/http:\/\/127\.0\.0\.1:\d+/.test(log)&&server.exitCode===null)await wait(50);
  const match=log.match(/http:\/\/127\.0\.0\.1:\d+/);assert.ok(match,log);return match[0];
}
async function settled(page){
  await page.waitForFunction(()=>!document.documentElement.matches(':active-view-transition'));
  await page.evaluate(()=>Promise.all(document.getAnimations().filter(a=>['studio-arrive','studio-leave','page-enter'].includes(a.animationName)).map(a=>a.finished.catch(()=>{}))));
}
async function pixels(page,selector,mode,official=false){
  await page.waitForFunction(selector=>document.querySelector(selector)?.dataset.frame!==undefined,selector);
  const result=await page.evaluate(async({selector,mode,official})=>{
    const c=document.querySelector(selector),data=JSON.parse(document.querySelector('#studio-data').textContent),
      catalog=JSON.parse(document.querySelector('#runtime-roles-data').textContent),binding=official?catalog.roles[0].expressions[c.dataset.runtimeOfficial]:null,
      a=data.assets[binding?.resource_id||c.dataset.resource],image=new Image();
    image.src=a.atlas;await image.decode();
    // Reduced motion freezes the clock; expected pixels use the actual reported frame.
    const frame=Number(c.dataset.frame),expected=document.createElement('canvas');expected.width=c.width;expected.height=c.height;
    const ctx=expected.getContext('2d'),w=a.frame_width,h=a.frame_height,x=frame%a.columns*w,y=Math.floor(frame/a.columns)*h;
    ctx.fillStyle='#000';ctx.fillRect(0,0,c.width,c.height);ctx.save();
    if(mode!=='raw'){ctx.beginPath();ctx.arc(180,180,180,0,Math.PI*2);ctx.clip();}
    if(mode==='raw'){assertDimensions();ctx.drawImage(image,x,y,w,h,0,0,w,h);}
    else if(mode==='eye_anim'){
      const source=document.createElement('canvas');source.width=w;source.height=h;
      const sc=source.getContext('2d');sc.drawImage(image,x,y,w,h,0,0,w,h);
      const raw=sc.getImageData(0,0,w,h).data,top=Math.floor((360-h)/2)+10;
      // Inverse GFX mapping, independent from the production forward renderer.
      for(let py=0;py<h;py++)for(let p=0;p<340;p++){
        const candidates=[p,339-p].filter(i=>i<Math.min(w,340)&&raw[(py*w+i)*4+3]);
        if(!candidates.length)continue;const i=(py*w+Math.max(...candidates))*4;
        ctx.fillStyle=`rgb(${raw[i]},${raw[i+1]},${raw[i+2]})`;ctx.fillRect(10+p,top+py,1,1);
      }
    }
    else ctx.drawImage(image,x,y,w,h,Math.floor((360-w)/2),mode==='listen_anim'?25:Math.floor((360-h)/2),w,h);
    function assertDimensions(){if(c.width!==w||c.height!==h)throw Error('Raw canvas lost source dimensions');}
    ctx.restore();const actual=c.getContext('2d').getImageData(0,0,c.width,c.height).data,wanted=ctx.getImageData(0,0,c.width,c.height).data;
    const index=actual.findIndex((v,i)=>v!==wanted[i]);
    return {layout:c.dataset.layout,equal:index<0,frame,first_mismatch:index<0?null:{pixel:Math.floor(index/4),channel:index%4,actual:actual[index],expected:wanted[index]}};
  },{selector,mode,official});
  assert.equal(result.layout,mode);assert.equal(result.equal,true,selector+' pixels '+mode+' '+JSON.stringify(result.first_mismatch));return result.frame;
}
(async()=>{
  fs.mkdirSync(qa,{recursive:true});const url=await start();
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1440,height:1080},reducedMotion:'reduce'}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('request',r=>{if(/\/api\/(studio\/command|body\/connect)/.test(r.url()))deviceRequests.push(r.url());});
  await page.goto(url+'/#editor');await page.locator('[data-resource-mirror]').waitFor();
  const draft=()=>page.evaluate(()=>DeskBotStudio.snapshot());
  const active=d=>d.roles.find(r=>r.id===d.selected_role_id);
  const mirror=()=>page.locator('#node-resource [data-resource-mirror]');
  const reset=()=>page.locator('#node-resource [data-mirror-auto]');
  async function select(id){await page.locator(`[data-action=select-semantic][data-semantic="${id}"]`).first().click();await settled(page);}
  async function replace(id){await page.locator('[data-action=pick-resource]').click();await page.locator(`[data-action=choose-resource][data-resource="${id}"]`).click();await settled(page);}
  async function route(id){await page.locator(`#main-nav [data-view="${id}"]`).click();await settled(page);}
  await select('eat');
  check(await page.locator('#node-layout').count()===0,'resource has a checkbox without an extra layout node');
  check(!await mirror().isChecked()&&await reset().isDisabled(),'eat defaults to mirror off');
  await pixels(page,'#node-resource canvas','full_frame');
  await mirror().check();await settled(page);await pixels(page,'#node-resource canvas','eye_anim');
  check(active(await draft()).mirror_overrides['eat.eaf']===true,'manual mirror updates preview and saved draft');
  check((await page.locator('.resource-mirror-option').textContent()).includes('手动设置'),'manual choice is labelled');
  await page.locator('[data-action=undo]').click();await settled(page);
  check(!await mirror().isChecked(),'undo restores automatic default');
  await page.locator('[data-action=redo]').click();await settled(page);
  check(await mirror().isChecked(),'redo restores manual mirror');
  await reset().click();await settled(page);
  check(!Object.hasOwn(active(await draft()).mirror_overrides,'eat.eaf')&&!await mirror().isChecked(),'restore automatic removes manual override');
  await select('happy');check(await mirror().isChecked(),'single-eye defaults to mirror on');
  await mirror().uncheck();await settled(page);await pixels(page,'#node-resource canvas','full_frame');
  check(await page.evaluate(()=>{const d=JSON.parse(document.querySelector('#studio-data').textContent);return d.assets['Happy.eaf'].frame_width===125;}),'odd-width 125px single eye matches integer GFX centering');
  check(active(await draft()).mirror_overrides['Happy.eaf']===false,'manual false remains an explicit choice');
  await select('laughing');
  check(await mirror().getAttribute('data-resource-mirror')==='Happy.eaf'&&!await mirror().isChecked(),'shared resource references use the same choice');
  check((await page.locator('.resource-mirror-foot').textContent()).includes('处引用同步采用'),'shared scope is visible');
  await replace('eat.eaf');check(!await mirror().isChecked()&&await reset().isDisabled(),'replacement uses the new resource default');
  await replace('Happy.eaf');check(!await mirror().isChecked()&&!await reset().isDisabled(),'returning to resource retains its manual choice');
  await page.reload();await page.locator('[data-resource-mirror]').waitFor();await select('happy');
  check(!await mirror().isChecked(),'refresh restores manual preference');
  const download=page.waitForEvent('download');await page.locator('[data-action=export]').click();
  const exported=await download,exportPath=path.join(temp,'export.json');await exported.saveAs(exportPath);
  const exportedDraft=JSON.parse(fs.readFileSync(exportPath,'utf8'));
  check(exportedDraft.schema==='deskbot.studio_draft/v2'&&active(exportedDraft).mirror_overrides['Happy.eaf']===false,'export contains the manual choice');
  await reset().click();await settled(page);check(await mirror().isChecked(),'automatic reset works before import');
  await page.locator('#import-file').setInputFiles(exportPath);await settled(page);
  await page.waitForFunction(()=>DeskBotStudio.snapshot().roles[0].mirror_overrides['Happy.eaf']===false);
  check(!await mirror().isChecked(),'import restores the chosen mirror state');
  await route('roles');await page.locator('.runtime-draft-editor summary').click();
  await page.locator('[data-action=clone-role]').click();await settled(page);
  const cloneDraft=await draft();check(active(cloneDraft).id.startsWith('role-copy-')&&active(cloneDraft).mirror_overrides['Happy.eaf']===false,'role copy retains mirror preferences');
  await route('editor');await select('happy');await mirror().check();await settled(page);
  const isolated=await draft();check(isolated.roles[0].mirror_overrides['Happy.eaf']===false&&active(isolated).mirror_overrides['Happy.eaf']===true,'editing a copied role keeps original choices independent');
  await page.locator('#role-picker').selectOption(isolated.roles[0].id);await settled(page);
  await select('embarrassed');await replace('shy_candidate.eaf');
  check(await mirror().isChecked(),'generated shy resource defaults to mirrored eyes');
  await page.locator('.node-source summary').click();
  check(await pixels(page,'.node-source canvas','raw')===await pixels(page,'#node-resource canvas','eye_anim'),'raw source and processed preview show the same frame');
  await page.locator('[data-action=step]').click();
  check(await pixels(page,'.node-source canvas','raw')===await pixels(page,'#node-resource canvas','eye_anim'),'stepping updates source and output together');
  await page.locator('#frame-control').evaluate(el=>{el.value='5';el.dispatchEvent(new Event('input',{bubbles:true}));});
  check(await pixels(page,'.node-source canvas','raw')===5&&await pixels(page,'#node-resource canvas','eye_anim')===5,'slider seeks the exact requested frame');
  await page.locator('#frame-control').focus();await page.keyboard.press('ArrowRight');
  check(await pixels(page,'.node-source canvas','raw')===6,'keyboard seeking reaches the next frame');
  const mismatches=await page.evaluate(()=>{
    const input=document.querySelector('#frame-control'),raw=document.querySelector('.node-source canvas'),out=document.querySelector('#node-resource canvas'),wrong=[];
    for(let f=0;f<=Number(input.max);f++){input.value=String(f);input.dispatchEvent(new Event('input',{bubbles:true}));if(Number(raw.dataset.frame)!==f||Number(out.dataset.frame)!==f)wrong.push(f);}return wrong;
  });check(mismatches.length===0,'all source frames are reachable without floating point rounding errors');
  await page.locator('#frame-control').evaluate(el=>{el.value='28';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.locator('[data-action=step]').click();check(await pixels(page,'.node-source canvas','raw')===29,'stepping crosses a fractional time boundary');
  await page.locator('#frame-control').evaluate(el=>{el.value=el.max;el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.locator('[data-action=step]').click();check(await pixels(page,'.node-source canvas','raw')===0,'last frame wraps to first');
  await page.screenshot({path:path.join(qa,'mirror-option-editor-desktop.png'),fullPage:true});
  await select('listen');check(await mirror().isDisabled()&&!await mirror().isChecked(),'system listening retains its separate layout');
  await pixels(page,'#node-resource canvas','listen_anim');
  await select('eat');await mirror().check();await settled(page);
  await route('publish');await page.waitForFunction(()=>!document.querySelector('[data-package-action=generate]')?.disabled);
  check((await page.locator('.mirror-release-note').textContent()).includes('E3f-9'),'publishing explains the required player increment');
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/package')&&r.request().method()==='POST');
  await page.locator('[data-package-action=generate]').first().click();
  const response=await responsePromise,value=await response.json();assert.equal(response.status(),200,JSON.stringify(value));
  await page.waitForFunction(()=>DeskBotStudio.release().current);
  const report=value.report,entries=report.layout_manifest.value.assets;
  check(entries.find(a=>a.resource_id==='eat.eaf').mirror===true&&entries.find(a=>a.resource_id==='Happy.eaf').mirror===false,'real generated package report contains both manual choices');
  check(report.device_compatible===false&&report.required_capabilities[0]==='asset_layout_manifest_v1','generation records the required device capability');
  const bytes=await context.request.get(url+value.downloads['expression-package.bin']);assert.equal(bytes.status(),200);
  fs.writeFileSync(path.join(temp,'downloaded.bin'),await bytes.body());
  const {execFileSync}=require('node:child_process');
  const binary=JSON.parse(execFileSync(python,['-c',"import json,sys;from protocol.studio_resource_package import load_legacy_tools;raw=open(sys.argv[1],'rb').read();tools,_=load_legacy_tools();members=tools.parse_package_bytes(raw,modern=False)['members'];print(members['deskbot-layout.json'].decode())",path.join(temp,'downloaded.bin')],{cwd:root,windowsHide:true,encoding:'utf8'}));
  check(JSON.stringify(binary)===JSON.stringify(report.layout_manifest.value),'downloaded binary metadata matches the selected preview settings');
  await page.screenshot({path:path.join(qa,'mirror-option-publish-desktop.png'),fullPage:true});
  await route('editor');await select('eat');await reset().click();await settled(page);await route('publish');
  check(!(await page.evaluate(()=>DeskBotStudio.release())).current,'changing mirror makes the earlier package stale');
  // An imported report must also bind the serialized metadata digest.
  const forged=JSON.parse(JSON.stringify(report));forged.layout_manifest.sha256='0'.repeat(64);
  await page.locator('#package-report-file').setInputFiles({name:'report.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(forged))});
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('镜像清单摘要'));
  check(true,'tampered metadata digest is rejected on report import');
  await route('editor');await select('eat');
  for(const width of [390,320]){
    await page.setViewportSize({width,height:960});
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no page overflow at '+width);
    await mirror().scrollIntoViewIfNeeded();check(await mirror().isVisible(),'mirror option stays reachable at '+width);
    await page.screenshot({path:path.join(qa,'mirror-option-mobile-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1080});await page.locator('#theme-toggle').click();
  check(await mirror().isVisible(),'checkbox remains visible in dark theme');await page.screenshot({path:path.join(qa,'mirror-option-dark.png'),fullPage:true});
  check(errors.length===0,'no browser script errors');check(deviceRequests.length===0,'authoring and packaging send no hardware commands');
  fs.writeFileSync(path.join(qa,'mirror-option-browser-report.json'),JSON.stringify({checks,errors,device_requests:deviceRequests,hardware_accessed:false},null,2)+'\n');
  console.log(JSON.stringify({checks:checks.length,errors,hardware_accessed:false}));await context.close();
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  await browser?.close();if(server&&server.exitCode===null){server.kill();await new Promise(resolve=>server.once('exit',resolve));}
  if(temp){assert.equal(path.dirname(path.resolve(temp)),path.join(root,'tmp'));assert.ok(path.basename(temp).startsWith('mirror-e3f8-'));fs.rmSync(temp,{recursive:true,force:true});}
});
