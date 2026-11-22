'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const Options=require('../scripts/deskbot-mirror-options.js'),LegacyRelease=require('../scripts/deskbot-studio-e2b-release-core.js');
const root=path.resolve(__dirname,'..'),copy=x=>JSON.parse(JSON.stringify(x));
const html=fs.readFileSync(path.join(root,'tmp/deskbot-studio-e3f8-20261111/index.html'),'utf8');
const data=JSON.parse(html.match(/<script id="studio-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const coreScript=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.startsWith('/* Versioned offline editor state'));
assert.ok(coreScript,'tests execute the actual generated editor core');
const sandbox={DeskBotMirrorOptions:Options,TextEncoder};vm.runInNewContext(coreScript,sandbox);
const Core=sandbox.DeskBotStudioCore,Release=Options.installRelease(LegacyRelease);

test('registered defaults: full-face eat off, single-eye and shy on, listen separate',()=>{
  assert.equal(Options.resolve(data,'eat.eaf','eat').mirror,false);
  assert.equal(Options.resolve(data,'Happy.eaf','happy').mirror,true);
  assert.equal(Options.resolve(data,'shy_candidate.eaf','embarrassed').mirror,true);
  assert.equal(Options.resolve(data,'listen.eaf','listen').id,'listen_anim');
});
test('v1 migration clones the draft and retains every binding',()=>{
  const before=copy(data.initial),after=Core.validate(data.initial,data);
  assert.equal(after.schema,Options.SCHEMA);assert.deepEqual(copy(after.roles[0].bindings),before.roles[0].bindings);
  assert.deepEqual(data.initial,before);assert.ok(after.roles.every(r=>Object.keys(r.mirror_overrides).length===0));
});
test('v2 overrides reject unknown assets, listen, non-booleans and unknown fields',()=>{
  for(const overrides of [null,[],{'missing.eaf':false},{'listen.eaf':false},{'eat.eaf':0},{'eat.eaf':'false'}]){
    const draft=Options.migrate(data.initial);draft.roles[0].mirror_overrides=overrides;assert.throws(()=>Core.validate(draft,data));
  }
  const draft=Options.migrate(data.initial);draft.roles[0].extra=true;assert.throws(()=>Core.validate(draft,data));
  const v1=copy(data.initial);v1.roles[0].mirror_overrides={};assert.throws(()=>Core.validate(v1,data));
});
test('manual setting overrides both directions; restoring auto removes the override',()=>{
  const editor=Core.create(data);editor.setMirror('eat.eaf',true);
  assert.equal(Options.resolve(data,'eat.eaf','eat',editor.active().mirror_overrides).id,'eye_anim');
  editor.setMirror('Happy.eaf',false);
  assert.equal(Options.resolve(data,'Happy.eaf','happy',editor.active().mirror_overrides).id,'full_frame');
  editor.setMirror('eat.eaf',null);assert.equal(Object.hasOwn(editor.active().mirror_overrides,'eat.eaf'),false);
  assert.equal(Options.resolve(data,'eat.eaf','eat',editor.active().mirror_overrides).manual,false);
});
test('setting is shared across references, stays with its resource when rewired',()=>{
  const editor=Core.create(data);editor.connect('happy','eat.eaf');editor.setMirror('eat.eaf',true);
  for(const semantic of ['eat','happy'])assert.equal(Options.resolve(data,editor.active().bindings[semantic],semantic,editor.active().mirror_overrides).mirror,true);
  editor.connect('eat','winking.eaf');assert.equal(Options.resolve(data,'winking.eaf','eat',editor.active().mirror_overrides).manual,false);
  editor.connect('eat','eat.eaf');assert.equal(Options.resolve(data,'eat.eaf','eat',editor.active().mirror_overrides).manual,true);
});
test('undo redo and no-op edits preserve mirror history',()=>{
  const editor=Core.create(data);editor.setMirror('Happy.eaf',false);const rev=editor.snapshot().revision;
  assert.equal(editor.setMirror('Happy.eaf',false),false);assert.equal(editor.snapshot().revision,rev);
  editor.undo();assert.equal(Options.resolve(data,'Happy.eaf','happy',editor.active().mirror_overrides).mirror,true);
  editor.redo();assert.equal(editor.active().mirror_overrides['Happy.eaf'],false);
});
test('clone, export and import retain choices while roles remain independent',()=>{
  const editor=Core.create(data),id=editor.active().id;editor.setMirror('eat.eaf',true);editor.cloneRole();
  assert.equal(editor.active().mirror_overrides['eat.eaf'],true);editor.setMirror('eat.eaf',false);
  const restored=Core.create(data);restored.importText(JSON.stringify(editor.exportDocument()));
  assert.equal(restored.active().mirror_overrides['eat.eaf'],false);restored.selectRole(id);
  assert.equal(restored.active().mirror_overrides['eat.eaf'],true);
});
test('invalid manual edits do not change the draft',()=>{
  const editor=Core.create(data),before=editor.snapshot();
  for(const [id,value] of [['listen.eaf',true],['missing',false],['eat.eaf',1]])assert.throws(()=>editor.setMirror(id,value));
  assert.deepEqual(copy(editor.snapshot()),copy(before));
});
test('manifest includes only used resources, deterministically binds content and geometry',()=>{
  const role=Options.migrate(data.initial).roles[0];role.mirror_overrides={'eat.eaf':true,'shy_candidate.eaf':false};
  const manifest=Options.manifest(role,data),eat=manifest.assets.find(r=>r.resource_id==='eat.eaf');
  assert.equal(eat.mirror,true);assert.equal(eat.source,'manual');assert.equal(eat.resource_sha256,data.assets['eat.eaf'].deployed_sha256);
  assert.equal(eat.width,data.assets['eat.eaf'].frame_width);assert.ok(!manifest.assets.some(r=>r.resource_id==='shy_candidate.eaf'));
  assert.deepEqual(manifest.assets.map(r=>r.resource_id),manifest.assets.map(r=>r.resource_id).sort());
});
test('setting changes make package reports stale; inactive roles do not',()=>{
  const editor=Core.create(data);const report={input:copy(Release.inputFor(editor.snapshot(),data))};
  assert.equal(Release.matches(report,editor.snapshot(),data),true);editor.setMirror('eat.eaf',true);
  assert.equal(Release.matches(report,editor.snapshot(),data),false);editor.undo();assert.equal(Release.matches(report,editor.snapshot(),data),true);
  const draft=editor.snapshot();draft.roles[1].mirror_overrides['eat.eaf']=true;assert.equal(Release.matches(report,draft,data),true);
});
function reportV2(){
  const r=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2b-20261104/default-package-report.json')));
  r.input.role.mirror_overrides={};r.layout_manifest={file:'deskbot-layout.json',value:Options.manifest(r.input.role,data),sha256:'a'.repeat(64),required_capability:'asset_layout_manifest_v1'};
  r.members.added=['deskbot-layout.json'];r.package.member_count++;
  r.required_capabilities=['asset_layout_manifest_v1'];r.device_compatible=false;r.next_step='install_layout_manifest_player_then_verify';r.checks.layout_manifest_round_trip=true;return r;
}
test('v2 report validates capability, exact metadata member, source and geometry',()=>{
  assert.equal(Release.validate(reportV2(),data).device_compatible,false);
  for(const mutate of [r=>r.required_capabilities=[],r=>r.members.added=[],r=>r.members.added.push('deskbot-layout.json'),
    r=>r.layout_manifest.sha256='bad',r=>r.next_step='ready_to_flash',r=>r.device_compatible=true,
    r=>r.layout_manifest.value.assets[0].mirror=false,r=>r.layout_manifest.value.assets[0].width++,r=>r.layout_manifest.value.assets[0].source='manual']){
    const report=reportV2();mutate(report);assert.throws(()=>Release.validate(report,data));
  }
});
test('legacy reports continue to validate unchanged; mirror metadata contributes budget',()=>{
  const r=JSON.parse(fs.readFileSync(path.join(root,'artifacts/deskbot-studio-e2b-20261104/default-package-report.json')));
  assert.equal(Release.validate(r,data),r);const editor=Core.create(data),budget=editor.budget();
  assert.equal(budget.exact,false);assert.ok(budget.bytes>data.release.rollback_reference.package_bytes);
});
