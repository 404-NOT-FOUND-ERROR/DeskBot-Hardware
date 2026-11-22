/* A resource preference, shared by its references within one authoring role. */
(function(root){
  'use strict';
  const SCHEMA='deskbot.studio_draft/v2',own=(v,k)=>Object.hasOwn(v||{},k),clone=v=>JSON.parse(JSON.stringify(v));
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function migrate(value){
    if(value?.schema!=='deskbot.studio_draft/v1')return value;
    const next=clone(value);next.schema=SCHEMA;
    for(const role of next.roles||[]){if(own(role,'mirror_overrides'))throw Error('旧版草稿包含新版字段');role.mirror_overrides={};}
    return next;
  }
  function validate(overrides,data){
    if(!overrides||typeof overrides!=='object'||Array.isArray(overrides))throw Error('镜像设置格式无效');
    for(const [id,value] of Object.entries(overrides))if(!own(data.assets,id)||data.assets[id].object_kind==='listen'||typeof value!=='boolean')throw Error('镜像设置应对应已知动画与布尔值');
  }
  function resolve(data,id,semantic,overrides={}){
    const automatic=data.asset_layouts?.[id]||data.semantics?.[semantic]?.layout_id||
      (data.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');
    const manual=own(overrides,id),mirror=manual?overrides[id]:automatic==='eye_anim';
    const layout=manual?(mirror?'eye_anim':'full_frame'):automatic;
    return {id:layout,mirror,manual,automatic:automatic==='eye_anim',
      title:layout==='eye_anim'?'双眼镜像':layout==='listen_anim'?'聆听布局':'完整画面'};
  }
  function controls(data,id,semantic,role){
    const info=resolve(data,id,semantic,role.mirror_overrides),system=data.assets[id]?.object_kind==='listen',
      count=Object.values(role.bindings).filter(name=>name===id).length;
    return `<div class="resource-mirror-option"><div><label><input type="checkbox" data-resource-mirror="${esc(id)}" ${info.mirror?'checked':''} ${!id||system?'disabled':''}> 镜像</label><span>${info.manual?'手动设置':'自动默认'}</span></div>
      <p>${system?'系统聆听布局':info.mirror?'左右双眼 · 右侧镜像':'居中显示一次'}</p>
      <div class="resource-mirror-foot"><small>${count>1?'此角色 '+count+' 处引用同步采用':'此动画的显示设置'}</small><button type="button" data-mirror-auto="${esc(id)}" ${info.manual?'':'disabled'}>恢复自动</button></div></div>`;
  }
  function differences(role,data){return Object.entries(role.mirror_overrides||{}).filter(([id])=>Object.values(role.bindings).includes(id))
    .map(([resource,after])=>({kind:'mirror',resource,semantic:Object.keys(role.bindings).find(k=>role.bindings[k]===resource),before:null,after}));}
  function manifest(role,data){
    const ids=[...new Set(Object.values(role.bindings).filter(Boolean))].sort();
    return {schema:'deskbot.asset_layouts/v1',role_id:role.id,assets:ids.map(id=>{
      const semantic=Object.keys(role.bindings).find(s=>role.bindings[s]===id),a=data.assets[id],p=resolve(data,id,semantic,role.mirror_overrides);
      return {resource_id:id,resource_sha256:a.deployed_sha256,width:a.frame_width,height:a.frame_height,
        layout_id:p.id,mirror:p.mirror,source:p.manual?'manual':'automatic'};
    })};
  }
  function installRelease(base){return Object.freeze({...base,validate(report,data){
    if(!own(report?.input?.role,'mirror_overrides'))return base.validate(report,data);
    validate(report.input.role.mirror_overrides,data);
    const expected=manifest(report.input.role,data);
    if(base.canonical(expected)!==base.canonical(report.layout_manifest?.value)||report.layout_manifest?.file!=='deskbot-layout.json'||
      report.layout_manifest?.required_capability!=='asset_layout_manifest_v1'||!/^[a-f0-9]{64}$/.test(report.layout_manifest?.sha256)||
      base.canonical(report.required_capabilities)!==base.canonical(['asset_layout_manifest_v1'])||
      report.next_step!=='install_layout_manifest_player_then_verify'||report.checks?.layout_manifest_round_trip!==true||
      report.device_compatible!==false||!Array.isArray(report.members?.added)||
      report.members.added.filter(name=>name==='deskbot-layout.json').length!==1)throw Error('镜像清单或播放器版本要求不匹配');
    // Reuse every legacy byte, budget and binding check, then validate the new metadata above.
    const legacy=clone(report);delete legacy.input.role.mirror_overrides;
    legacy.members.added=legacy.members.added.filter(name=>name!=='deskbot-layout.json');legacy.package.member_count--;
    base.validate(legacy,data);return report;
  }});}
  const api=Object.freeze({SCHEMA,migrate,validate,resolve,controls,differences,manifest,installRelease});
  if(typeof module==='object'&&module.exports)module.exports=api;else root.DeskBotMirrorOptions=api;
})(globalThis);
