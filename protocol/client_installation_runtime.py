"""One background owner transfers saved installations; HTTP runs outside device locks."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlencode

from protocol.client_installation_sync import InstallationHTTP, prepare, validate_result
from protocol.client_presentation_ledger import canonical
from protocol.role_activation import StateOwner, atomic_save, RoleStorageError
from protocol.studio_usb_session import _unique

SCHEMA = 'deskbot.client-installation-runtime.v1'


class ClientInstallationRuntime:
    def __init__(self, installation, path, *, url, source_id, character_id, device_id,
                 client=None, background=True, interval=2, writer=atomic_save):
        if not all(type(value) is str and value.strip() and len(value) <= 128
                   for value in (source_id, character_id, device_id)):
            raise ValueError('请提供安装来源、伙伴和设备编号')
        self.installation, self.path = installation, Path(path).resolve()
        self.client = client or InstallationHTTP(url, timeout=3)
        # Validate addresses even when tests inject a transport.
        InstallationHTTP(url)
        self.config = dict(source_id=source_id, character_id=character_id, device_id=device_id,
                           client_url=url.rstrip('/'), installation_path=str(Path(installation.path).resolve()))
        self.writer, self.interval = writer, interval
        self.lock, self.tick_lock = threading.RLock(), threading.Lock()
        self.stop = threading.Event()
        self.thread, self.closed = None, False
        self.status, self.error, self.checked_at = 'checking', None, None
        self.verified_key, self.last_receipt, self.verified_at = None, None, None
        self.owner = StateOwner(self.path)
        try:
            if self.path.exists():
                if self.path.stat().st_size > 32 * 1024:
                    raise ValueError('同步记录大小无效')
                doc = json.loads(self.path.read_bytes(), object_pairs_hook=_unique)
                if (type(doc) is not dict or set(doc) != {'schema', 'config', 'last_receipt'}
                        or doc['schema'] != SCHEMA or doc['config'] != self.config
                        or (doc['last_receipt'] is not None and type(doc['last_receipt']) is not dict)):
                    raise ValueError('同步配置与原记录不同，请沿用原来源和安装库')
                self.last_receipt = doc['last_receipt']
            else:
                self._persist(None)
            if background:
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        except Exception:
            self.owner.close()
            raise

    def _persist(self, receipt):
        self.writer(self.path, dict(schema=SCHEMA, config=self.config, last_receipt=receipt))
        self.last_receipt = deepcopy(receipt)

    def _value(self):
        return prepare(self.installation.state(), **{k: self.config[k] for k in ('source_id', 'character_id', 'device_id')})

    @staticmethod
    def _key(value):
        return None if value is None else (value['installation']['installation_id'],
            hashlib.sha256(canonical(value['installation']).encode()).hexdigest())

    def _still_current(self, key):
        return not self.stop.is_set() and self._key(self._value()) == key

    def _get(self):
        query = urlencode({k: self.config[k] for k in ('character_id', 'device_id')})
        state = self.client.request('GET', '/api/hardware/installation?' + query)
        if (type(state) is not dict or state.get('scope') != 'installation_facts' or state.get('live_device_state') is not False
                or any(state.get(k) != self.config[k] for k in ('character_id', 'device_id'))):
            raise ValueError('客户端同步查询与当前伙伴或设备不同')
        return state.get('current')

    def _checked_receipt(self, current, value):
        if current is None:
            return None
        if type(current) is not dict:
            raise ValueError('客户端当前安装结果不完整')
        if current.get('installation_id') != value['installation']['installation_id']:
            return None
        if (current.get('source_id') != self.config['source_id']
                or current.get('installation_sha256') != self._key(value)[1]):
            raise ValueError('客户端当前安装内容与工作台不同')
        result = validate_result(value, dict(accepted=True, duplicate=True, receipt=current.get('receipt'),
            current_installation_id=current['installation_id']))
        return result['result']['receipt']

    def _record(self, receipt):
        with self.lock:
            if receipt != self.last_receipt:
                self._persist(receipt)

    def tick(self):
        if self.stop.is_set() or not self.tick_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                if self.status != 'synchronized':
                    self.status, self.error, self.verified_key = 'checking', None, None
            value = self._value()
            key = self._key(value)
            if value is None:
                with self.lock:
                    self.status, self.verified_key = 'waiting', None
                return
            if not self._still_current(key):
                return
            current = self._get()
            if not self._still_current(key):
                return
            receipt = self._checked_receipt(current, value)
            if receipt is None:
                result = self.client.request('POST', '/api/hardware/installations', value)
                checked = validate_result(value, result)
                # A slow old reply remains attached to its own installation.
                self._record(checked['result']['receipt'])
                if not self._still_current(key):
                    return
                current = self._get()
                if not self._still_current(key):
                    return
                receipt = self._checked_receipt(current, value)
                if receipt is None:
                    raise ValueError('等待客户端当前安装核对')
            self._record(receipt)
            if self._still_current(key):
                with self.lock:
                    self.status, self.verified_key = 'synchronized', key
                    self.error = None
                    self.verified_at = time.monotonic()
                    self.checked_at = datetime.now(timezone.utc).isoformat()
        except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
            with self.lock:
                self.status, self.error, self.verified_key = 'retrying', str(error), None
        finally:
            self.tick_lock.release()

    def snapshot(self, installation_state=None):
        state = self.installation.state() if installation_state is None else installation_state
        try:
            value = prepare(state, **{k: self.config[k] for k in ('source_id', 'character_id', 'device_id')})
        except (ValueError, KeyError, TypeError):
            value = None
        key = self._key(value)
        with self.lock:
            verified = (not self.closed and key is not None and key == self.verified_key
                        and self.status == 'synchronized' and self.verified_at is not None
                        and time.monotonic() - self.verified_at <= 10)
            status = 'synchronized' if verified else self.status if self.status != 'synchronized' else 'waiting'
            return dict(schema=SCHEMA, status=status, verified=verified, checked_at=self.checked_at,
                installation_id=state.get('current'), source_id=self.config['source_id'],
                last_receipt=deepcopy(self.last_receipt), error=self.error,
                scope='installation_facts', runtime_adoption='pending')

    def _loop(self):
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(10)
            if self.thread.is_alive():
                raise RoleStorageError('同步任务正在结束，请稍后关闭')
        # A manually invoked tick also completes before releasing the file owner.
        with self.tick_lock:
            if not self.closed:
                self.closed = True
                self.owner.close()
