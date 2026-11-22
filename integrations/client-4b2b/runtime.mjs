import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

export const ROOT = resolve(fileURLToPath(new URL('../..',import.meta.url)));
export const RUNTIME = resolve(ROOT,'tmp/voice-runtime-4b2b');
const lock = JSON.parse(readFileSync(new URL('./dependencies.lock.json',import.meta.url),'utf8'));
for (const dependency of lock.packages) {
  for (const [path,expected] of Object.entries(dependency.files)) {
    const file = resolve(RUNTIME,'node_modules',dependency.name,path);
    if (createHash('sha256').update(readFileSync(file)).digest('hex') !== expected)
      throw new Error(`voice_dependency_mismatch:${dependency.name}/${path}`);
  }
}
const require = createRequire(resolve(RUNTIME,'runtime.cjs'));
export const {WebSocketServer,WebSocket} = require('ws');
export const OpusScript = require('opusscript');
