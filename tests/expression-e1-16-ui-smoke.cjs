/* Static controller smoke for E1.16; this is not browser or LCD QA. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'artifacts/expression-e1-16-repack-20261024/index.html'), 'utf8');
const embedded = JSON.parse(page.match(/<script id="e116-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
class Element {
  constructor() { this.children = []; this.listeners = new Map(); this.attributes = {}; this.style = {}; this.textContent = ''; this.dataset = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, cb) { this.listeners.set(name, cb); }
  emit(name) { assert.ok(this.listeners.has(name), `missing event listener ${name}`); this.listeners.get(name)(); }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const ids = new Map([...page.matchAll(/\bid="([^"]+)"/g)].map(([_, id]) => [id, new Element()]));
ids.get('e116-data').textContent = JSON.stringify(embedded);
const document = {
  getElementById(id) { assert.ok(ids.has(id), `unresolved id ${id}`); return ids.get(id); },
  createElement() { return new Element(); },
};
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter(x => !x[1].includes('application/json'));
assert.equal(scripts.length, 1);
vm.runInNewContext(scripts[0][2], {document}, {timeout: 3000});
assert.equal(ids.get('member-count').textContent, '显示 29 / 29 个成员');
ids.get('changed-only').emit('click');
assert.equal(ids.get('member-count').textContent, '显示 1 / 29 个成员');
ids.get('changed-only').emit('click');
assert.equal(ids.get('member-count').textContent, '显示 29 / 29 个成员');
assert.match(ids.get('rollback-result').textContent, /逐字节通过/);
console.log('E1.16 UI smoke passed: report payload, member filter and rollback status; DOM double only');
