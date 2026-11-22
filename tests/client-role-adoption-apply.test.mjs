import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,rmSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {applyRoleAdoption} from '../integrations/client-role-adoption/apply.mjs';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const base=resolve(root,'tmp/deskbot-client-capability-4a-20261116');
const manifest=JSON.parse(readFileSync(resolve(root,'integrations/client-role-adoption/manifest.json'),'utf8'));

function checkout(t){
  const parent=mkdtempSync(join(tmpdir(),'role-adoption-patch-'));
  const folder=join(parent,'client');mkdirSync(folder);
  for(const file of [...Object.keys(manifest.before),...Object.keys(manifest.required)]){
    const target=join(folder,file);mkdirSync(join(target,'..'),{recursive:true});
    writeFileSync(target,readFileSync(join(base,file)));
  }
  execFileSync('git',['init','-q',folder],{windowsHide:true});
  t.after(()=>rmSync(parent,{recursive:true,force:true}));
  return folder;
}

test('reviewed patch applies once and recognizes an unchanged repeat',t=>{
  const folder=checkout(t);
  assert.equal(applyRoleAdoption(folder).status,'applied');
  assert.equal(applyRoleAdoption(folder).status,'already_applied');
  for(const [file,hash] of Object.entries(manifest.after)){
    const bytes=readFileSync(join(folder,file));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),hash);
  }
});

test('a changed target file stops before applying and preserves local bytes',t=>{
  const folder=checkout(t),file='apps/deskbot-service/src/chat-orchestrator.mjs';
  const target=join(folder,file),custom=Buffer.concat([readFileSync(target),Buffer.from('\n// local edit\n')]);
  writeFileSync(target,custom);
  assert.throws(()=>applyRoleAdoption(folder),/reviewed_4b1a_baseline_required/);
  assert.deepEqual(readFileSync(target),custom);
  assert.equal(existsSync(join(folder,'apps/deskbot-service/src/installed-presentation.mjs')),false);
});

test('missing or changed recovery dependency is reported before either application path',t=>{
  const folder=checkout(t),file=join(folder,'apps/deskbot-service/src/chat-turn-recovery.mjs');
  const original=readFileSync(file);rmSync(file);
  assert.throws(()=>applyRoleAdoption(folder),/reviewed_recovery_dependencies_required/);
  writeFileSync(file,original);applyRoleAdoption(folder);
  writeFileSync(file,'// changed recovery');
  assert.throws(()=>applyRoleAdoption(folder),/reviewed_recovery_dependencies_required/);
});

test('archived baseline is reserved for reference and regression',()=>{
  assert.throws(()=>applyRoleAdoption(base),/frozen_baseline_reserved/);
});

test('a repository subdirectory is rejected without changing the checkout',t=>{
  const folder=checkout(t),file=join(folder,'apps/deskbot-service/src/chat-orchestrator.mjs');
  const original=readFileSync(file);
  assert.throws(()=>applyRoleAdoption(join(folder,'apps/deskbot-service')),/explicit_client_repository_root_required/);
  assert.deepEqual(readFileSync(file),original);
  assert.equal(existsSync(join(folder,'apps/deskbot-service/src/installed-presentation.mjs')),false);
});
