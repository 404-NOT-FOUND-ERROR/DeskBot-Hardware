import {execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';
import {request as httpRequest} from 'node:http';
import {networkInterfaces} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';

const ssidPattern = /^Xiaozhi-[A-F0-9]{4}$/;
const trialUrlPattern = /^http:\/\/(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)[0-9.]+:\d{4,5}\/deskbot-voice\/[A-Za-z0-9_-]{24}$/;
const scope = 'deskbot.voice-trial-address.v1';
const validAddress = value => typeof value === 'string' && Buffer.byteLength(value) < 256 && !value.includes('\0');

function profile(value) {
  const url = value?.ota_url ?? '';
  if (!validAddress(url) || !Number.isInteger(value?.max_tx_power) ||
      typeof value.remember_bssid !== 'boolean' || typeof value.sleep_mode !== 'boolean') {
    throw new Error('unexpected_setup_page');
  }
  return {url,power:value.max_tx_power,remember:value.remember_bssid,sleep:value.sleep_mode};
}

export async function changeTrialAddress({action,ssid,rollbackFile,trialUrl,request,restart=false}) {
  if (!ssidPattern.test(ssid) || !['apply','restore'].includes(action)) throw new Error('invalid_setup_action');
  if (action === 'apply' && (!trialUrlPattern.test(trialUrl) || new URL(trialUrl).href !== trialUrl))
    throw new Error('invalid_trial_address');
  const before = profile(await request('/advanced/config'));
  let saved;
  try { saved=JSON.parse(readFileSync(rollbackFile,'utf8')); }
  catch (error) {
    if (error.code !== 'ENOENT' || action !== 'apply') throw error;
    if (before.url === trialUrl || before.url.includes('/deskbot-voice/')) throw new Error('original_address_not_recorded');
    saved={schema:scope,ssid,original:before.url,trial:trialUrl};
    // Reserve the original setting before changing anything; repeat apply never overwrites it.
    writeFileSync(rollbackFile,JSON.stringify(saved)+'\n',{flag:'wx',mode:0o600});
  }
  if (saved?.schema !== scope || saved.ssid !== ssid || !validAddress(saved.original) ||
      !trialUrlPattern.test(saved.trial) || (action === 'apply' && saved.trial !== trialUrl))
    throw new Error('restore_record_mismatch');
  if (![saved.original,saved.trial].includes(before.url)) throw new Error('device_address_changed_elsewhere');
  const target = action === 'apply' ? saved.trial : saved.original;
  if (before.url !== target && (await request('/advanced/submit',{ota_url:target})).success !== true)
    throw new Error('setup_save_failed');
  const after = profile(await request('/advanced/config'));
  if (after.url !== target || after.power !== before.power || after.remember !== before.remember || after.sleep !== before.sleep)
    throw new Error('setup_readback_mismatch');
  if (restart && (await request('/reboot',{})).success !== true) throw new Error('setup_restart_failed');
  return {status:action === 'apply' ? 'trial_address_saved' : 'original_address_restored',
    restarted:restart, wifi_settings_preserved:true};
}

function requestSetup(localAddress, path, value) {
  const body = value === undefined ? undefined : Buffer.from(JSON.stringify(value));
  return new Promise((resolve, reject) => {
    const request = httpRequest({host:'192.168.4.1',localAddress,path,method:body ? 'POST' : 'GET',
      agent:false,signal:AbortSignal.timeout(5000),headers:body ?
        {'content-type':'application/json','content-length':body.length} : {}}, response => {
      if (response.statusCode !== 200) { response.resume(); reject(new Error(`setup_http_${response.statusCode}`)); return; }
      const chunks=[];
      let bytes=0;
      response.on('data', chunk => {
        bytes+=chunk.length;
        if (bytes>4096) response.destroy(new Error('setup_response_too_large'));
        else chunks.push(chunk);
      });
      response.on('error',reject);
      response.on('end',()=>{try {resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));} catch(error) {reject(error);}});
    });
    request.on('error',reject);
    request.end(body);
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const {values} = parseArgs({options:{action:{type:'string'},ssid:{type:'string'},
    'state-dir':{type:'string',default:resolve(import.meta.dirname,'../tmp/device-voice-trial-20261119')},restart:{type:'boolean'}}});
  // The owner reads this exact SSID from the deskpet. Bind requests to the
  // address received on that setup network instead of relying on a default route.
  if (!ssidPattern.test(values.ssid)) throw new Error('请填写桌宠屏幕上显示的 Xiaozhi-XXXX 热点名。');
  const wlan = execFileSync('netsh.exe',['wlan','show','interfaces'],{windowsHide:true,encoding:'utf8',timeout:5000});
  const ssids = [...wlan.matchAll(/^\s*SSID\s*:\s*(Xiaozhi-[A-F0-9]{4})[ \t]*\r?$/gm)].map(match => match[1]);
  const setup = Object.values(networkInterfaces()).flat().filter(item =>
    item.family === 'IPv4' && /^192\.168\.4\.[0-9]{1,3}$/.test(item.address) && item.address !== '192.168.4.1');
  if (ssids.length !== 1 || ssids[0] !== values.ssid || setup.length !== 1)
    throw new Error('请让电脑连接桌宠屏幕上显示的配网热点。');
  const stateDir = resolve(values['state-dir']);
  const trialUrl = values.action === 'apply' ?
    JSON.parse(readFileSync(join(stateDir,'connection.json'),'utf8')).setup_url : undefined;
  const result = await changeTrialAddress({action:values.action,ssid:values.ssid,
    rollbackFile:join(stateDir,'original-voice-address.json'),trialUrl,
    request:(path,value)=>requestSetup(setup[0].address,path,value),restart:values.restart});
  process.stdout.write(JSON.stringify(result)+'\n');
}
