'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Scheduler = require('../scripts/grok-role-scheduler.js');
const states = ['idle', 'happy', 'sad', 'shy', 'confused', 'drowsy'];
const phases = {entry:11,loop:49,exit:11};
const definitions = Object.fromEntries(states.map(state=>[state,Object.fromEntries(
  Object.entries(phases).map(([phase,frames])=>[phase,{frames}]))]));
function advance(player,count){for(let i=0;i<count;i++)player.advance();return player.snapshot()}
function startLoop(player){advance(player,11);assert.equal(player.phase,'loop')}

test('entry and every frame hold advance at the declared boundaries',()=>{
  const s=new Scheduler(definitions);
  assert.deepEqual(s.snapshot(),{current:'idle',target:'idle',phase:'entry',frame:0,
    frames:11,paused:false,transitions:0});
  advance(s,10);assert.equal(s.frame,10);assert.equal(s.phase,'entry');
  advance(s,1);assert.equal(s.frame,0);assert.equal(s.phase,'loop');
  advance(s,49*3);assert.equal(s.frame,0);assert.equal(s.phase,'loop');assert.equal(s.current,'idle');
});
test('entry coalesces requests and exits at P without an unnecessary full loop',()=>{
  const s=new Scheduler(definitions);advance(s,3);
  s.request('happy');s.request('sad');s.request('shy');
  advance(s,8);assert.equal(s.phase,'exit');assert.equal(s.current,'idle');
  advance(s,11);assert.equal(s.current,'shy');assert.equal(s.phase,'entry');assert.equal(s.frame,0);
});
test('loop waits for its P endpoint; exit adopts the latest target at H',()=>{
  const s=new Scheduler(definitions);startLoop(s);advance(s,7);s.request('sad');
  advance(s,41);assert.equal(s.phase,'loop');assert.equal(s.frame,48);
  s.advance();assert.equal(s.phase,'exit');
  advance(s,5);s.request('happy');s.request('confused');
  advance(s,6);assert.equal(s.current,'confused');assert.equal(s.phase,'entry');
});
test('withdrawing a pending request in entry or loop preserves the current expression',()=>{
  for(const phase of ['entry','loop']){
    const s=new Scheduler(definitions);if(phase==='loop')startLoop(s);
    s.request('sad');s.request('idle');advance(s,phases[phase]);
    assert.equal(s.current,'idle');assert.equal(s.phase,'loop');
  }
});
test('once exit starts a return to the old state completes exit then entry',()=>{
  const s=new Scheduler(definitions);startLoop(s);s.request('happy');advance(s,49);
  s.request('idle');advance(s,11);
  assert.equal(s.phase,'entry');assert.equal(s.current,'idle');advance(s,11);assert.equal(s.phase,'loop');
});
test('same semantic in each phase is idempotent and never restarts a partial clip',()=>{
  for(const phase of ['entry','loop','exit']){
    const s=new Scheduler(definitions);
    if(phase!=='entry')startLoop(s);
    if(phase==='exit'){s.request('happy');advance(s,49)}
    advance(s,3);const before=s.snapshot();s.request('idle');const after=s.snapshot();
    assert.equal(after.frame,before.frame);assert.equal(after.phase,before.phase);
    assert.equal(after.current,before.current);
  }
});
test('pause stops frames and transitions while allowing a new target',()=>{
  const s=new Scheduler(definitions);advance(s,5);s.setPaused(true);
  const before=s.snapshot();s.request('shy');advance(s,1000);
  assert.equal(s.frame,before.frame);assert.equal(s.transitions,before.transitions);assert.equal(s.target,'shy');
  s.setPaused(false);advance(s,6);assert.equal(s.phase,'exit');
});
test('invalid states and malformed frame counts fail without changing playback',()=>{
  const s=new Scheduler(definitions), before=s.snapshot();
  for(const name of ['',null,'thinking','__proto__','constructor',1,['happy'],new String('happy'),
      {toString(){return 'happy'}}])
    assert.throws(()=>s.request(name),RangeError);
  assert.deepEqual(s.snapshot(),before);assert.throws(()=>s.setPaused(1),TypeError);
  assert.throws(()=>new Scheduler(definitions,'unknown'),TypeError);
  assert.throws(()=>new Scheduler(definitions,['idle']),TypeError);
  for(const value of [0,-1,193,NaN,true,2.5]){
    const broken=structuredClone(definitions);broken.idle.entry.frames=value;
    assert.throws(()=>new Scheduler(broken),TypeError);
  }
  const copied=new Scheduler(definitions);const original=definitions.idle.entry.frames;
  definitions.idle.entry.frames=1;assert.equal(copied.snapshot().frames,original);
  definitions.idle.entry.frames=original;assert(Object.isFrozen(copied.snapshot()));
});
test('all 30 directed switches progress through P -> H -> P with exact decoded seam hashes',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.resolve(__dirname,
    '../artifacts/grok-role-clips-20261111/manifest.json'),'utf8'));
  for(const from of states)for(const to of states){
    if(from===to)continue;
    const player=new Scheduler(definitions,from);startLoop(player);player.request(to);
    const observed=[];
    for(let i=0;i<49+11+11;i++){
      const before=player.snapshot(),after=player.advance();
      if(before.phase!==after.phase||before.current!==after.current){
        const a=manifest.expressions[before.current].clips[before.phase];
        const b=manifest.expressions[after.current].clips[after.phase];
        assert.equal(a.last_rgba_sha256,b.first_rgba_sha256,from+' -> '+to);
        observed.push(after.phase);
      }
    }
    assert.deepEqual(observed,['exit','entry','loop']);assert.equal(player.current,to);
  }
});
