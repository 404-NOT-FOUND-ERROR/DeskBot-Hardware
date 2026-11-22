import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {once} from 'node:events';

// This UI fixture isolates connection updates from device I/O and paid calls.
const {chromium}=createRequire(import.meta.url)(process.env.DESKBOT_PLAYWRIGHT_MODULE || 'playwright');
const pageSource=readFileSync(new URL('../integrations/client-conversation/conversation.html',import.meta.url));
const output=resolve(import.meta.dirname,'../tmp/device-voice-trial-browser-20261119');
mkdirSync(output,{recursive:true});
const state={ready:false,configured:false};
const history=[{id:'seed:user',speaker:'你',text:'我喜欢靛蓝色。'},{id:'seed:assistant',speaker:'喵呜',text:'我记住了。'}];
const setupUrl='http://192.168.20.3:8891/deskbot-voice/'+'c'.repeat(24);
const status=()=>({
  character_id:'trial-character',installations:[{display_name:'圆团'}],model:{configured:true},
  connection:{voice:'xiaozhi_device_trial'},
  voice_pilot:{peer_ready:state.ready,session_state:state.ready?'ready':null,setup_url:setupUrl,
    device_setup:{state:state.configured?'configured':'waiting'},last_outcome:state.ready?{status:'sent'}:null},
});
const server=createServer(async (request,response)=>{
  const path=new URL(request.url,'http://127.0.0.1').pathname;
  if (path==='/conversation') {
    response.writeHead(200,{'content-type':'text/html; charset=utf-8'});
    response.end(pageSource);
    return;
  }
  let value;
  if (path==='/api/integration/status') value=status();
  if (path==='/api/chat/history') value={messages:history};
  if (path==='/api/chat') {
    const chunks=[];
    for await (const chunk of request) chunks.push(chunk);
    const text=JSON.parse(Buffer.concat(chunks)).message;
    history.push({id:'text:user',speaker:'你',text},{id:'text:assistant',speaker:'喵呜',text:'是靛蓝色。'});
    value={turn:{reply:'是靛蓝色。'}};
  }
  response.writeHead(value?200:404,{'content-type':'application/json'});
  response.end(JSON.stringify(value??{error:'not_found'}));
});
await once(server.listen(0,'127.0.0.1'),'listening');
let browser;
try {
  browser=await chromium.launch({headless:true,executablePath:process.env.DESKBOT_BROWSER_EXE});
  const context=await browser.newContext({viewport:{width:1280,height:800},permissions:['clipboard-read','clipboard-write']});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const root=`http://127.0.0.1:${server.address().port}`;
  await page.goto(root+'/conversation');
  await page.locator('#voice-status').filter({hasText:'等待桌宠接入'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'发送'}).isEnabled(),true);
  await page.getByRole('button',{name:'复制试用地址'}).click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),setupUrl);
  await page.locator('#message').fill('先保留我的草稿');
  await page.locator('#message').focus();
  state.configured=true;
  await page.locator('#voice-status').filter({hasText:'配置已下发'}).waitFor();
  assert.equal(await page.locator('#message').inputValue(),'先保留我的草稿');
  assert.equal(await page.locator('#message').evaluate(e=>document.activeElement===e),true);
  history.push({id:'voice:user',speaker:'你',text:'我最喜欢什么颜色？'},
    {id:'voice:assistant',speaker:'喵呜',text:'靛蓝色呀。'});
  state.ready=true;
  await page.getByText('靛蓝色呀。',{exact:true}).waitFor();
  assert.equal(await page.locator('#message').inputValue(),'先保留我的草稿');
  await page.locator('#voice-status').filter({hasText:'最近一条语音已发出'}).waitFor();
  await page.locator('#message').fill('你记住了吗？');
  await page.getByRole('button',{name:'发送'}).click();
  await page.getByText('是靛蓝色。',{exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#message').value==='');
  await page.screenshot({path:resolve(output,'desktop.png')});
  await page.setViewportSize({width:320,height:740});
  await page.screenshot({path:resolve(output,'narrow.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'passed',scope:'browser_fixture',viewports:[1280,320],
    clipboard:true,voice_history:true,draft_and_focus_preserved:true,text_send:true,physical_device_tested:false}));
} finally {
  await browser?.close();
  await new Promise(done=>server.close(done));
}
