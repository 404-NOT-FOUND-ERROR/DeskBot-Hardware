'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {chromium}=require(process.argv[2]||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1024}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>errors.push(r.url()+': '+r.failure()?.errorText));
  const response=await page.goto('http://127.0.0.1:8782/expressions/');assert.equal(response.status(),200);
  await page.waitForFunction(()=>document.querySelector('#connection')?.textContent.includes('360 圆屏'));
  assert.equal(await page.locator('.node.semantic').count(),27);
  await page.locator('#new-project').click();
  await page.waitForFunction(()=>document.querySelector('#projects').options.length>=1);
  await page.waitForFunction(()=>!document.querySelector('#upload').disabled);
  const fixture=process.argv[4];await page.locator('#upload').setInputFiles(fixture);
  try{await page.waitForFunction(()=>document.querySelector('#assets').options.length===2,null,{timeout:10000});}
  catch(error){console.log('upload_debug',JSON.stringify({toast:await page.locator('#toast').textContent(),projects:await page.locator('#projects').innerText(),assets:await page.locator('#assets').innerText(),errors}));throw error;}
  const firstAsset=await page.locator('#assets').inputValue();assert.ok(firstAsset);
  await page.locator('#bind').click();
  await page.waitForFunction(()=>document.querySelector('#binding-tag').textContent==='新素材绑定');
  assert.equal(await page.locator('#assets').inputValue(),firstAsset);
  const started=page.waitForResponse(r=>r.url().endsWith('/api/expressions/build')&&r.request().method()==='POST');
  await page.locator('#build').click();const buildRequest=await(await started).json();
  let state,ready=false;
  for(let attempt=0;attempt<450;attempt++){
   state=await(await page.request.get('http://127.0.0.1:8782/api/expressions/state')).json();
   if(state.job?.project===buildRequest.project&&state.job?.revision===buildRequest.revision&&['completed','failed'].includes(state.job?.status)){ready=true;break;}
   await new Promise(ok=>setTimeout(ok,200));
  }
  assert.equal(ready,true,'build did not complete');
  assert.equal(state.job.status,'completed',JSON.stringify(state.job));
  const active=state.projects.find(p=>p.id===state.job.project);assert.equal(active.build.status,'ready');
  assert.equal(active.build.report.canvas,360);const manifest=await(await page.request.get('http://127.0.0.1:8782/api/expressions/file?'+new URLSearchParams({project:active.id,revision:active.revision,name:'delivery.json'}))).json();
  assert.equal(manifest.app_sha256,active.build.manifest.app_sha256);assert.equal(manifest.resource_sha256,active.build.report.image_sha256);
  const pixels=await page.locator('#face').evaluate(c=>[...c.getContext('2d').getImageData(180,180,1,1).data]);assert.equal(pixels.length,4);assert.ok(pixels.some(v=>v>0));
  try{await page.waitForFunction(()=>document.querySelector('#download').hidden===false);}
  catch(error){console.log('delivery_debug',JSON.stringify({toast:await page.locator('#toast').textContent(),projects:await page.locator('#projects').inputValue(),pipeline:await page.locator('#pipeline').innerText(),build:await page.locator('#build-result').innerText(),errors,active:active.id}));await page.screenshot({path:process.argv[5],fullPage:true});throw error;}
  const download=await page.request.get(new URL(await page.locator('#download').getAttribute('href'),'http://127.0.0.1:8782').href);assert.equal(download.status(),200);assert.ok((await download.body()).length>100000);
  await page.screenshot({path:process.argv[5],fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:process.argv[6],fullPage:true});assert.deepEqual(errors,[]);
  console.log(JSON.stringify({uploaded:true,bound_without_selection_loss:true,built:true,production_validation:active.build.native.valid,
   package_bytes:active.build.report.package_bytes,free_bytes:active.build.report.partition_free_bytes,download:true,mobile:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
