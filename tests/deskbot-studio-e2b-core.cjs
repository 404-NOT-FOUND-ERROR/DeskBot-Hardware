'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const Core=require('../scripts/deskbot-studio-e2b-release-core.js');
const root=path.resolve(__dirname,'..'),copy=value=>JSON.parse(JSON.stringify(value));
const html=fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2b-20261104/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const fixture=name=>JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2b-20261104',name+'-package-report.json')));

test('default and E1.45 candidate reports validate against current catalog',()=>{
  for(const name of ['default','candidate'])assert.equal(Core.validate(fixture(name),data).result,'offline_package_verified');
});
test('default report matches the selected initial role',()=>{
  assert.equal(Core.matches(fixture('default'),data.initial,data),true);
  assert.equal(Core.matches(fixture('candidate'),data.initial,data),false);
});
test('editing selected wiring immediately makes an old report stale',()=>{
  const draft=copy(data.initial);draft.roles[0].bindings.embarrassed='shy_candidate.eaf';
  assert.equal(Core.matches(fixture('default'),draft,data),false);
  assert.equal(Core.matches(fixture('candidate'),draft,data),true);
});
test('history and unrelated roles do not invalidate the selected package',()=>{
  const draft=copy(data.initial);draft.revision++;draft.roles[1].name='其他角色';
  assert.equal(Core.matches(fixture('default'),draft,data),true);
});
test('renaming and selecting another role invalidates the package association',()=>{
  const draft=copy(data.initial);draft.roles[0].name='新名字';
  assert.equal(Core.matches(fixture('default'),draft,data),false);
  draft.selected_role_id=draft.roles[1].id;assert.equal(Core.matches(fixture('default'),draft,data),false);
});
test('missing report never matches',()=>assert.equal(Core.matches(null,data.initial,data),false));
test('wrong target, budget, protected members, layout and changes rejected',()=>{
  for(const mutate of [r=>r.target.assets.offset=0,r=>r.budget.free_bytes++,r=>r.members.removed=['Happy.eaf'],
    r=>r.checks.listen_unchanged=false,r=>r.input.role.bindings.listen='Happy.eaf',
    r=>r.changes=[],r=>r.changes[0].after.eaf.fps=20,r=>r.hardware_accessed=true]){
    const report=fixture('candidate');mutate(report);assert.throws(()=>Core.validate(report,data));
  }
});
test('download links are bound to exact package id and filenames',()=>{
  const report=fixture('default'),links={};for(const name of ['report.json','expression-package.bin','expression-assets-8m.bin'])links[name]='/packages/'+report.package_id+'/'+name;
  assert.deepEqual(Core.downloads(links,report),links);
  for(const invalid of ['https://example.org/package.bin','/packages/'+'0'.repeat(64)+'/expression-assets-8m.bin','/README.md']){
    assert.throws(()=>Core.downloads({...links,'expression-assets-8m.bin':invalid},report));
  }
});
