/* Versioned offline editor state; kept independent of DOM, storage and device I/O. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DeskBotStudioCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'deskbot.studio_draft/v1', MAX_BYTES = 512 * 1024;
  const own = (v,k) => Object.prototype.hasOwnProperty.call(v,k);
  const clone = v => JSON.parse(JSON.stringify(v));
  const record = v => v && typeof v === 'object' && !Array.isArray(v);
  const keys = (v, expected) => record(v) && Object.keys(v).length === expected.length && expected.every(k=>own(v,k));
  const text = (v,max) => typeof v === 'string' && v.trim().length > 0 && Array.from(v).length <= max;
  function validate(value, data) {
    if (!keys(value,['schema','catalog_id','scope','revision','selected_role_id','roles']) || value.schema !== SCHEMA ||
        value.scope !== 'offline_draft' || value.catalog_id !== data.catalog_id) throw Error('草稿版本或素材目录不匹配');
    if (!Number.isSafeInteger(value.revision) || value.revision < 1) throw Error('草稿修订号无效');
    if (!Array.isArray(value.roles) || value.roles.length < 1 || value.roles.length > 16) throw Error('角色数量应为 1–16');
    const ids = new Set();
    for (const role of value.roles) {
      if (!keys(role,['id','name','voice','example','bindings']) || !/^[a-z][a-z0-9-]{0,63}$/.test(role.id) ||
          !text(role.name,80) || !text(role.voice,96) || typeof role.example !== 'boolean' || ids.has(role.id)) throw Error('角色名称、编号或声音字段无效');
      ids.add(role.id);
      if (!keys(role.bindings,Object.keys(data.semantics))) throw Error('表情清单不完整');
      for (const [semantic,resource] of Object.entries(role.bindings)) {
        if (resource !== null && (typeof resource !== 'string' || !data.semantics[semantic].allowed_resources.includes(resource))) throw Error('动画与当前圆屏布局不兼容');
      }
    }
    if (!ids.has(value.selected_role_id)) throw Error('选中的角色不存在');
    return value;
  }
  function parse(raw,data) {
    if (typeof raw !== 'string' || new TextEncoder().encode(raw).byteLength > MAX_BYTES) throw Error('草稿文件应小于 512 KiB');
    return validate(JSON.parse(raw.replace(/^\uFEFF/,'')),data);
  }
  function differences(role,data) {
    return Object.entries(role.bindings).filter(([id,resource])=>resource !== data.semantics[id].resource_id)
      .map(([semantic,resource])=>({semantic,before:data.semantics[semantic].resource_id,after:resource}));
  }
  function budget(role,data) {
    const changes=differences(role,data), release=data.release;
    const extras=[...new Set(Object.values(role.bindings).filter(id=>id && data.assets[id].origin==='candidate'))];
    const exactCandidate=changes.length===1 && changes[0].semantic==='embarrassed' && changes[0].after==='shy_candidate.eaf';
    const exact=changes.length===0 || exactCandidate;
    const bytes=exactCandidate ? release.candidate.package_bytes : release.rollback_reference.package_bytes +
      extras.reduce((n,id)=>n+data.assets[id].bytes,0)+(changes.length?4096:0);
    return {bytes,exact,free:release.budget.partition_bytes-bytes,soft_free:release.budget.soft_limit_bytes-bytes,
      changes:changes.length,disconnected:Object.values(role.bindings).filter(v=>v===null).length,
      state:Object.values(role.bindings).some(v=>v===null)?'incomplete':bytes>release.budget.soft_limit_bytes?'over_budget':exactCandidate?'verified_candidate':changes.length?'review_required':'baseline'};
  }
  function frameAt(asset,seconds,loop=asset.loop) {
    const tick=Math.max(0,Math.floor(seconds*asset.fps));
    const ticks=asset.frame_count+(asset.tail_hold_ticks||0);
    return Math.min(asset.frame_count-1,loop?tick%ticks:tick);
  }
  function create(data,initial=data.initial) {
    let value=clone(validate(initial,data));
    const undo=[],redo=[];
    const active=()=>value.roles.find(r=>r.id===value.selected_role_id);
    function commit(fn) {
      const next=clone(value);fn(next);
      if(JSON.stringify(next)===JSON.stringify(value))return false;
      if(value.revision>=Number.MAX_SAFE_INTEGER)throw Error('请先导出当前草稿，再创建新版本');
      next.revision=value.revision+1;validate(next,data);
      undo.push(clone(value));if(undo.length>40)undo.shift();redo.length=0;value=next;return true;
    }
    return {
      snapshot:()=>clone(value),active:()=>clone(active()),changes:()=>differences(active(),data),budget:()=>budget(active(),data),
      selectRole(id){if(!value.roles.some(r=>r.id===id))throw Error('角色不存在');value.selected_role_id=id;},
      connect(semantic,resource){
        if(!own(data.semantics,semantic))throw Error('表情不存在');
        if(resource!==null&&!data.semantics[semantic].allowed_resources.includes(resource))throw Error('动画与当前圆屏布局不兼容');
        return commit(next=>{next.roles.find(r=>r.id===next.selected_role_id).bindings[semantic]=resource;});
      },
      restore(semantic){if(!own(data.semantics,semantic))throw Error('表情不存在');return this.connect(semantic,data.semantics[semantic].resource_id);},
      rename(name){return commit(next=>{next.roles.find(r=>r.id===next.selected_role_id).name=name.trim();});},
      cloneRole(){
        if(value.roles.length>=16)throw Error('最多保存 16 个角色草稿');
        let n=1;while(value.roles.some(r=>r.id==='role-copy-'+n))n++;
        const id='role-copy-'+n;
        commit(next=>{const row=clone(active());row.id=id;row.name=Array.from(row.name).slice(0,73).join('')+' · 副本';row.example=true;next.roles.push(row);next.selected_role_id=id;});return id;
      },
      importText(raw){const next=clone(parse(raw,data));undo.push(clone(value));if(undo.length>40)undo.shift();redo.length=0;value=next;},
      exportDocument:()=>clone(validate(value,data)),
      canUndo:()=>undo.length>0,canRedo:()=>redo.length>0,
      undo(){if(!undo.length)return false;redo.push(clone(value));value=undo.pop();return true;},
      redo(){if(!redo.length)return false;undo.push(clone(value));value=redo.pop();return true;}
    };
  }
  return {SCHEMA,MAX_BYTES,validate,parse,create,budget,differences,frameAt};
});
