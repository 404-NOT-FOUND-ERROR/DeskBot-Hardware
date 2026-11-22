// Small isolated archive served through the actual client HTTP and world modules.
import {writeFileSync, readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const [root, database, tokenFile, readyFile] = process.argv.slice(2);
const moduleAt = name => import(pathToFileURL(resolve(root, 'apps/deskbot-service/src', name)));
const {createDeskBotServer} = await moduleAt('app.mjs');
const {createSqlitePersistence} = await moduleAt('persistence.mjs');
const {createPersistentWorld} = await moduleAt('persistent-world.mjs');
const {installAutonomy} = await moduleAt('life-state.mjs');
const {installRefraction} = await moduleAt('input-refraction.mjs');
const {installLivedMemory} = await moduleAt('lived-memory.mjs');
const {installBodyPerception} = await moduleAt('body-perception.mjs');
const store = createSqlitePersistence({filename: database});
const world = createPersistentWorld({persistence: store, timeMode: 'realtime'});
const initial = world.get(), at = new Date().toISOString();
if (!initial.refraction || !initial.memory || !initial.body) {
  installAutonomy(initial, at); installRefraction(initial, at);
  installLivedMemory(initial, at); installBodyPerception(initial, at);
  store.put('canonical-world.states', initial.world_id, initial);
}
const server = createDeskBotServer({persistence: store,
  persistentWorld: createPersistentWorld({persistence: store, timeMode: 'realtime', syncClockOnOpen: false}),
  websocket: false, ownerStoryOnly: true, ownerStoryToken: readFileSync(tokenFile, 'utf8').trim(),
  hardwareTouchDevice: {
    device_id: 'device-9046b3127b3c912b392774b8ffadd819',
    firmware_sha256: '234334de23ae70abc98b5838666ddb13df72c3569f2eaa4ba01018e0b0004e97',
  },
});
server.listen(0, '127.0.0.1', () => writeFileSync(readyFile, JSON.stringify({port: server.address().port})));
server.once('close', () => store.close());
