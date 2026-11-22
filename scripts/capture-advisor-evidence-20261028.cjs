const fs = require('node:fs/promises');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');
const repo = path.resolve(__dirname, '..');
const project = path.resolve(repo, '../..');
const out = path.join(repo, 'artifacts/advisor-defense-20261028/media');
const checks = [];
const hash = b => createHash('sha256').update(b).digest('hex');
async function main(){
  await fs.mkdir(out,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page = await browser.newPage({viewport:{width:1780,height:1220},deviceScaleFactor:1.5});
  const requests=[], errors=[];
  page.on('request',r=>{if(!r.url().startsWith('file:')&&!r.url().startsWith('data:'))requests.push(r.url())});
  page.on('pageerror',e=>errors.push(String(e)));
  const graphPath=path.join(repo,'artifacts/expression-node-preview-e1-19-20261024/index.html');
  await page.goto(pathToFileURL(graphPath).href);
  await page.waitForTimeout(1000);
  await page.locator('#play-all').click();
  await page.locator('#frame').fill('8');
  await page.locator('#frame').dispatchEvent('input');
  await page.waitForTimeout(200);
  await page.addStyleTag({content:'.canvas-viewport{height:900px;max-height:none}.canvas-shell{width:1030px}.canvas{transform:scale(1)}'});
  await page.locator('.canvas-shell').screenshot({path:path.join(out,'node-workbench.png')});
  for(const [selector,name] of [['#preview-before','happy-face'],['#preview-current','shocked-face']]){
    const data=await page.locator(selector).evaluate(c=>c.toDataURL('image/png').split(',')[1]);
    await fs.writeFile(path.join(out,name+'.png'),Buffer.from(data,'base64'));
  }
  checks.push({kind:'actual_local_workbench_screenshot',source:path.relative(repo,graphPath).replaceAll('\\','/'),sha256:hash(await fs.readFile(graphPath)),output:'node-workbench.png',note:'Actual DOM capture. Viewport expanded to include all graph nodes; no mapping changed. EAF images are decoded reference composites, not LCD photographs.'});
  const reviewPath=path.join(repo,'artifacts/expression-e1-28-semantic-review-20261027/index.html');
  await page.goto(pathToFileURL(reviewPath).href);
  await page.waitForFunction(()=>document.body.dataset.ready==='true');
  await page.waitForTimeout(500);
  if(await page.locator('#play').getAttribute('aria-pressed')==='true') await page.locator('#play').click();
  for(const semantic of ['happy','embarrassed','sleepy','neutral']){
    const canvas=page.locator(`.semantic-card[data-semantic="${semantic}"] canvas`);
    const data=await canvas.evaluate(c=>c.toDataURL('image/png').split(',')[1]);
    await fs.writeFile(path.join(out,semantic+'-face.png'),Buffer.from(data,'base64'));
  }
  await page.locator('#step').click();
  await page.locator('#graph').count().then(async count=>{if(count) await page.locator('#graph').screenshot({path:path.join(out,'semantic-branches.png')})});
  await page.screenshot({path:path.join(out,'semantic-review.png'),fullPage:false});
  checks.push({kind:'decoded_reference_EAF_composite',source:path.relative(repo,reviewPath).replaceAll('\\','/'),sha256:hash(await fs.readFile(reviewPath)),outputs:['happy-face.png','embarrassed-face.png','sleepy-face.png','neutral-face.png','semantic-review.png'],note:'Official EAF frame atlas reference; not a device photograph or evidence of runtime LCD completion.'});
  const manifestPath=path.join(project,'protocol/magnetic-validation-20261018.manifest.json');
  const manifest=JSON.parse(await fs.readFile(manifestPath,'utf8'));
  const magnetic=[];
  for(const id of ['eval-shell-a-ab-01','eval-shell-b-01','eval-unknown-01','eval-removed-02','eval-replace-01']){
    const seg=manifest.segments.find(s=>s.id===id);
    const file=path.join(project,seg.m1.path,'samples.jsonl');
    const bytes=await fs.readFile(file);
    if(hash(bytes)!==seg.m1.files['samples.jsonl'])throw Error('Sample hash mismatch: '+id);
    const samples=bytes.toString('utf8').trim().split(/\r?\n/).map(JSON.parse);
    if(samples.length!==seg.sample_count)throw Error('Sample count mismatch: '+id);
    magnetic.push({id,ground_truth:seg.ground_truth,source:seg.m1.path+'/samples.jsonl',sha256:hash(bytes),count:samples.length,status_counts:seg.m2.status_counts,xyz:samples.map(r=>[r.x,r.y,r.z])});
  }
  await fs.writeFile(path.join(out,'magnetic-evidence.json'),JSON.stringify({source:'M2 real recordings, 2026-10-18',sensor:'BMM150',units:'transport_counts',electrical_frame_capture:false,independent_installation_accuracy:null,limits:'Single device/base/environment. Continuous frames are not independent trials. Removed condition produced unknown, not a confirmed detached state.',segments:magnetic},null,2));
  await fs.writeFile(path.join(out,'visual-source-manifest.json'),JSON.stringify({date:'2026-10-28',hardware_accessed:false,network_requests:requests,page_errors:errors,sources:checks},null,2));
  await browser.close();
  if(errors.length||requests.length)throw Error(JSON.stringify({errors,requests}));
  console.log(JSON.stringify({out,sources:checks.length,magneticSegments:magnetic.length,samples:magnetic.map(s=>s.count),networkRequests:requests.length,pageErrors:errors.length}));
}
main().catch(e=>{console.error(e);process.exitCode=1});
