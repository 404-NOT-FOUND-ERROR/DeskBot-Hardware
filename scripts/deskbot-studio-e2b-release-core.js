/* Package report validation and draft matching, independent of the browser. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.DeskBotStudioReleaseCore=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const HASH=/^[a-f0-9]{64}$/;
  const canonical=value=>JSON.stringify(normalize(value));
  function normalize(value){if(Array.isArray(value))return value.map(normalize);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,normalize(value[k])]));return value;}
  const equal=(a,b)=>canonical(a)===canonical(b);
  function inputFor(draft,data){const role=draft.roles.find(r=>r.id===draft.selected_role_id);if(!role)throw Error('选中的角色不存在');return {catalog_id:data.catalog_id,target_id:data.target.id,role};}
  function matches(report,draft,data){return !!report&&equal(report.input,inputFor(draft,data));}
  function validate(report,data){
    const fail=message=>{throw Error(message||'资源包报告与当前目录不匹配');};
    if(!report||report.schema!=='deskbot.studio_package/v1'||report.edition!=='E2b'||report.result!=='offline_package_verified'||report.catalog_id!==data.catalog_id||!equal(report.target,data.target))fail();
    const input=report.input;if(!input||!equal(Object.keys(input).sort(),['catalog_id','role','target_id'])||input.catalog_id!==data.catalog_id||input.target_id!==data.target.id)fail();
    const role=input.role;
    if(!role||!/^[a-z][a-z0-9-]{0,63}$/.test(role.id)||!equal(Object.keys(role).sort(),['bindings','example','id','name','voice'])||typeof role.name!=='string'||!role.name.trim()||Array.from(role.name).length>80||typeof role.voice!=='string'||!role.voice.trim()||Array.from(role.voice).length>96||typeof role.example!=='boolean')fail('报告中的角色资料无效');
    if(!role.bindings||!equal(Object.keys(role.bindings).sort(),Object.keys(data.semantics).sort()))fail('报告中的表情清单不完整');
    for(const [sid,name] of Object.entries(role.bindings))if(typeof name!=='string'||!data.semantics[sid].allowed_resources.includes(name))fail('报告中存在布局不兼容的动画');
    if(role.bindings.listen!=='listen.eaf')fail('报告中的聆听状态发生变化');
    const p=report.package,i=report.image,b=report.budget,size=data.target.assets.bytes,limit=data.target.assets.soft_limit_bytes;
    if(!p||!i||!b||!Number.isSafeInteger(p.bytes)||p.bytes<=12||p.bytes>limit||!HASH.test(p.sha256)||!HASH.test(i.sha256)||!HASH.test(report.input_sha256)||!HASH.test(report.package_id)||i.bytes!==size||b.partition_bytes!==size||b.soft_limit_bytes!==limit||b.package_bytes!==p.bytes||b.free_bytes!==size-p.bytes||b.soft_limit_free_bytes!==limit-p.bytes)fail('资源包容量或摘要字段不完整');
    if(!report.baseline||report.baseline.package_sha256!==data.release.rollback_reference.package_sha256||report.baseline.image_sha256!==data.release.rollback_reference.partition_sha256)fail('报告基线与 E1.18 不一致');
    const expected=Object.entries(data.semantics).filter(([sid,s])=>role.bindings[sid]!==s.resource_id);
    if(!Array.isArray(report.changes)||report.changes.length!==expected.length)fail('报告中的接线差异不完整');
    const seen=new Set();for(const change of report.changes){const s=data.semantics[change.semantic],a=data.assets[role.bindings[change.semantic]];if(!s||seen.has(change.semantic)||change.semantic==='listen'||change.before?.name!==change.semantic||change.before?.file!==s.resource_id||change.after?.name!==change.semantic||change.after?.file!==role.bindings[change.semantic]||!equal(change.before.eaf,{loop:s.loop,fps:s.fps})||!equal(change.after.eaf,{loop:a.origin==='candidate'?a.loop:s.loop,fps:a.origin==='candidate'?a.fps:s.fps}))fail('报告中的动画接线或播放时序不匹配');seen.add(change.semantic);}
    if(expected.some(([sid])=>!seen.has(sid)))fail();
    const added=[...new Set(Object.values(role.bindings).filter(name=>data.assets[name].origin==='candidate'))].sort();
    const members=report.members;
    if(!members||!equal(members.added,added)||!equal(members.removed,[])||!equal(members.changed,expected.length?['index.json']:[])||members.all_existing_non_index_byte_exact!==true||!Array.isArray(members.preserved)||members.preserved.length!==28||new Set(members.preserved).size!==28||p.member_count!==29+added.length)fail('已有资源的保留记录不完整');
    const checks=['package_round_trip','layout_unchanged','listen_unchanged','icons_fonts_models_unchanged','action_branch_unchanged','within_release_budget','asset_hashes_and_geometry_match'];
    if(!report.checks||checks.some(k=>report.checks[k]!==true)||report.hardware_accessed!==false||report.device_written!==false)fail('资源包检查记录不完整');
    return report;
  }
  function downloads(value,report){const result={};if(!value||typeof value!=='object')throw Error('下载清单缺失');for(const name of ['report.json','expression-package.bin','expression-assets-8m.bin']){const path='/packages/'+report.package_id+'/'+name;if(value[name]!==path)throw Error('下载路径与当前资源包不一致');result[name]=path;}return result;}
  return {canonical,inputFor,matches,validate,downloads};
});
