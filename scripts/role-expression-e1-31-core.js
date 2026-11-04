/* Offline draft state. No DOM, storage, device I/O or firmware generation. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RoleExpressionDraft = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'deskbot.role_expression_draft/v1', MAX_BYTES = 512 * 1024;
  const copy = v => JSON.parse(JSON.stringify(v));
  const own = (v, k) => Object.prototype.hasOwnProperty.call(v, k);
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const keys = (v, expected) => object(v) && Object.keys(v).length === expected.length && expected.every(k => own(v, k));
  const idPattern = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
  const identifier = v => typeof v === 'string' && idPattern.test(v) && !['constructor', 'prototype'].includes(v);
  const text = (v, max) => typeof v === 'string' && v.trim().length > 0 && Array.from(v).length <= max;
  const integer = v => Number.isSafeInteger(v) && v > 0;
  // Keep this browser-side allow-list identical to protocol/deskbot_role_profile.py.
  // The workbench validates drafts before export; omitting a protocol action here
  // would reject an otherwise valid role only in the browser.
  const safeActions = ['shark_head', 'shark_head_decay', 'look_around', 'beat_swing', 'cat_nuzzle'];
  function canonical(v) {
    if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
    if (object(v)) return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
    return JSON.stringify(v);
  }
  function validateProfile(p, ctx) {
    const required = ['schema', 'shell_id', 'magnet_profile_id', 'role_id', 'display_name', 'voice_id',
      'catchphrases', 'expression_map', 'action_map', 'asset_version', 'binding_version', 'profile_revision', 'binding_state'];
    if (!object(p) || required.some(k => !own(p, k)) || Object.keys(p).some(k => ![...required, 'notes'].includes(k))) throw Error('角色资料字段缺失或含不支持的字段');
    if (p.schema !== 'deskbot.role_profile.v1' || p.binding_state !== 'draft' ||
        p.asset_version !== ctx.asset_version || p.binding_version !== ctx.binding_version) throw Error('角色必须是使用当前资源版本的 draft');
    if (!identifier(p.shell_id) || !identifier(p.role_id) ||
        new Set([p.shell_id, p.magnet_profile_id, p.role_id]).size !== 3) throw Error('外壳、磁场档案与角色 ID 必须分属独立命名空间');
    for (const [key, max] of [['magnet_profile_id',128], ['display_name',80], ['voice_id',96]]) if (!text(p[key],max)) throw Error(key + ' 为空或超长');
    if (!integer(p.profile_revision)) throw Error('profile_revision 无效');
    if (own(p, 'notes') && (typeof p.notes !== 'string' || Array.from(p.notes).length > 1000)) throw Error('notes 超长或无效');
    if (!Array.isArray(p.catchphrases) || p.catchphrases.length > 32 || p.catchphrases.some(x => typeof x !== 'string' || Array.from(x).length > 160)) throw Error('catchphrases 无效');
    for (const key of ['expression_map', 'action_map']) {
      const m = p[key];
      if (!object(m) || Object.keys(m).length > 64 || Object.entries(m).some(([k,v]) => !identifier(k) || !identifier(v))) throw Error(key + ' 无效');
    }
    if (Object.values(p.expression_map).some(v => !own(ctx.semantics, v))) throw Error('角色指向未知表情语义');
    if (Object.values(p.action_map).some(v => !safeActions.includes(v))) throw Error('动作不在已审核的普通动作列表中');
  }
  function validate(value, ctx) {
    if (!keys(value, ['schema','edition','scope','firmware_deployable','draft_revision','baseline','selected_role_id','roles'])) throw Error('草稿字段缺失或含不支持的字段');
    if (value.schema !== SCHEMA || value.edition !== 'E1.31' || value.scope !== 'offline_draft_only' || value.firmware_deployable !== false) throw Error('不是受支持的离线角色表达草稿');
    if (!integer(value.draft_revision)) throw Error('draft_revision 无效');
    if (canonical(value.baseline) !== canonical(ctx.baseline)) throw Error('基线或资源目录不匹配，未导入');
    if (!Array.isArray(value.roles) || value.roles.length < 1 || value.roles.length > 16) throw Error('角色数量须为 1–16');
    const ids = new Set(), semantics = Object.keys(ctx.semantics);
    for (const row of value.roles) {
      if (!keys(row, ['role_profile', 'semantic_resources'])) throw Error('角色草稿字段无效');
      validateProfile(row.role_profile, ctx);
      const id = row.role_profile.role_id;
      if (ids.has(id)) throw Error('角色 ID 重复');
      ids.add(id);
      if (!keys(row.semantic_resources, semantics)) throw Error('表情语义清单不完整或含未知项');
      for (const [semantic, resource] of Object.entries(row.semantic_resources)) {
        if (resource !== null && (typeof resource !== 'string' || !ctx.semantics[semantic].allowed_resources.includes(resource))) throw Error(semantic + ' 未接线或资源不兼容');
      }
    }
    if (!ids.has(value.selected_role_id)) throw Error('选中的角色不存在');
    return value;
  }
  function parse(raw, ctx) {
    if (typeof raw !== 'string' || new TextEncoder().encode(raw).byteLength > MAX_BYTES) throw Error('草稿文件超过 512 KiB');
    return validate(JSON.parse(raw.replace(/^\uFEFF/, '')), ctx);
  }
  function create(ctx, initial) {
    let state = copy(validate(initial, ctx));
    const undo = [], redo = [], journal = [];
    let seq = 0;
    const active = () => state.roles.find(r => r.role_profile.role_id === state.selected_role_id);
    const note = (operation, detail) => {journal.unshift({sequence: ++seq, operation, detail, scope:'offline_draft_only'}); journal.splice(24);};
    function change(operation, fn) {
      const before = copy(state), next = copy(state);
      const result = fn(next);
      if (result === false || canonical(next) === canonical(before)) return false;
      if (state.draft_revision >= Number.MAX_SAFE_INTEGER) throw Error('草稿修订号已达上限，请导出归档');
      next.draft_revision = state.draft_revision + 1;
      undo.push(before); if (undo.length > 40) undo.shift(); redo.length = 0;
      state = next; note(operation, result || state.selected_role_id); return true;
    }
    function role(next) { return next.roles.find(r => r.role_profile.role_id === next.selected_role_id); }
    function checkSemantic(id) { if (!own(ctx.semantics,id)) throw Error('未知语义'); }
    function setResource(semantic, resource) {
      checkSemantic(semantic);
      if (resource !== null && (typeof resource !== 'string' || !ctx.semantics[semantic].allowed_resources.includes(resource))) throw Error('资源不兼容当前布局');
      return change('rewire', next => { const r=role(next), old=r.semantic_resources[semantic]; if(old===resource)return false;
        r.semantic_resources[semantic]=resource; return {role_id:next.selected_role_id,semantic,disconnected:old,connected:resource}; });
    }
    return {
      snapshot: () => copy(state), history: () => copy(journal), active: () => copy(active()),
      selectRole(id) { if(!state.roles.some(r=>r.role_profile.role_id===id)) throw Error('角色不存在'); state.selected_role_id=id; },
      connect: setResource, disconnect: semantic => setResource(semantic,null),
      rename(name) { if(!text(name,80)) throw Error('角色名称须为 1–80 字'); return change('rename', next=>{role(next).role_profile.display_name=name;}); },
      clone() { if(state.roles.length>=16) throw Error('最多 16 个角色草稿');
        let n=1; while(state.roles.some(r=>r.role_profile.role_id==='role-copy-'+n)) n++;
        const id='role-copy-'+n;
        change('clone', next=>{const r=copy(role(next));r.role_profile.role_id=id;
          r.role_profile.display_name=Array.from(r.role_profile.display_name).slice(0,73).join('')+' · 副本';
          r.role_profile.binding_state='draft';r.role_profile.profile_revision=1;next.roles.push(r);next.selected_role_id=id;return id;}); return id;
      },
      restore(semantic) { checkSemantic(semantic); return setResource(semantic,ctx.semantics[semantic].resource_id); },
      restoreRole() {return change('restore_role',next=>{role(next).semantic_resources=Object.fromEntries(Object.entries(ctx.semantics).map(([k,v])=>[k,v.resource_id]));});},
      importText(raw) {const candidate=copy(parse(raw,ctx)); const before=copy(state); undo.push(before);if(undo.length>40)undo.shift();redo.length=0;state=candidate;note('import','validated draft');},
      exportDocument() {return copy(validate(state,ctx));},
      canUndo:()=>undo.length>0, canRedo:()=>redo.length>0,
      undo(){if(!undo.length)return false;redo.push(copy(state));state=undo.pop();note('undo',state.selected_role_id);return true;},
      redo(){if(!redo.length)return false;undo.push(copy(state));state=redo.pop();note('redo',state.selected_role_id);return true;},
      changes(){return Object.entries(active().semantic_resources).filter(([k,v])=>v!==ctx.semantics[k].resource_id).map(([semantic,to])=>({semantic,from:ctx.semantics[semantic].resource_id,to}));},
      sharing() {const counts={};for(const r of Object.keys(ctx.assets))counts[r]={baseline:0,role:0,all_roles:0,bytes:ctx.assets[r].bytes};
        for(const v of Object.values(ctx.semantics))counts[v.resource_id].baseline++;
        for(const v of Object.values(active().semantic_resources))if(v)counts[v].role++;
        for(const row of state.roles)for(const v of Object.values(row.semantic_resources))if(v)counts[v].all_roles++;
        return counts;}
    };
  }
  return {SCHEMA,MAX_BYTES,SAFE_ACTIONS:Object.freeze([...safeActions]),validate,parse,create,canonical};
});
