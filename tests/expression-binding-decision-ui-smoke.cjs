/* Execute the actual page controller with a bounded DOM double. No browser,
 * layout engine, networking or device APIs; this is not visual QA. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'artifacts/expression-binding-decision-e1-14-20261024/index.html'), 'utf8');
const data = JSON.parse(page.match(/<script id="decision-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = new Map(); this.attributes = {};
    this.style = {}; this.dataset = {}; this.value = ''; this.textContent = ''; this.html = '';
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(event, fn) { this.listeners.set(event, fn); }
  emit(event) { assert.ok(this.listeners.has(event), 'missing event handler: '+event); this.listeners.get(event)(); }
  set innerHTML(value) {
    this.html = value; this.children = [];
    for (const match of value.matchAll(/<button\b[^>]*data-route="([^"]+)"[^>]*>/g)) {
      const button = new Element('button'); button.dataset.route = match[1]; this.children.push(button);
    }
  }
  get innerHTML() { return this.html; }
  querySelectorAll(selector) {
    assert.equal(selector, '[data-route]');
    return this.children.filter((node) => node.dataset.route);
  }
}
const ids = new Map([...page.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], new Element('existing')]));
ids.get('decision-data').textContent = JSON.stringify(data);
const document = {
  getElementById(id) { assert.ok(ids.has(id), 'unresolved DOM id: '+id); return ids.get(id); },
  createElement(tag) { return new Element(tag); },
  createElementNS(ns, tag) { assert.equal(ns, 'http://www.w3.org/2000/svg'); return new Element(tag); },
};
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter((match) => !match[1].includes('application/json'));
assert.equal(scripts.length, 1);
vm.runInNewContext(scripts[0][2], {document, Intl, Map, Set}, {timeout: 3000});
const el = (id) => document.getElementById(id);
const texts = () => el('binding-graph').children.flatMap((node) => [node.textContent, ...node.children.map((child) => child.textContent)]).filter(Boolean);
assert.equal(el('resource-list').children.length, 19);
assert.equal(el('route-select').children.flatMap((group) => group.children).length, 56);

for (const item of data.resource_decisions) {
  const button = el('resource-list').children.find((node) => node.innerHTML.includes('>'+item.resource_id+'<'));
  assert.ok(button, item.resource_id); button.emit('click');
  assert.ok(el('resource-detail').innerHTML.includes('<h2>'+item.resource_id+'</h2>'));
  assert.equal(el('resource-list').children.filter((node) => node.attributes['aria-pressed'] === 'true').length, 1);
}
for (const route of data.route_projections) {
  el('route-select').value = route.route_id; el('route-select').emit('change');
  assert.ok(texts().includes(route.trigger+'的静态绑定关系'));
  for (const output of route.outputs) {
    assert.ok(texts().includes(output.src)); assert.ok(texts().includes(output.layout));
  }
}
el('route-select').value = 'route:pet-head'; el('route-select').emit('change');
assert.ok(texts().includes('服务端动态值'));
assert.ok(!texts().includes('Happy.eaf'));
assert.ok(el('resource-detail').innerHTML.includes('没有固定 EAF'));
el('route-select').value = 'route:pair-detect'; el('route-select').emit('change');
assert.ok(texts().includes('语义：摇头'));
assert.ok(texts().includes('入口：好奇环顾'));

for (const [kind,count] of [['core',5],['protected_shared',3],['protected_single',10],['offline_candidate',1]]) {
  el('kind-filter').value = kind; el('kind-filter').emit('change');
  assert.equal(el('resource-list').children.length, count);
}
assert.ok(el('resource-detail').innerHTML.includes('<h2>shy_legacy.eaf</h2>'));
assert.ok(texts().includes('Happy.eaf')); // Candidate selection preserves official mapping.
assert.ok(!texts().includes('shy_legacy.eaf'));
for (const button of el('resource-detail').querySelectorAll('[data-route]')) button.emit('click');
for (let i=0;i<10;i++) el('zoom-out').emit('click');
assert.equal(el('zoom-label').textContent, '80%');
for (let i=0;i<10;i++) el('zoom-in').emit('click');
assert.equal(el('zoom-label').textContent, '140%');
el('zoom-reset').emit('click'); assert.equal(el('zoom-label').textContent, '100%');
console.log('E1.14 controller smoke passed: 56 routes, 19 resources, filters, dynamic/candidate isolation and zoom; DOM double only');
