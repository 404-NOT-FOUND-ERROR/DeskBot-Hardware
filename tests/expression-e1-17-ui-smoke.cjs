/* Static controller smoke for E1.17; this is not browser or LCD QA. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'artifacts/expression-e1-17-player-20261024/index.html'), 'utf8');
const embedded = JSON.parse(page.match(/<script id="e117-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
assert.equal(embedded.edition, 'E1.17');
assert.equal(embedded.result, 'offline_c_player_compatibility_passed');
assert.equal(Object.keys(embedded.cases).length, 2);
assert.match(page, /connect-src 'none'/);
assert.doesNotMatch(page, /COM4|COM8/);
console.log('E1.17 UI smoke passed: payload, two player cases and offline CSP; DOM not exercised');
