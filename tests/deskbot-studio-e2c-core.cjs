'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const Core=require('../scripts/deskbot-studio-e2c-core.js');
const root=path.resolve(__dirname,'..'),catalog=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2c-20261104/expression-catalog.json'),'utf8'));

function fixture(delay=100){
  let now=0;const sim=Core.create(catalog,{clock:()=>now,dispatchDelay:delay});sim.connect();
  return {sim,at:value=>{now=value;},expression:()=>sim.makeRequest({kind:'play_expression',semantic_id:'happy'}),action:()=>sim.makeRequest({kind:'play_action',action_id:'look_around'})};
}

test('catalog SHA-256 agrees across Python and JavaScript and detects changed contents',async()=>{
  await Core.verifyCatalog(catalog);
  const changed=structuredClone(catalog);changed.expressions[0].resource_id='other.eaf';
  await assert.rejects(()=>Core.verifyCatalog(changed),/digest/);
});

test('identifiers are typed rather than coerced into strings',()=>{
  const {expression}=fixture(),base=expression();
  for(const value of [null,[],{},true,12])for(const field of ['request_id','target_id','boot_id','session_id','role_id']){
    const changed=structuredClone(base);changed[field]=value;assert.throws(()=>Core.validateRequest(changed,catalog));
  }
  for(const value of [null,[],{},true,12]){const changed=structuredClone(base);changed.command.semantic_id=value;assert.throws(()=>Core.validateRequest(changed,catalog));}
});

test('same id with changed payload is rejected while a pending duplicate returns accepted',()=>{
  const {sim,expression}=fixture(),request=expression();sim.submit(request);
  assert.equal(sim.submit(request).receipt.original_status,'accepted');
  const conflict=structuredClone(request);conflict.command.semantic_id='sad';
  const result=sim.submit(conflict).receipt;assert.equal(result.status,'rejected');assert.equal(result.reason,'request_id_conflict');
  assert.equal(sim.state().execution_count,0);assert.equal(sim.state().pending.request_id,request.request_id);
});

test('client timeout can be followed by a dispatch and snapshot synchronization',()=>{
  const {sim,at,expression}=fixture(),request=expression();sim.submit(request);
  assert.equal(sim.clientTimeout(request.request_id).physical_result,'unknown');
  sim.disconnect();at(101);assert.equal(sim.advance()[0].status,'dispatched');
  assert.equal(sim.state().receipts.at(-1).status,'accepted');
  sim.connect();assert.equal(sim.state().recent_results.find(x=>x.request.request_id===request.request_id).receipt.status,'dispatched');
  assert.equal(sim.state().receipts.at(-1).status,'dispatched');
});

test('base disconnect before dispatch interrupts actions but expressions are independent',()=>{
  const {sim,at,action,expression}=fixture(),request=action();sim.submit(request);sim.setMode('base_offline');
  assert.equal(sim.state().pending,null);
  const receipt=sim.state().recent_results.find(x=>x.request.request_id===request.request_id).receipt;
  assert.equal(receipt.status,'interrupted');assert.equal(receipt.reason,'base_disconnected_before_dispatch');
  assert.equal(sim.submit(action()).receipt.reason,'base_unavailable');
  sim.submit(expression());at(101);assert.equal(sim.advance()[0].status,'dispatched');
});

test('an uncorrelated base fact leaves the pending request intact',()=>{
  const {sim,action}=fixture(),request=action();sim.submit(request);
  const fact=sim.baseFact();assert.equal(fact.request_id,null);assert.equal(fact.correlated,false);
  assert.equal(sim.state().pending.request_id,request.request_id);assert.equal(sim.state().execution_count,0);
});

test('reconnect recovers known ids but rejects new requests from an old session',()=>{
  const {sim,at,expression}=fixture(),old=expression(),unsent=expression();sim.submit(old);at(101);sim.advance();
  sim.disconnect();sim.connect();assert.equal(sim.submit(old).receipt.original_status,'dispatched');
  assert.equal(sim.submit(unsent).receipt.reason,'session_changed');
  assert.equal(sim.state().execution_count,1);
});

test('busy transition and exact deadline yield explicit non-dispatch outcomes',()=>{
  const {sim,at,expression}=fixture(300),request=expression();sim.submit(request);sim.setMode('busy');
  const interrupted=sim.state().recent_results.find(x=>x.request.request_id===request.request_id).receipt;
  assert.equal(interrupted.status,'interrupted');assert.equal(sim.state().execution_count,0);
  sim.setMode('normal');const expired=sim.makeRequest({kind:'play_expression',semantic_id:'happy'},{expiresAfterMs:300});sim.submit(expired);
  at(300);assert.equal(sim.advance()[0].status,'expired');assert.equal(sim.state().execution_count,0);
});

test('target, catalog, role and sequence mismatches have separate decisions',()=>{
  for(const [field,value,reason] of [['target_id','other-device','target_mismatch'],['catalog_id','0'.repeat(64),'catalog_or_role_changed'],['role_id','other-role','catalog_or_role_changed']]){
    const {sim,expression}=fixture(),request=expression();request[field]=value;
    assert.equal(sim.submit(request).receipt.reason,reason);assert.equal(sim.state().execution_count,0);
  }
  const {sim,expression}=fixture(),one=expression();sim.submit(one);
  const replay=expression();replay.sequence=one.sequence;assert.equal(sim.submit(replay).receipt.reason,'sequence_not_increasing');
});

