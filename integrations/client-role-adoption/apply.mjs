import {readFileSync,existsSync,writeFileSync,realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const here=fileURLToPath(new URL('.',import.meta.url));
const sha=path=>createHash('sha256').update(readFileSync(path)).digest('hex');
const samePath=(a,b)=>process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;
const newFiles={'apps/deskbot-service/src/installed-presentation.mjs':'installed-presentation.mjs',
  'apps/deskbot-service/test/installed-presentation.test.mjs':'installed-presentation.test.mjs'};
export function applyRoleAdoption(clientRoot){
  // Native resolution expands Windows 8.3 aliases, matching Git's long paths.
  const root=realpathSync.native(resolve(clientRoot)),manifest=JSON.parse(readFileSync(resolve(here,'manifest.json'),'utf8'));
  const frozen=realpathSync.native(resolve(here,'../../tmp/deskbot-client-capability-4a-20261116'));
  if(samePath(root,frozen))throw Error('frozen_baseline_reserved');
  const gitRoot=realpathSync.native(execFileSync('git',['-C',root,'rev-parse','--show-toplevel'],{encoding:'utf8',windowsHide:true}).trim());
  if(!samePath(gitRoot,root))throw Error('explicit_client_repository_root_required');
  const patch=resolve(here,'client-role-adoption.patch');
  if(sha(patch)!==manifest.patch_sha256)throw Error('role_adoption_patch_mismatch');
  for(const [file,source] of Object.entries(newFiles))
    if(sha(resolve(here,source))!==manifest.after[file])throw Error('role_adoption_module_mismatch');
  const matches=files=>Object.entries(files).every(([path,hash])=>existsSync(resolve(root,path))&&sha(resolve(root,path))===hash);
  if(!matches(manifest.required))throw Error('reviewed_recovery_dependencies_required');
  if(matches(manifest.after))return {status:'already_applied',client_root:root};
  if(!matches(manifest.before))throw Error('reviewed_4b1a_baseline_required');
  for(const file of Object.keys(newFiles))if(existsSync(resolve(root,file)))throw Error('installed_presentation_already_exists');
  execFileSync('git',['-C',root,'apply','--check',patch],{windowsHide:true});
  execFileSync('git',['-C',root,'apply',patch],{windowsHide:true});
  // Windows Git may preserve the baseline's CRLF. Deliver the reviewed LF
  // bytes so the final source digest is independent of local autocrlf rules.
  for(const file of Object.keys(manifest.before)){
    const path=resolve(root,file);
    writeFileSync(path,readFileSync(path,'utf8').replace(/\r\n/g,'\n'));
  }
  for(const [file,source] of Object.entries(newFiles))writeFileSync(resolve(root,file),readFileSync(resolve(here,source)));
  if(!matches(manifest.after))throw Error('role_adoption_result_mismatch');
  return {status:'applied',client_root:root};
}

if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url){
  if(process.argv.length!==3)throw Error('usage: node apply.mjs <client-root>');
  process.stdout.write(JSON.stringify(applyRoleAdoption(process.argv[2]))+'\n');
}
