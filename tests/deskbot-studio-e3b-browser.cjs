'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.DESKBOT_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'tmp/deskbot-studio-e3b-browser-20261104');
const origin=process.env.DESKBOT_STUDIO_URL||'http://127.0.0.1:8769';
const checks=[];let browser;

function fixture(status='disabled',identity='disconnected',boot='b'){
  const connected=status==='connected',hello=['confirmed','stale'].includes(identity);
  return {schema:'deskbot.body_observation/v2',source:'usb_console',read_only:true,controls_available:false,
    generated_at:'2026-11-04T04:00:00.000Z',hardware_accessed:status!=='disabled',device_written:false,
    connection:{status,port:status==='disabled'?null:'COM4',reason:'synthetic_browser_fixture'},
    freshness:connected?(hello?'fresh':'waiting'):status==='disabled'?'disabled':'disconnected',fresh_for_ms:30000,
    live:connected&&hello,timeline_id:'observation-'+'a'.repeat(32),device_log_time_ms:10000,
    last_seen_at:hello?'2026-11-04T04:00:00.000Z':null,last_seen_age_ms:hello?500:null,
    application_state:null,internal_memory:null,events:[],dropped_lines:0,
    identity:{status:identity,source:'device_announcement',received_at:hello?'2026-11-04T04:00:00.000Z':null,
      age_ms:hello?(identity==='stale'?31000:500):null,reason:'synthetic_browser_fixture',rejected_messages:0,
      hello:hello?{schema:'deskbot.device_link/v1',message_type:'hello',device_id:'device-'+'a'.repeat(32),
        boot_id:'boot-'+boot.repeat(32),sequence:3,uptime_ms:31000,firmware:{edition:'E3b',version:'2.0.5',app_elf_sha256:'c'.repeat(64)},
        hardware:{chip:'esp32s3',board:'esp_vocat',flash_bytes:33554432,psram_bytes:16777216},
        capabilities:['device_identity'],transport:'usb_console',controls_available:false,catalog_id:null,
        limits:{max_frame_bytes:2048,hello_interval_ms:10000}}:null}};
}
async function nav(page,view){await page.locator(`#main-nav [data-view="${view}"]`).click();await page.waitForFunction(v=>DeskBotStudio.state().view===v,view);}
async function shot(page,name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}

