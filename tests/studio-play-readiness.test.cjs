'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const readiness=require('../scripts/deskbot-studio-play-readiness.js');
const ready=()=>({enabled:true,connection_id:'conn-1',target_id:'device-a',boot_id:'boot-a',session_id:'session-a',
  can_submit:true,age_ms:10,display:{expression_semantics:['neutral','eat'],application:{phase:'settled',value:'idle'},
    page:{phase:'settled',value:'home'},player:{state:'initialized'}},
  catalog:{status:'complete',target_id:'device-a',boot_id:'boot-a',session_id:'session-a',manifest:{catalog_id:'catalog-a',entries:[{kind:'expression',id:'eat',resource_id:'eat.eaf'}]}}});
function harness(read){
  let time=0;const writes=[];
  return {writes,options:{operation:'eat',initial:ready(),now:()=>time,
    wait:async ms=>{time+=ms;},read:async budget=>{assert.ok(budget>0);return read(()=>time,ms=>time+=ms);},
    send:async state=>{writes.push(state);return 'sent';}}};
}
test('waits through a slow query, then sends exactly once',async()=>{
  const h=harness((now,advance)=>{advance(40);return now()<3400?{...ready(),can_submit:false,age_ms:3500,display:null}:ready();});
  assert.equal(await readiness.play(h.options),'sent');assert.equal(h.writes.length,1);
});
test('stale facts alone do not authorize a send',async()=>{
  const h=harness(()=>({...ready(),can_submit:false,display:null,age_ms:2500}));
  await assert.rejects(readiness.play({...h.options,timeout:600}),/尚未就绪/);assert.equal(h.writes.length,0);
});
test('age plus HTTP round trip must be below 350 ms',async()=>{
  for(const [age,rtt] of [[350,0],[340,10],[0,350],[20,400]]){
    const h=harness((now,advance)=>{advance(rtt);return {...ready(),age_ms:age};});
    await assert.rejects(readiness.play({...h.options,timeout:800}),/尚未就绪/);assert.equal(h.writes.length,0);
  }
});
test('never starts playback after the total deadline',async()=>{
  const h=harness((now,advance)=>{advance(1000);return ready();});
  await assert.rejects(readiness.play({...h.options,timeout:1000}),/尚未就绪/);assert.equal(h.writes.length,0);
});
for(const key of ['connection_id','target_id','boot_id','session_id'])test(key+' change cancels this click',async()=>{
  const h=harness(()=>({...ready(),[key]:'changed'}));
  await assert.rejects(readiness.play(h.options),/会话已变化/);assert.equal(h.writes.length,0);
});
test('stopped session cancels this click',async()=>{
  const h=harness(()=>({...ready(),enabled:false}));await assert.rejects(readiness.play(h.options),/会话已变化/);assert.equal(h.writes.length,0);
});
test('explicit cancel and page exit cancel before and after GET',async()=>{
  for(const duringRead of [false,true]){
    let active=!duringRead?false:true;
    const h=harness(()=>{active=false;return ready();});
    await assert.rejects(readiness.play({...h.options,isCurrent:()=>active}),/已取消/);assert.equal(h.writes.length,0);
  }
});
for(const change of ['speaking','menu','player'])test(change+' cancels instead of waiting for later idle',async()=>{
  const h=harness(()=>{const s=ready();if(change==='speaking')s.display.application.value='speaking';
    if(change==='menu')s.display.page.value='other';if(change==='player')s.display.player.state='stopping';return s;});
  await assert.rejects(readiness.play(h.options),/主页待机/);assert.equal(h.writes.length,0);
});
test('capability must still support the requested expression',async()=>{
  const h=harness(()=>{const s=ready();s.display.expression_semantics=['neutral'];return s;});
  await assert.rejects(readiness.play(h.options),/尚未接入/);assert.equal(h.writes.length,0);
});
test('missing or different directory stops playback',async()=>{
  for(const change of ['absent','session','binding','catalog']){
    const h=harness(()=>{const s=ready();if(change==='absent')s.catalog.status='reading';
      if(change==='session')s.catalog.session_id='other';if(change==='binding')s.catalog.manifest.entries[0].resource_id='other.eaf';
      if(change==='catalog')s.catalog.manifest.catalog_id='other';return s;});
    await assert.rejects(readiness.play(h.options),/目录已变化/);assert.equal(h.writes.length,0);
  }
});
test('initial complete directory is required for eat',async()=>{
  const h=harness(()=>ready()),initial=ready();initial.catalog.status='reading';
  await assert.rejects(readiness.play({...h.options,initial}),/先读取/);assert.equal(h.writes.length,0);
});
test('legacy neutral may retain its direct capability without a directory',async()=>{
  const initial=ready();initial.catalog={status:'idle'};delete initial.display.expression_semantics;
  const h=harness(()=>initial);assert.equal(await readiness.play({...h.options,operation:'neutral',initial}),'sent');assert.equal(h.writes.length,1);
});
test('POST rejection and transport timeout never cause a second POST',async()=>{
  for(const message of ['request_not_ready','HTTP timeout']){
    const h=harness(()=>ready());let count=0;
    await assert.rejects(readiness.play({...h.options,send:async()=>{count++;throw Error(message);}}),new RegExp(message));
    assert.equal(count,1);
  }
});
test('malformed negative age does not permit sending',async()=>{
  const h=harness(()=>({...ready(),age_ms:-1}));
  await assert.rejects(readiness.play({...h.options,timeout:300}),/尚未就绪/);assert.equal(h.writes.length,0);
});
