/* Pure mapping state: no DOM, storage, hardware or firmware writer. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ExpressionPreviewCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  function create(data) {
    const base = copy(data.snapshots.deployed);
    const draft = copy(base), undo = [], redo = [];
    let seq = 0;
    const journal = [];
    function check(semantic) {
      if (!own(base, semantic)) throw Error('未知语义');
    }
    function apply(semantic, resource, remember = true) {
      check(semantic);
      if (resource !== null && !own(data.assets, resource)) throw Error('资源不在官方白名单中');
      const old = draft[semantic].resource_id;
      if (old === resource) return false;
      if (remember) { undo.push({semantic, from: old, to: resource}); redo.length = 0; }
      // Replace only the edge. Timing, layout and action never come from the target asset.
      draft[semantic].resource_id = resource;
      journal.unshift({sequence: ++seq, semantic, disconnected: old, connected: resource,
        scope: 'local_draft_only', device_changed: false});
      journal.splice(24);
      return true;
    }
    function connect(from, to) {
      if (from.kind !== 'semantic' || to.kind !== 'resource') throw Error('只允许语义 → EAF 资源接线');
      return apply(from.id, to.id);
    }
    function changes() {
      return Object.keys(base).filter(id => draft[id].resource_id !== base[id].resource_id)
        .map(id => ({semantic: id, before: base[id].resource_id, after: draft[id].resource_id}));
    }
    function exportPatch() {
      if (Object.values(draft).some(entry => entry.resource_id === null)) throw Error('有未接线的语义，不能导出');
      return {schema: 'deskbot.expression_mapping_draft/v1', edition: 'E1.19',
        base_package_sha256: data.baselines.deployed.package_sha256,
        base_index_sha256: data.baselines.deployed.index_sha256,
        firmware_deployable: false, hardware_accessed: false,
        changes: changes().map(change => ({...change, resource_sha256: data.assets[change.after].deployed_sha256,
          fps: base[change.semantic].fps, loop: base[change.semantic].loop,
          action_id: base[change.semantic].action_id, layout_id: base[change.semantic].layout_id})),
        constraints: {action_map_changed: false, timing_changed: false, layout_changed: false,
          eaf_payloads_changed: false, resources_removed: false},
        next_gate: 'independent_mapping_review_then_package_player_and_rollback_audit'};
    }
    return {snapshot: () => copy(draft), changes, connect, disconnect: id => apply(id, null),
      history: () => copy(journal), canUndo: () => undo.length > 0, canRedo: () => redo.length > 0,
      undo() { const edit = undo.pop(); if (!edit) return false; apply(edit.semantic, edit.from, false); redo.push(edit); return true; },
      redo() { const edit = redo.pop(); if (!edit) return false; apply(edit.semantic, edit.to, false); undo.push(edit); return true; },
      restore(id) { check(id); return apply(id, base[id].resource_id); }, exportPatch};
  }
  return {create};
});
