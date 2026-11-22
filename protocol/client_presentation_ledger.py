"""Durable local handoff records; no serial or HTTP I/O occurs in transactions."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class PresentationLedger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,id))')
        self.db.commit()

    def get(self, kind, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
            return json.loads(row[0]) if row else None

    def all(self, kind):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                'SELECT value FROM records WHERE kind=? ORDER BY rowid', (kind,))]

    def put(self, kind, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value',
                            (kind, key, canonical(value)))
        return deepcopy(value)

    def create(self, kind, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?)', (kind, key, canonical(value)))
            return self.get(kind, key)

    def close(self):
        with self.lock:
            self.db.close()