test('unknown semantic, unknown action and voice-owned listen are not dispatched',()=>{
  for(const command of [{kind:'play_expression',semantic_id:'missing'},{kind:'play_expression',semantic_id:'listen'},{kind:'play_action',action_id:'missing'}]){
    const {sim}=fixture();assert.equal(sim.submit(sim.makeRequest(command)).receipt.reason,'unsupported_expression_or_action');
    assert.equal(sim.state().pending,null);assert.equal(sim.state().execution_count,0);
  }
});

test('saved E1.18 catalog is bounded and separates voice state, expressions and actions',()=>{
  assert.equal(Core.validateCatalog(catalog),catalog);
  assert.equal(catalog.expressions.length,29);
  assert.equal(catalog.actions.length,5);
  assert.equal(catalog.expressions.find(x=>x.semantic_id==='listen').requestable,false);
  assert.equal(catalog.live,false);
});

test('requests reject stale catalogs, voice state, candidate ids and extra fields',()=>{
  const base={schema:Core.SCHEMA,message_type:'request',request_id:'sim-test-1',target_id:Core.TARGET,boot_id:'sim-boot-1',session_id:'sim-session-1',sequence:1,catalog_id:catalog.catalog_id,role_id:catalog.role_id,issued_at:'2026-11-04T12:00:00.000Z',expires_after_ms:3000,command:{kind:'play_expression',semantic_id:'happy'}};
  assert.equal(Core.validateRequest(base,catalog),base);
  for(const mutate of [r=>r.catalog_id='0'.repeat(64),r=>r.command.semantic_id='listen',r=>r.command.semantic_id='shy_candidate',r=>r.command.resource_id='new.eaf',r=>r.sequence=true]){
    const changed=structuredClone(base);mutate(changed);assert.throws(()=>Core.validateRequest(changed,catalog));
  }
});

test('accepted request is dispatched by one owner and its repeated id is deduplicated',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now,wallClock:()=>new Date(1762257600000+now).toISOString(),dispatchDelay:100});
  sim.connect();const request=sim.makeRequest({kind:'play_expression',semantic_id:'happy'});
  assert.equal(sim.submit(request).receipt.status,'accepted');assert.equal(sim.state().pending.request_id,request.request_id);
  now=101;assert.equal(sim.advance()[0].status,'dispatched');
  assert.equal(sim.state().execution_count,1);assert.equal(sim.state().preview_semantic,'happy');
  assert.equal(sim.submit(request).receipt.status,'duplicate');assert.equal(sim.state().execution_count,1);
  assert.equal(sim.state().duplicate_count,1);
});

test('busy and overlapping requests do not submit a second execution',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now,dispatchDelay:500});sim.connect();
  const first=sim.makeRequest({kind:'play_expression',semantic_id:'happy'});assert.equal(sim.submit(first).receipt.status,'accepted');
  const second=sim.makeRequest({kind:'play_action',action_id:'look_around'});assert.equal(sim.submit(second).receipt.status,'busy');
  assert.equal(sim.state().execution_count,0);assert.equal(sim.state().pending.request_id,first.request_id);
  assert.equal(sim.submit(second).receipt.status,'duplicate');
});

test('client timeout does not cancel accepted work; expiry is explicit before dispatch',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now,dispatchDelay:500});sim.connect();sim.setMode('stall');
  const request=sim.makeRequest({kind:'play_action',action_id:'look_around'},{expiresAfterMs:700});
  assert.equal(sim.submit(request).receipt.status,'accepted');
  assert.equal(sim.clientTimeout(request.request_id).physical_result,'unknown');
  assert.equal(sim.state().pending.request_id,request.request_id);
  now=701;assert.equal(sim.advance()[0].status,'expired');assert.equal(sim.state().execution_count,0);
});

test('a standalone base action-completed fact has no fabricated request id',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now});sim.connect();
  const fact=sim.baseFact();assert.equal(fact.fact,'base.action.completed');assert.equal(fact.request_id,null);assert.equal(fact.correlated,false);
});

test('reconnect replays a bounded snapshot and restart invalidates old boot ids',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now,dispatchDelay:0});sim.connect();
  const old=sim.makeRequest({kind:'play_action',action_id:'look_around'});sim.submit(old);sim.advance();
  sim.disconnect();sim.baseFact();assert.equal(sim.state().receipts.at(-1).message_type,'receipt');
  sim.connect();assert.ok(sim.state().receipts.some(row=>row.request_id===old.request_id));
  sim.restart();assert.equal(sim.state().connected,false);sim.connect();const stale=sim.submit(old);assert.equal(stale.receipt.status,'rejected');assert.equal(stale.receipt.reason,'boot_changed');
});

test('request sequences are monotonic and decision storage has a fixed bound',()=>{
  let now=0;const sim=Core.create(catalog,{clock:()=>now,dispatchDelay:0});sim.connect();
  for(let i=0;i<Core.LIMITS.max_dedup;i++){
    const req=sim.makeRequest({kind:'play_expression',semantic_id:'happy'});sim.submit(req);now+=1;sim.advance();
  }
  assert.equal(sim.state().retained_decisions,Core.LIMITS.max_dedup);
  const extra=sim.makeRequest({kind:'play_expression',semantic_id:'happy'});assert.equal(sim.submit(extra).receipt.reason,'idempotency_window_full');
  assert.ok(sim.state().receipts.length<=Core.LIMITS.max_history);
});
