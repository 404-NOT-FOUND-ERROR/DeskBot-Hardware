'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const Core=require('../scripts/deskbot-studio-e2a-core.js');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2a-20261104/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const copy=value=>JSON.parse(JSON.stringify(value));

test('catalog keeps accepted mapping and separates candidate',()=>{
  assert.equal(Object.keys(data.semantics).length,29);assert.equal(Object.keys(data.assets).length,19);
  assert.equal(data.semantics.embarrassed.resource_id,'Happy.eaf');
  assert.equal(data.semantics.surprised.resource_id,'shocked.eaf');
  assert.equal(data.assets['shy_candidate.eaf'].atlas,null);assert.equal(data.device.live,false);
});
test('initial state round trips through strict validation',()=>{
  const editor=Core.create(data);assert.deepEqual(Core.parse(JSON.stringify(editor.exportDocument()),data),data.initial);
});
test('one semantic rewire preserves action and other Happy references',()=>{
  const editor=Core.create(data),before=editor.snapshot();editor.connect('embarrassed','shy_candidate.eaf');
  assert.equal(editor.changes().length,1);assert.equal(data.semantics.embarrassed.action_id,'cat_nuzzle');
  for(const [id,resource] of Object.entries(before.roles[0].bindings))if(id!=='embarrassed')assert.equal(editor.active().bindings[id],resource);
});
test('listen layout rejects eye animation and vice versa',()=>{
  const editor=Core.create(data),before=editor.snapshot();
  assert.throws(()=>editor.connect('listen','shy_candidate.eaf'),/布局/);
  assert.throws(()=>editor.connect('happy','listen.eaf'),/布局/);assert.deepEqual(editor.snapshot(),before);
});
test('candidate exact package budget matches E1.45 measured package',()=>{
  const editor=Core.create(data);editor.connect('embarrassed','shy_candidate.eaf');
  assert.deepEqual(editor.budget(),{bytes:5981260,exact:true,free:2407348,soft_free:1358772,changes:1,disconnected:0,state:'verified_candidate'});
});
test('arbitrary changes are estimates and preserve existing resource package',()=>{
  const editor=Core.create(data);editor.connect('happy','Sad.eaf');const b=editor.budget();
  assert.equal(b.exact,false);assert.equal(b.state,'review_required');assert.ok(b.bytes>=5538351);
});
test('multiple candidate references add the EAF once',()=>{
  const editor=Core.create(data);editor.connect('happy','shy_candidate.eaf');const first=editor.budget().bytes;
  editor.connect('embarrassed','shy_candidate.eaf');assert.equal(editor.budget().bytes,first);assert.equal(editor.budget().exact,false);
});
test('disconnected semantic is retained and flagged incomplete',()=>{
  const editor=Core.create(data);editor.connect('happy',null);assert.equal(editor.budget().state,'incomplete');
  assert.equal(editor.exportDocument().roles[0].bindings.happy,null);editor.restore('happy');assert.equal(editor.budget().state,'baseline');
});
test('undo redo and role cloning preserve isolated bindings',()=>{
  const editor=Core.create(data);editor.connect('embarrassed','shy_candidate.eaf');editor.undo();assert.equal(editor.active().bindings.embarrassed,'Happy.eaf');
  editor.redo();assert.equal(editor.active().bindings.embarrassed,'shy_candidate.eaf');
  const id=editor.cloneRole();editor.restore('embarrassed');assert.equal(editor.active().bindings.embarrassed,'Happy.eaf');
  editor.selectRole('role-default');assert.equal(editor.active().bindings.embarrassed,'shy_candidate.eaf');assert.notEqual(id,'role-default');
});
test('invalid import leaves state and history untouched',()=>{
  const editor=Core.create(data),before=editor.snapshot(),wrong=copy(before);wrong.catalog_id='0'.repeat(64);
  assert.throws(()=>editor.importText(JSON.stringify(wrong)),/匹配/);assert.deepEqual(editor.snapshot(),before);assert.equal(editor.canUndo(),false);
});
test('oversized file rejected before parsing',()=>{assert.throws(()=>Core.parse(' '.repeat(Core.MAX_BYTES+1),data),/512/);});
test('unknown fields, unknown assets and duplicate roles rejected',()=>{
  for(const mutate of [d=>d.extra=true,d=>d.roles[0].bindings.happy='bad.eaf',d=>d.roles.push(copy(d.roles[0]))]){
    const draft=copy(data.initial);mutate(draft);assert.throws(()=>Core.validate(draft,data));
  }
});
test('role name limits and role count checked before commit',()=>{
  const editor=Core.create(data),before=editor.snapshot();assert.throws(()=>editor.rename(' '));assert.deepEqual(editor.snapshot(),before);
  assert.throws(()=>editor.rename('a'.repeat(81)));assert.deepEqual(editor.snapshot(),before);
  for(let i=0;i<13;i++)editor.cloneRole();assert.equal(editor.snapshot().roles.length,16);assert.throws(()=>editor.cloneRole(),/16/);
});
test('exported objects cannot mutate editor state',()=>{
  const editor=Core.create(data),doc=editor.exportDocument();doc.roles[0].bindings.happy=null;assert.equal(editor.active().bindings.happy,'Happy.eaf');
});
test('same binding produces no extra revision or undo entry',()=>{
  const editor=Core.create(data);assert.equal(editor.connect('happy','Happy.eaf'),false);assert.equal(editor.snapshot().revision,1);assert.equal(editor.canUndo(),false);
});
test('terminal frame holds for one tick before looping',()=>{
  const asset=data.assets['shy_candidate.eaf'];assert.equal(Core.frameAt(asset,187/25),187);assert.equal(Core.frameAt(asset,188/25),187);
  assert.equal(Core.frameAt(asset,189/25),0);assert.equal(Core.frameAt(asset,50,false),187);assert.equal(Core.frameAt(asset,-1),0);
});
test('verified-candidate status applies only to exact single-semantic mapping',()=>{
  const editor=Core.create(data);editor.connect('embarrassed','shy_candidate.eaf');editor.connect('happy','Sad.eaf');
  assert.equal(editor.budget().state,'review_required');assert.equal(editor.budget().exact,false);
});
