'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3a-browser-20261104');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8768';
let browser;
const checks=[];
function fixture(status='disabled',freshness='disabled',state=null){
  return {schema:'deskbot.body_observation/v1',source:'usb_console',read_only:true,controls_available:false,
    generated_at:'2026-11-04T04:00:00.000Z',hardware_accessed:status!=='disabled',device_written:false,
    connection:{status,port:status==='disabled'?null:'COM4',reason:'browser_test_fixture'},freshness,fresh_for_ms:30000,
    live:freshness==='fresh',timeline_id:'observation-'+ 'a'.repeat(32),device_log_time_ms:10000,
    last_seen_at:state?'2026-11-04T04:00:00.000Z':null,last_seen_age_ms:state?500:null,
    application_state:state?{value:state,received_at:'2026-11-04T04:00:00.000Z',age_ms:500}:null,
    internal_memory:state?{free_internal_bytes:102400,minimum_internal_bytes:90000,received_at:'2026-11-04T04:00:00.000Z',age_ms:500}:null,
    events:[],dropped_lines:0};
}
async function nav(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);}
async function shot(page,name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function exercise(){
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  const page=await context.newPage(),errors=[],calls=[];
  let current=fixture(),failure=false;
  page.on('pageerror',error=>errors.push(String(error)));
  await page.route('**/api/body/**',async route=>{
    const p=new URL(route.request().url()).pathname;calls.push({path:p,method:route.request().method()});
    if(failure&&p==='/api/body/state')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'测试：服务断开'})});
    let data=current;
    if(p==='/api/body/ports')data={available:true,ports:[{port:'COM4',usb_id:'303A:1001',key:'a'.repeat(64)}]};
    if(p==='/api/body/connect'){
      assert.deepEqual(route.request().postDataJSON(),{port:'COM4',key:'a'.repeat(64)});
      current=fixture('connected','waiting');data=current;
    }
    if(p==='/api/body/disconnect'){assert.deepEqual(route.request().postDataJSON(),{});current=fixture();data=current;}
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/#connection');await page.waitForFunction(()=>window.DeskBotStudio?.observation);
  await page.waitForFunction(()=>DeskBotStudio.observation().ports.length===1);
  assert.equal(calls.filter(r=>r.method==='POST').length,0,'loading never opens a device');
  assert.equal(await page.locator('#main-nav a').count(),6);
  assert.equal(await page.locator('.e3a-simulator').getAttribute('open'),null);
  await page.locator('[data-e3a-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.freshness==='waiting');
  assert.match(await page.locator('.e3a-pill').textContent(),/等待/);
  current=fixture('connected','fresh','listening');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.application_state?.value==='listening');
  assert.match(await page.locator('.e3a-facts').textContent(),/聆听/);
  assert.match(await page.locator('.e3a-facts').textContent(),/100.0 KiB/);
  await shot(page,'live-desktop');
  current=fixture('connected','fresh','speaking');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.application_state?.value==='speaking');
  await page.locator('.e3a-events summary').click();
  await page.waitForTimeout(1200);assert.equal(await page.locator('.e3a-events').getAttribute('open'),'');
  current=fixture('connected','stale','speaking');current.last_seen_age_ms=31000;
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.freshness==='stale');
  assert.match(await page.locator('.e3a-pill').textContent(),/过期/);
  current=fixture('missing','disconnected','speaking');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='missing');
  assert.match(await page.locator('.e3a-facts').textContent(),/断开/);
  current=fixture('connected','waiting');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.freshness==='waiting');
  assert.match(await page.locator('.e3a-facts').textContent(),/尚未观察到/);
  failure=true;
  await page.waitForFunction(()=>DeskBotStudio.observation().error);
  assert.equal(await page.evaluate(()=>DeskBotStudio.observation().device.live),false);
  failure=false;current=fixture('connected','fresh','idle');
  await page.waitForFunction(()=>!DeskBotStudio.observation().error&&DeskBotStudio.observation().device.live);
  await page.locator('[data-e3a-action="disconnect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='disabled');
  const before=structuredClone(current);
  await page.locator('.e3a-simulator>summary').click();
  await page.locator('[data-sim-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  await page.locator('[data-sim-action="send"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().receipts.at(-1)?.status==='dispatched');
  assert.deepEqual(current,before,'simulation cannot update body state');
  const pending=page.waitForEvent('download');await page.locator('[data-sim-action="export-catalog"]').click();
  const download=await pending,target=path.join(out,'saved-catalog.json');await download.saveAs(target);
  const catalog=JSON.parse(fs.readFileSync(target));assert.equal(catalog.live,false);assert.equal(catalog.source,'saved_release_record');
  await nav(page,'editor');assert.equal(await page.locator('#graph-world').count(),1);
  await nav(page,'roles');assert.equal(await page.locator('.role-card').count(),3);
  await nav(page,'publish');await page.waitForFunction(()=>DeskBotStudio.release().connection==='ready');
  await page.locator('[data-package-action="generate"]').first().click();
  await page.waitForFunction(()=>DeskBotStudio.release().current&&!DeskBotStudio.release().busy,{},{timeout:30000});
  assert.ok(await page.locator('a[download]').count()>=2);
  await nav(page,'connection');
  for(const width of [320,390,1440]){
    await page.setViewportSize({width,height:1000});await page.waitForTimeout(350);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow '+width);
    for(const entry of await page.locator('#main-nav a').all()){
      const b=await entry.boundingBox();assert.ok(b.x>=0&&b.x+b.width<=width+1,'visible navigation '+width);
    }
    await shot(page,'connection-'+width);
  }
  await page.locator('#theme-toggle').click();await shot(page,'connection-dark');
  assert.ok(calls.every(r=>['/api/body/ports','/api/body/state','/api/body/connect','/api/body/disconnect'].includes(r.path)));
  assert.deepEqual(errors,[]);
  await context.close();
  checks.push('initial_page_read_only','six_page_navigation','simulator_collapsed_by_default','usb_waiting_without_state_claim',
    'listening_speaking_idle','memory_units','expanded_details_survive_poll','stale_label','disconnect_label',
    'reconnect_clears_state','service_failure_clears_live','service_recovery','explicit_stop','simulation_is_separate',
    'saved_catalog_stays_non_live','editor_roles_regression','real_package_generation','320_390_1440_layout',
    'all_navigation_visible','dark_theme','no_hardware_command_routes','no_page_errors');
}
async function offline(){
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
  await page.goto(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e3a-20261104/index.html')).href+'#connection');
  await page.waitForFunction(()=>window.DeskBotStudio?.observation().error);
  assert.match(await page.locator('.e3a-error').textContent(),/本机工作台/);
  assert.equal(await page.locator('[data-e3a-action="connect"]').isDisabled(),true);
  await page.locator('.e3a-simulator>summary').click();await page.locator('[data-sim-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  assert.equal(await page.locator('.sim-motion-pet').count(),0);
  const canvas=page.locator('.sim-screen canvas');const first=await canvas.evaluate(c=>c.toDataURL());
  await page.waitForTimeout(350);assert.equal(await canvas.evaluate(c=>c.toDataURL()),first);
  await context.close();checks.push('public_offline_still_works','explicit_local_service_hint','reduced_motion');
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  await exercise();await offline();await browser.close();
  const result={result:'passed',checks,body_data_source:'synthetic_browser_fixture',hardware_accessed:false,device_written:false};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
