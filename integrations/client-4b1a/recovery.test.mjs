import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createChatOrchestrator } from './src/chat-orchestrator.mjs';
import { createInputStore } from './src/input-store.mjs';
import { createStateEngine } from './src/state-engine.mjs';
import { createWorldContext } from './src/world-context.mjs';
import { createOutputRouter } from './src/output-router.mjs';
import { createSqlitePersistence } from './src/persistence.mjs';
import { createPersistentWorld } from './src/persistent-world.mjs';

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'deskbot-4b1a-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  let at = Date.parse('2026-11-16T01:00:00Z'), calls = 0;
  const now = () => new Date(at);
  const request = { event_id: 'recovery-turn', correlation_id: 'recovery-correlation',
    character_id: 'shaping-001', device_id: 'device-test', role_revision:'frozen-revision', message: '今天做饭很开心' };
  function open(checkpoint) {
    const persistence = createSqlitePersistence({ filename: join(dir, 'world.sqlite'), now });
    const outputRouter = createOutputRouter({ persistence, now });
    const inputStore = createInputStore({ persistence, now });
    const persistentWorld = createPersistentWorld({persistence,now});
    const orchestrator = createChatOrchestrator({persistence,now,outputRouter,inputStore,persistentWorld,
      stateEngine:createStateEngine({persistence,now}),worldContext:createWorldContext({persistence,now}),
      recoveryCheckpoint:checkpoint,
      llm:{async complete(){calls++;return {text:'我也很开心，想再试一道菜。',provider:'fixed',trace:{}};}}});
    return {orchestrator,persistence,outputRouter,inputStore,persistentWorld};
  }
  return {open,request,advance:()=>{at+=12000;},calls:()=>calls};
}

for (const phase of ['prepared', 'output_enqueued']) {
  test(`SQLite resumes ${phase} with one model reply and one outbox route`, async t => {
    const f=fixture(t), first=f.open(at=>{if(at===phase)throw new Error('injected_crash');});
    await assert.rejects(()=>first.orchestrator.run(f.request),/injected_crash/);
    const before=first.outputRouter.size();
    first.persistence.close(); f.advance();
    const second=f.open();
    try {
      const turn=await second.orchestrator.run(f.request);
      assert.equal(f.calls(),1);
      assert.equal(turn.reply,'我也很开心，想再试一道菜。');
      assert.equal(second.outputRouter.size(),5);
      assert.equal(second.inputStore.size(),2);
      assert.equal(turn.reply_event.role_revision,'frozen-revision');
      if(phase==='output_enqueued')assert.equal(before,5);
      f.advance();
      assert.equal((await second.orchestrator.run(f.request)).duplicate,true);
      assert.equal(second.outputRouter.size(),5);
      assert.equal(f.calls(),1);
    } finally {second.persistence.close();}
  });
}

test('advancing clock preserves input time but rejects changed message/binding',async t=>{
  const f=fixture(t), runtime=f.open();
  try {
    const turn=await runtime.orchestrator.run(f.request); f.advance();
    const retry=await runtime.orchestrator.run(f.request);
    assert.equal(retry.input_event.occurred_at,turn.input_event.occurred_at);
    for(const change of [{message:'另一句话'},{role_revision:'changed'},{device_id:'other'}]) {
      await assert.rejects(()=>runtime.orchestrator.run({...f.request,...change}),e=>e.code==='event_id_conflict');
    }
    assert.equal(f.calls(),1);
  } finally {runtime.persistence.close();}
});

test('prepared output plan stays frozen while another turn changes live state',async t=>{
  const f=fixture(t), first=f.open(at=>{if(at==='prepared')throw new Error('injected_crash');});
  await assert.rejects(()=>first.orchestrator.run(f.request));
  const prepared=first.persistence.get('chat.recovery',f.request.event_id).turn;
  assert.ok(Array.isArray(prepared.output_plan));
  first.persistence.close();f.advance();
  const second=f.open();
  try {
    await second.orchestrator.run({...f.request,event_id:'sad-new-turn',correlation_id:'sad-new-correlation',message:'我很难过很担心'});
    const old=await second.orchestrator.run(f.request);
    assert.deepEqual(old.output_plan,prepared.output_plan);
    assert.deepEqual(old.expression_intent,prepared.expression_intent);
    assert.equal(old.output_plan[0].expression,'happy');assert.equal(f.calls(),2);
  } finally {second.persistence.close();}
});

test('interrupted model stage stays explicit after restart and accepts a new input',async t=>{
  const f=fixture(t), first=f.open(at=>{if(at==='started')throw new Error('injected_crash');});
  await assert.rejects(()=>first.orchestrator.run(f.request)); first.persistence.close(); f.advance();
  const second=f.open();
  try {
    await assert.rejects(()=>second.orchestrator.run(f.request),e=>e.code==='turn_recovery_pending');
    assert.equal(f.calls(),0);
    await second.orchestrator.run({...f.request,event_id:'new-turn',correlation_id:'new-correlation'});
    assert.equal(f.calls(),1);
  } finally {second.persistence.close();}
});

test('failed final save cannot expose an unpersisted completed turn',async t=>{
  const f=fixture(t), first=f.open(), put=first.persistence.put;
  first.persistence.put=(namespace,...args)=>{if(namespace==='chat.turns')throw new Error('disk_fault');return put(namespace,...args);};
  await assert.rejects(()=>first.orchestrator.run(f.request),/disk_fault/);
  assert.equal(first.orchestrator.get(f.request.event_id),null);
  first.persistence.close(); f.advance();
  const second=f.open();
  try {await second.orchestrator.run(f.request);assert.equal(f.calls(),1);assert.equal(second.outputRouter.size(),5);}
  finally {second.persistence.close();}
});

test('outbox filters before pagination while speech and body commands keep their own consumer',()=>{
  const router=createOutputRouter();
  for(let i=0;i<250;i++)router.enqueue({source_event:{event_id:'speech-'+i,device_id:'device-test'},
    output_plan:[{type:'speak',targets:['vocat'],text:'语音'}]});
  for(let i=0;i<60;i++)router.enqueue({source_event:{event_id:'body-perception:'+i,device_id:'device-test'},
    output_plan:[{type:'render.expression',targets:['vocat'],expression:'neutral'}]});
  router.enqueue({source_event:{event_id:'reply-latest',device_id:'device-test'},
    output_plan:[{type:'render.expression',targets:['vocat'],expression:'happy'}]});
  const page=router.listQueued({target:'vocat',device_id:'device-test',type:'render.expression',source_event_prefix:'reply-'});
  assert.equal(page.length,1);assert.equal(page[0].source_event_id,'reply-latest');
  assert.equal(router.size(),311);
});
