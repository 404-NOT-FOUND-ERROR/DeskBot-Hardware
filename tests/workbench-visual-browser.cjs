/* Integrated UI over a temporary store and a memory device. No serial access. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/workbench-visual-qa');
const routes=['overview','characters','shells','roles','editor','library','connection','publish','capture'];
const checks=[],errors=[],layouts=[],posts=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let server,browser,folder,origin;
function check(value,name){assert.ok(value,name);checks.push(name);}
async function route(page,name){
  await page.evaluate(name=>{location.hash=name;},name);
  await page.waitForFunction(name=>DeskBotStudio.state().view===name&&DeskBotWorkbenchMotion.state().route===name,name);
  if(name==='characters')await page.waitForSelector('[data-character-add]');
  if(name==='shells')await page.waitForSelector('#sc-form');
  if(name==='capture')await page.waitForSelector('#shell-workbench');
}
(async()=>{
  folder=fs.mkdtempSync(path.join(root,'tmp/workbench-visual-browser-'));fs.mkdirSync(out,{recursive:true});
  server=spawn('D:/Program Files (x86)/Python/Python 3.13.0/python.exe',['tests/serve_shell_workbench_fixture.py',folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let log='';server.stdout.on('data',v=>log+=v);server.stderr.on('data',v=>log+=v);
  for(let i=0;i<400&&!/http:\/\/127.0.0.1:\d+/.test(log)&&server.exitCode===null;i++)await pause(50);
  origin=log.match(/http:\/\/127.0.0.1:\d+/)?.[0];assert.ok(origin,log);
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  context.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.stack));
  await page.goto(origin+'/#overview');await page.waitForFunction(()=>!!window.DeskBotWorkbenchMotion);
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    for(const theme of ['light','dark']){
      await page.evaluate(theme=>{document.body.dataset.theme=theme;},theme);
      for(const name of routes){
        await route(page,name);await pause(50);
        const layout=await page.evaluate(()=>{
          const heading=document.querySelector('#workspace h1'),panel=document.querySelector('#workspace .surface,#workspace .characters-empty,#workspace .sc-editor,#workspace .runtime-stage,#workspace .shell-create');
          const style=el=>el?getComputedStyle(el):null;
          return {scroll:document.documentElement.scrollWidth,width:innerWidth,title:style(heading)?.fontSize,
            panel:style(panel)?.backgroundColor,radius:style(panel)?.borderRadius,ink:style(heading)?.color,
            background:getComputedStyle(document.body).getPropertyValue('--surface').trim(),
            overflow:[...document.querySelectorAll('#workspace *')].filter(el=>{
              const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+2&&getComputedStyle(el).position!=='absolute'&&!el.closest('.graph-scroll,.graph-world');
            }).slice(0,5).map(el=>el.className)};
        });
        layouts.push({width,theme,route:name,...layout});
        check(layout.scroll<=width+2,`${name} ${theme} at ${width}px fits viewport: ${JSON.stringify(layout.overflow)}`);
        check(layout.ink===(theme==='dark'?'rgb(232, 237, 250)':'rgb(32, 42, 65)'),`${name} ${theme} uses shared title color at ${width}px`);
        if(width===1440){
          check(layout.title==='36px',`${name} uses shared desktop heading scale`);
          if(['overview','characters','shells','roles','editor','capture'].includes(name)&&((theme==='light'&&['overview','shells','roles','editor'].includes(name))||(theme==='dark'&&['characters','capture'].includes(name))))
            await page.screenshot({path:path.join(out,`${name}-${theme}.png`)});
        }
      }
    }
  }
  // Populate a capture project as well as the empty screen: progress badges and
  // long real project names must wrap inside the narrow panel.
  await route(page,'capture');
  for(const [name,value]of Object.entries({name:'正式采样项目 · 固定布局 A 与 B 的重复装回验证',device_id:'visual-memory-device',sensor_id:'visual-memory-sensor',frame_id:'fixed-visual-frame',shell_names:'外壳 A\n外壳 B'}))await page.locator(`#shell-create-form [name="${name}"]`).fill(value);
  await page.locator('#shell-create-form [type="submit"]').click();await page.waitForSelector('.shell-project');
  for(const width of [390,320])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:844});await page.evaluate(theme=>document.body.dataset.theme=theme,theme);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),`populated capture ${theme} fits ${width}px`);
  }
  await page.setViewportSize({width:1440,height:1000});await route(page,'roles');
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.body.dataset.theme=theme,theme);
    const button=page.locator('[data-pack-export]');await page.keyboard.press('Tab');await button.focus();await button.hover();await pause(350);
    const colors=await button.evaluate(el=>({background:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color,outline:getComputedStyle(el).outlineColor}));
    check(colors.background===(theme==='dark'?'rgb(48, 60, 96)':'rgb(237, 240, 255)'),`${theme} editor hover follows shared theme`);
    check(colors.color===(theme==='dark'?'rgb(193, 204, 255)':'rgb(64, 83, 204)'),`${theme} editor hover keeps readable text`);
    check(colors.outline===(theme==='dark'?'rgb(169, 184, 255)':'rgb(82, 101, 223)'),`${theme} editor focus follows shared theme: ${JSON.stringify(colors)}`);
  }
  check(await page.evaluate(()=>DeskBotWorkbenchMotion.state().activeAnimations===0),'reduced motion completes all UI transitions immediately');
  const motionContext=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'no-preference'});
  motionContext.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
  const motion=await motionContext.newPage();motion.on('pageerror',error=>errors.push(error.stack));
  await motion.goto(origin+'/#overview');await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().activeAnimations===0);
  await motion.locator('#main-nav [data-view="shells"]').click();await motion.waitForSelector('#sc-form');
  check(await motion.evaluate(()=>DeskBotWorkbenchMotion.state().activeAnimations>0),'route change animates actual page elements');
  const midpoint=await motion.evaluate(()=>{
    const page=document.querySelector('#view-content'),animation=page.getAnimations().find(a=>a.effect.getTiming().duration===520);
    if(!animation)return null;animation.pause();animation.currentTime=260;
    const s=getComputedStyle(page),result={opacity:+s.opacity,y:new DOMMatrixReadOnly(s.transform).m42};
    animation.finish();return result;
  });
  check(midpoint&&midpoint.opacity>.7&&midpoint.opacity<1&&midpoint.y>0&&midpoint.y<6,'page has a real nonlinear intermediate frame');
  await motion.evaluate(()=>{for(const name of ['characters','roles','library'])document.querySelector(`#main-nav [data-view="${name}"]`).click();});
  check(await motion.evaluate(()=>DeskBotStudio.state().view==='library'&&DeskBotWorkbenchMotion.state().route==='library'),'rapid navigation keeps the final destination');
  check(await motion.evaluate(()=>DeskBotWorkbenchMotion.state().activeAnimations<=12),'rapid navigation cancels replaced page animations');
  check(await motion.locator('.workbench-nav-indicator').count()===1,'one persistent navigation indicator');
  await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().activeAnimations===0);
  const aligned=await motion.evaluate(()=>{
    const a=document.querySelector('#main-nav [aria-current=page]').getBoundingClientRect(),b=document.querySelector('.workbench-nav-indicator').getBoundingClientRect();
    return Math.abs(a.top-b.top)<1&&Math.abs(a.left-b.left)<1&&Math.abs(a.width-b.width)<1;
  });check(aligned,'navigation indicator settles on the current destination');
  await route(motion,'shells');await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().activeAnimations===0);
  const fold=motion.locator('.sc-session-lab'),summary=fold.locator('summary');
  const closed=(await fold.boundingBox()).height;
  await summary.click();await pause(80);
  check(await fold.evaluate(el=>el.open&&el.dataset.wbOpen==='true'&&el.getBoundingClientRect().height>24),'disclosure opens through an intermediate height');
  await summary.click({force:true});await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().folds===0);
  check(await fold.evaluate(el=>!el.open&&!el.style.height&&!el.style.overflow),'midflight reversal returns cleanly to closed');
  check(Math.abs((await fold.boundingBox()).height-closed)<1,'reversed disclosure restores natural collapsed layout');
  await summary.focus();await motion.keyboard.press('Enter');await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().folds===0);
  check(await fold.evaluate(el=>el.open),'keyboard opens disclosure');
  await fold.locator('select').focus();await summary.evaluate(el=>el.click());await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().folds===0);
  check(await summary.evaluate(el=>el===document.activeElement),'closing disclosure returns contained focus to summary');
  check(await fold.locator('select').evaluate(el=>!el.inert&&!el.closest('[inert]')),'closed disclosure restores original inert attributes');
  const entranceCount=await motion.evaluate(()=>DeskBotWorkbenchMotion.state().entrances);
  await motion.locator('#sc-form [name="shell_name"]').fill('保留中的外壳草稿');await motion.locator('#sc-form [name="shell_name"]').focus();await pause(2100);
  check(await motion.locator('#sc-form [name="shell_name"]').inputValue()==='保留中的外壳草稿','polling preserves the shell draft');
  check(await motion.locator('#sc-form [name="shell_name"]').evaluate(el=>el===document.activeElement),'polling preserves keyboard focus');
  check(await motion.evaluate(count=>DeskBotWorkbenchMotion.state().entrances===count&&DeskBotWorkbenchMotion.state().activeAnimations===0,entranceCount),'polling never restarts an entrance');
  await route(motion,'characters');await motion.locator('[data-character-add="grok-round-personal-v1"]').click();
  await motion.locator('[name="display_name"]').fill('正在编辑的小圆');
  await motion.waitForFunction(()=>!!document.querySelector('#character-canvas'));
  const canvas=await motion.locator('#character-canvas').elementHandle();
  await motion.locator('[data-character-semantic="happy"]').click();
  check(await canvas.evaluate(el=>el===document.querySelector('#character-canvas')),'semantic selection retains the preview canvas');
  await route(motion,'shells');await route(motion,'characters');
  check(await motion.locator('[name="display_name"]').inputValue()==='正在编辑的小圆','route transition preserves character draft');
  const opener=motion.locator('[data-action="device-info"]');await opener.click();await motion.waitForFunction(()=>document.querySelector('#studio-dialog').open);
  await motion.locator('#studio-dialog [data-action="close-dialog"]').first().click();
  check(await motion.evaluate(()=>!document.querySelector('#studio-dialog').open),'dialog close keeps native closed semantics');
  check(await opener.evaluate(el=>el===document.activeElement),'dialog close restores opener focus');
  await route(motion,'shells');await summary.click();await motion.emulateMedia({reducedMotion:'reduce'});
  await motion.waitForFunction(()=>DeskBotWorkbenchMotion.state().folds===0&&DeskBotWorkbenchMotion.state().activeAnimations===0);
  check(await fold.evaluate(el=>el.open&&!el.style.height),'changing reduced-motion preference completes an in-flight disclosure');
  const hardware=await fetch(origin+'/__fixture/requests').then(r=>r.json());
  check(hardware.requests.length===0,'visual checks issue zero device commands');
  check(posts.every(p=>!p.startsWith('/api/control')&&!p.includes('/activate')&&!p.includes('/install')),'styling never issues activation or installation requests');
  check(errors.length===0,'no browser JavaScript errors: '+errors.join('\n'));
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({result:'passed',checks,layouts,midpoint,posts,device_commands:hardware.requests.length,hardware_accessed:false},null,2)+'\n');
  console.log(`${checks.length} visual and motion checks passed`);
})().catch(error=>{fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:error.stack,checks,layouts,errors,posts},null,2));console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),pause(2500)]);}
  if(folder&&path.dirname(folder)===path.join(root,'tmp')&&path.basename(folder).startsWith('workbench-visual-browser-'))fs.rmSync(folder,{recursive:true,force:true});
});
