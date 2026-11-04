/* Pure schema validation and evidence joins. No DOM, device, storage or I/O. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ExpressionObservationCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function() {
  'use strict';
  const MAX_FILE_BYTES = 65536;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const fail = (path, message) => { throw Error(`${path}: ${message}`); };
  function check(value, rule, schema, path) {
    if (rule.$ref) {
      if (!rule.$ref.startsWith('#/$defs/')) fail(path, '不支持的 schema 引用');
      return check(value, schema.$defs[rule.$ref.slice(8)], schema, path);
    }
    if (own(rule, 'const') && value !== rule.const) fail(path, '字段值与 E1.26 契约不符');
    if (rule.enum && !rule.enum.includes(value)) fail(path, '不在固定语义白名单中');
    if (rule.type === 'object') {
      if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(path, '需要 JSON 对象');
      for (const key of rule.required || []) if (!own(value, key)) fail(path, `缺少 ${key}`);
      for (const key of Object.keys(value)) {
        if (!own(rule.properties || {}, key)) {
          if (rule.additionalProperties === false) fail(path, '含未定义字段');
        } else check(value[key], rule.properties[key], schema, `${path}.${key}`);
      }
    } else if (rule.type === 'array') {
      if (!Array.isArray(value) || value.length > rule.maxItems) fail(path, '最多允许 24 条记录');
      value.forEach((row, index) => check(row, rule.items, schema, `${path}[${index}]`));
    } else if (rule.type === 'integer') {
      if (!Number.isSafeInteger(value) || value < rule.minimum || value > rule.maximum) fail(path, '整数超出协议边界');
    } else if (rule.type === 'string' && typeof value !== 'string') fail(path, '需要字符串');
  }
  function validate(snapshot, schema) {
    if (schema?.properties?.schema?.const !== 'deskbot.expression.runtime_observation.e1_26/v1') throw Error('观察 schema 不受支持');
    check(snapshot, schema, schema, 'snapshot');
    let previous = 0;
    for (const row of snapshot.records) {
      if (row.sequence <= previous) throw Error('sequence 必须唯一且递增；不自动重排证据');
      previous = row.sequence;
    }
    // Uptime is uint32 and may wrap. Sequence, not raw timestamp sorting, is
    // the ordering evidence shown by this workbench.
    return JSON.parse(JSON.stringify(snapshot));
  }
  function parse(text, schema) {
    if (typeof text !== 'string' || new TextEncoder().encode(text).length > MAX_FILE_BYTES) throw Error('JSON 快照不得超过 64 KiB');
    return validate(JSON.parse(text), schema);
  }
  function summarize(snapshot, reference) {
    const bySemantic = new Map();
    for (const row of snapshot.records) {
      let item = bySemantic.get(row.semantic);
      if (!item) {
        item = {semantic: row.semantic, records: 0, callsAtLeast: 0,
          firstSequence: row.sequence, lastSequence: row.sequence,
          reference: own(reference, row.semantic) ? reference[row.semantic].resource_id : null};
        bySemantic.set(row.semantic, item);
      }
      item.records++;
      item.callsAtLeast += row.repeat_count;
      item.lastSequence = row.sequence;
    }
    return {recordCount: snapshot.records.length,
      callsAtLeast: snapshot.records.reduce((sum, row) => sum + row.repeat_count, 0),
      semantics: [...bySemantic.values()], overwritten: snapshot.records_overwritten,
      contentionDrops: snapshot.records_dropped_contention,
      sequenceDrops: snapshot.records_dropped_sequence_exhausted,
      wrappedRecords: snapshot.records.filter(row => row.last_seen_ms < row.first_seen_ms).length};
  }
  return {MAX_FILE_BYTES, validate, parse, summarize};
});
