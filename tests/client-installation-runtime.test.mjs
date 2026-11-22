import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createUsbClientRuntime} from '../scripts/serve-client-expression-4b1b.mjs';

const root=process.env.DESKBOT_HANDOFF_CLIENT;
if(!root)throw new Error('explicit_client_checkout_required');
const archive=JSON.parse(readFileSync(new URL('../artifacts/client-installation-sync-20261117-r3/accepted-archive-replay.json',import.meta.url),'utf8'));
const sourceId='installation-runtime-test';
const now=()=>new Date('2026-11-17T12:00:00Z');
function envelope(){return {schema:'deskbot.owner-installation-handoff.v1',source_id:sourceId,source_revision:66,
  installation:structuredClone(archive.state.current.installation),device:structuredClone(archive.state.current.device_observation.value)};}
const post=value=>({method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Integration':'shell-installation-v1'},body:JSON.stringify(value)});
async function fixture(t){
  const folder=mkdtempSync(join(tmpdir(),'installation-runtime-'));
  const database=join(folder,'client.sqlite');
  let host;
  const close=async()=>{if(host){await new Promise(done=>{host.server.close(done);host.server.closeAllConnections();});host.persistence.close();host=null;}};
  const start=async source=>{
    host=await createUsbClientRuntime({clientRoot:root,database,sourceId:source,now});
    await new Promise(done=>host.server.listen(0,'127.0.0.1',done));
    return `http://127.0.0.1:${host.server.address().port}`;
  };
  t.after(async()=>{await close();rmSync(folder,{recursive:true,force:true});});
  return {start,close,get host(){return host;}};
}
async function register(url){
  const value=envelope();
  const result=await fetch(url+'/api/devices/hello',post({device_id:value.installation.device_id,
    character_id:value.installation.character_id,hardware:'esp32-s3',firmware:'E3f-9d.3',capabilities:{'display.expression':true}}));
  assert.equal(result.status,201);
}

test('original USB runtime receives accepted B and preserves world across restart and old-mode rollback',async t=>{
  const f=await fixture(t);
  let url=await f.start(sourceId);await register(url);
  const world=await (await fetch(url+'/api/world/state')).json();
  const first=await fetch(url+'/api/hardware/installations',post(envelope()));
  assert.equal(first.status,200,await first.clone().text());const receipt=(await first.json()).receipt;
  assert.deepEqual(await (await fetch(url+'/api/world/state')).json(),world);
  const stored=f.host.persistence.get('hardware.current-installation',`${receipt.character_id}:${receipt.device_id}`);
  await f.close();url=await f.start(sourceId);
  const again=await fetch(url+'/api/hardware/installations',post(envelope()));
  const result=await again.json();assert.equal(result.duplicate,true);assert.deepEqual(result.receipt,receipt);
  assert.deepEqual(await (await fetch(url+'/api/world/state')).json(),world);
  await f.close();url=await f.start(undefined);
  assert.equal((await fetch(url+'/api/hardware/installation')).status,404);
  assert.deepEqual(f.host.persistence.get('hardware.current-installation',`${receipt.character_id}:${receipt.device_id}`),stored);
  assert.deepEqual(await (await fetch(url+'/api/world/state')).json(),world);
  assert.equal((await fetch(url+'/health')).status,200);
});

test('runtime uses its original device registration for ownership and keeps prior routes',async t=>{
  const f=await fixture(t),url=await f.start(sourceId);
  assert.equal((await fetch(url+'/api/hardware/installations',post(envelope()))).status,409);
  await register(url);
  const wrong=envelope();wrong.source_id='another-source';
  assert.equal((await fetch(url+'/api/hardware/installations',post(wrong))).status,409);
  assert.equal(f.host.persistence.count('hardware.installations'),0);
  const message=await fetch(url+'/api/chat',post({event_id:'runtime-chat',correlation_id:'runtime-chat',
    character_id:envelope().installation.character_id,message:'你好'}));
  assert.equal(message.status,202,await message.clone().text());
  assert.equal((await message.json()).accepted,true);
  assert.equal((await fetch(url+'/api/outbox')).status,200);
});
