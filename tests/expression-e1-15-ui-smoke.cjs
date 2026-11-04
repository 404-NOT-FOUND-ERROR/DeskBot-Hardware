/* Run the workbench controller against a small DOM/canvas double; not browser QA. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'artifacts/expression-e1-15-surprised-shocked-20261024/index.html'), 'utf8');
const data = JSON.parse(page.match(/<script id="e115-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = new Map(); this.attributes = {};
    this.dataset = {}; this.disabled = false; this.value = ''; this.textContent = '';
  }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  emit(name) { assert.ok(this.listeners.has(name), `missing listener ${this.tag}/${name}`); this.listeners.get(name)(); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getContext(kind) {
    assert.equal(kind, '2d');
    return {clearRect() {}, save() {}, beginPath() {}, arc() {}, clip() {}, fillRect() {}, drawImage() {}, restore() {}, set fillStyle(_) {}};
  }
}
const ids = new Map([...page.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], new Element('existing')]));
ids.get('e115-data').textContent = JSON.stringify(data);
const events = new Map();
const document = {
  body: new Element('body'),
  hidden: false,
  getElementById(id) { assert.ok(ids.has(id), `unresolved id ${id}`); return ids.get(id); },
  createElement(tag) { return new Element(tag); },
  addEventListener(name, callback) { events.set(name, callback); },
};
class MockImage {
  set src(value) { this.url = value; this.onload(); }
}
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter((match) => !match[1].includes('application/json'));
assert.equal(scripts.length, 1);
vm.runInNewContext(scripts[0][2], {
  document, Image: MockImage, Promise, Map, Set, cancelAnimationFrame() {}, requestAnimationFrame() { return 1; },
  performance: {now() { return 1;}}, queueMicrotask,
}, {timeout: 3000});

(async () => {
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(document.body.dataset.ready, 'true', ids.get('preview-error').textContent);
  assert.equal(ids.get('play').disabled, false);
  assert.equal(ids.get('before').dataset.frame, '0');
  ids.get('frame').value = '500'; ids.get('frame').emit('input');
  assert.ok(Number(ids.get('before').dataset.frame) > 0);
  assert.ok(Number(ids.get('after').dataset.frame) > 0);
  ids.get('play').emit('click');
  assert.equal(ids.get('play').textContent, '暂停');
  ids.get('play').emit('click');
  assert.equal(ids.get('play').textContent, '播放');
  ids.get('mode').value = 'rollback'; ids.get('mode').emit('change');
  assert.equal(ids.get('resource-node').textContent, 'Happy.eaf');
  assert.match(ids.get('after-title').textContent, /回退/);
  assert.match(ids.get('sharing').textContent, /10 个语义/);
  ids.get('mode').value = 'proposal'; ids.get('mode').emit('change');
  assert.equal(ids.get('resource-node').textContent, 'shocked.eaf');
  assert.match(ids.get('sharing').textContent, /9 个语义/);
  assert.equal(data.decision.deployment_eligible, false);
  console.log('E1.15 controller smoke passed: embedded atlases, proposal/rollback switch; DOM/canvas double only');
})().catch((error) => { console.error(error); process.exitCode = 1; });
