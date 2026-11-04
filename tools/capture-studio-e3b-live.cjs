'use strict';
// Capture the actual local connection page after the identity deployment.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),run=process.argv[2];
assert.match(run||'',/^e3b-live-[0-9]{8}-[a-z0-9-]{1,32}$/);
const out=path.join(root,'tmp',run),expected=JSON.parse(fs.readFileSync(path.join(out,'restart-result.json'),'utf8')).boot.hello;
let browser;
(async()=>{
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto('http://127.0.0.1:8766/#connection');
  await page.waitForFunction(()=>window.DeskBotStudio?.observation().device?.identity?.status==='confirmed');
  const hello=await page.evaluate(()=>DeskBotStudio.observation().device.identity.hello);
  assert.equal(hello.device_id,expected.device_id);
  assert.equal(hello.boot_id,expected.boot_id);
  assert.deepEqual(hello.firmware,expected.firmware);
  assert.match(await page.locator('.e3b-status').textContent(),/身份已收到/);
  assert.equal(await page.locator('.e3b-chain .done').count(),2);
  await page.screenshot({path:path.join(out,'studio-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,'studio-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);
  const result={result:'passed',source:'live_local_service',hardware_accessed:true,serial_commands:false,
    identity_confirmed:true,firmware_matches_boot:true,connection_steps_done:2,viewport_widths:[1440,390],page_errors:errors};
  fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify(result,null,2)+'\n');
  await browser.close();console.log(JSON.stringify(result));
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