async function exercise(){
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  const page=await context.newPage(),errors=[],calls=[];let current=fixture(),failure=false;
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/api/body/**',async route=>{
    const p=new URL(route.request().url()).pathname;calls.push({path:p,method:route.request().method()});
    if(failure&&p==='/api/body/state')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'合成测试：服务离线'})});
    let data=current;
    if(p==='/api/body/ports')data={available:true,ports:[{port:'COM4',usb_id:'303A:1001',key:'a'.repeat(64)}]};
    if(p==='/api/body/connect'){
      assert.deepEqual(route.request().postDataJSON(),{port:'COM4',key:'a'.repeat(64)});
      current=fixture('connected','waiting');data=current;
    }
    if(p==='/api/body/disconnect'){assert.deepEqual(route.request().postDataJSON(),{});current=fixture();data=current;}
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/#connection');
  await page.waitForFunction(()=>DeskBotStudio?.observation().ports.length===1);
  assert.equal(calls.filter(r=>r.method==='POST').length,0,'page load is passive');
  assert.equal(await page.locator('#main-nav a').count(),6);
  assert.equal(await page.locator('.e3b-handshake').count(),1);
  assert.match(await page.locator('.e3b-status').textContent(),/等待 USB/);
  await page.locator('[data-e3a-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='waiting');
  assert.match(await page.locator('.e3b-status').textContent(),/等待设备/);
  assert.match(await page.locator('.e3b-wait').textContent(),/E1.42/);
  assert.equal(await page.locator('.e3b-chain .done').count(),1);
  await shot(page,'waiting-older-firmware');
  current=fixture('connected','confirmed');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='confirmed');
  await page.locator('.e3b-info').waitFor();
  assert.equal(await page.locator('.e3b-chain .done').count(),2);
  assert.match(await page.locator('.e3b-info').textContent(),/E3b.*2.0.5/);
  assert.match(await page.locator('.e3b-info').textContent(),/32 MiB \/ 16 MiB/);
  assert.match(await page.locator('.e3b-caption').first().textContent(),/设备自报/);
  assert.equal(await page.locator('.e3a-simulator').getAttribute('open'),null);
  await page.locator('.e3a-events summary').click();
  await page.waitForTimeout(2200);
  assert.equal(await page.locator('.e3a-events').getAttribute('open'),'');
  assert.equal(await page.locator('.e3b-handshake').count(),1);
  assert.equal(await page.locator('.e3b-handshake').evaluate(e=>getComputedStyle(e).animationName),'none','polling does not replay entrance animation');
  await shot(page,'identity-desktop');
  current=fixture('connected','confirmed');current.identity.hello.hardware.flash_bytes=0;
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.hello?.hardware.flash_bytes===0);
  assert.match(await page.locator('.e3b-info').textContent(),/待确认 \/ 16 MiB/);
  current=fixture('connected','stale');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='stale');
  assert.match(await page.locator('.e3b-status').textContent(),/等待更新/);
  assert.match(await page.locator('.e3b-caption').first().textContent(),/上次收到/);
  assert.equal(await page.locator('.e3b-chain .done').count(),1);
  current=fixture('missing','disconnected');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='missing');
  assert.equal(await page.locator('.e3b-info').count(),0);
  assert.equal(await page.locator('.e3b-chain .done').count(),0);
  current=fixture('connected','waiting');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='waiting');
  assert.equal(await page.locator('.e3b-info').count(),0);
  current=fixture('connected','confirmed','d');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.hello?.boot_id.endsWith('dddddddd'));
  assert.match(await page.locator('.e3b-info').textContent(),/dddddddd/);
  failure=true;
  await page.waitForFunction(()=>DeskBotStudio.observation().error);
  assert.equal(await page.locator('.e3b-info').count(),0,'service loss cannot leave identity confirmed');
  assert.match(await page.locator('.e3b-status').textContent(),/等待 USB/);
  failure=false;current=fixture('connected','confirmed');
  await page.waitForFunction(()=>!DeskBotStudio.observation().error&&DeskBotStudio.observation().device.connection.status==='connected');
  await page.locator('[data-e3a-action="disconnect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.connection.status==='disabled');
  const before=structuredClone(current);
  await page.locator('.e3a-simulator>summary').click();
  await page.locator('[data-sim-action="connect"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().connected);
  await page.locator('[data-sim-action="send"]').click();
  await page.waitForFunction(()=>DeskBotStudio.simulation().receipts.at(-1)?.status==='dispatched');
  assert.deepEqual(current,before,'simulation stays separate');
  const pending=page.waitForEvent('download');await page.locator('[data-sim-action="export-catalog"]').click();
  const download=await pending,target=path.join(out,'saved-catalog.json');await download.saveAs(target);
  const catalog=JSON.parse(fs.readFileSync(target));assert.equal(catalog.live,false);assert.equal(catalog.revision,'E1.18');
  await nav(page,'editor');assert.equal(await page.locator('#graph-world').count(),1);
  await nav(page,'roles');assert.equal(await page.locator('.role-card').count(),3);
  await nav(page,'publish');await page.waitForFunction(()=>DeskBotStudio.release().connection==='ready');
  await page.locator('[data-package-action="generate"]').first().click();
  await page.waitForFunction(()=>DeskBotStudio.release().current&&!DeskBotStudio.release().busy,{},{timeout:30000});
  assert.ok(await page.locator('a[download]').count()>=2);
  current=fixture('connected','confirmed');await nav(page,'connection');
  await page.waitForFunction(()=>DeskBotStudio.observation().device?.identity.status==='confirmed');
  for(const width of [320,390,1440]){
    await page.setViewportSize({width,height:1100});await page.waitForTimeout(300);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no overflow '+width);
    for(const entry of await page.locator('#main-nav a').all()){
      const b=await entry.boundingBox();assert.ok(b.x>=0&&b.x+b.width<=width+1,'visible navigation '+width);
    }
    await shot(page,'identity-'+width);
  }
  await page.locator('#theme-toggle').click();await shot(page,'identity-dark');
  assert.ok(calls.every(r=>['/api/body/ports','/api/body/state','/api/body/connect','/api/body/disconnect'].includes(r.path)));
  assert.deepEqual(errors,[]);await context.close();
  checks.push('passive_page_load','six_page_navigation','older_firmware_waiting','device_identity_version_capacity',
    'four_step_progress','identity_source_label','saved_catalog_separate','details_survive_poll',
    'single_identity_panel','no_repeated_entrance_animation','unknown_capacity_label','identity_stale','disconnect_clears_identity',
    'reconnect_waits_for_hello','new_boot_display','service_failure_clears_confirmation','service_recovery',
    'explicit_stop','simulation_is_separate','saved_catalog_download','editor_roles_regression','real_package_generation',
    '320_390_1440_layout','all_navigation_visible','dark_theme','no_device_command_routes','no_page_errors');
}

async function offline(){
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
  await page.goto(pathToFileURL(path.join(root,'artifacts/deskbot-studio-e3b-20261104/index.html')).href+'#connection');
  await page.waitForFunction(()=>window.DeskBotStudio?.observation().error);
  assert.match(await page.locator('.e3a-error').textContent(),/本机工作台/);
  assert.equal(await page.locator('[data-e3a-action="connect"]').isDisabled(),true);
  assert.match(await page.locator('.e3b-status').textContent(),/等待 USB/);
  assert.equal(await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches),true);
  assert.equal(await page.locator('.e3b-handshake').evaluate(e=>e.getAnimations().length),0);
  await context.close();checks.push('public_offline_page','local_service_hint','reduced_motion');
}

(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  await exercise();await offline();await browser.close();
  const result={result:'passed',checks,body_data_source:'synthetic_browser_fixture',hardware_accessed:false,device_written:false};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
