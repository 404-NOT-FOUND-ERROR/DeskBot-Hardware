// Isolated original client: real HTTP/SQLite, fixed model, no device transport.
import {existsSync, mkdirSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';

const [checkout, database] = process.argv.slice(2);
if (!checkout || !database || existsSync(database)) throw new Error('fresh_database_required');
const load = name => import(pathToFileURL(resolve(checkout, 'apps/deskbot-service/src', name + '.mjs')));
const {createDeskBotServer} = await load('app');
const {createSqlitePersistence} = await load('persistence');
const {bindRuntimeStore} = await load('runtime-profile');
const now = () => new Date('2026-11-16T01:00:00Z');
mkdirSync(dirname(database), {recursive: true});
const persistence = createSqlitePersistence({filename: database, now});
bindRuntimeStore(persistence, {scope: 'lab'});
let calls = 0;
const server = createDeskBotServer({persistence, now, websocket: false, timeMode: 'simulation',
  worldLifeEnabled: false, autonomousLifeEnabled: false, residentLifeEnabled: false,
  llm: {async complete({userText}) {
    return {text: userText.includes('开心') ? '听到你的分享，我也很开心。' : '你好，很高兴见到你。',
      provider: 'handoff-fixed', model: 'offline', trace: {calls: ++calls}};
  }}});
let closing = false;
function close() {
  if (closing) return;
  closing = true;
  server.close(() => {persistence.close(); process.exit(0);});
  server.closeAllConnections();
}
const input = createInterface({input: process.stdin});
input.on('line', line => {if (line === 'stop') close();});
input.on('close', close);
server.listen(0, '127.0.0.1', () => process.stdout.write(JSON.stringify({
  url: `http://127.0.0.1:${server.address().port}`, scope: 'lab', clock: now().toISOString()
}) + '\n'));
