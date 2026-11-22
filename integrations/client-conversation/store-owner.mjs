import {existsSync, mkdirSync, realpathSync} from 'node:fs';
import {basename, dirname, join, resolve} from 'node:path';
import {DatabaseSync} from 'node:sqlite';

export function lockConversationStore(filename) {
  const path = resolve(filename);
  mkdirSync(dirname(path), {recursive: true});
  const store = existsSync(path) ? realpathSync.native(path) : join(realpathSync.native(dirname(path)), basename(path));
  // The companion database holds an OS lock without blocking backups of the save.
  const lock = new DatabaseSync(store + '.owner.sqlite');
  try {
    lock.exec('BEGIN EXCLUSIVE');
  } catch (error) {
    lock.close();
    if (error.errcode === 5 || error.errcode === 6) {
      throw Object.assign(new Error('conversation_store_in_use'), {code: 'conversation_store_in_use'});
    }
    throw error;
  }
  return {store, release: () => lock.close()};
}
