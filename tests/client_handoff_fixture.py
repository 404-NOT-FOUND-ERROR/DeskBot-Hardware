"""Bounded isolated client process and a recorder around the real HTTP adapter."""
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

from protocol.client_presentation import ClientHTTP

ROOT = Path(__file__).resolve().parents[1]


class IsolatedClient:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.stderr = (self.directory / 'server.log').open('w+', encoding='utf-8')
        self.process = None
        try:
            env = dict(os.environ, DESKBOT_RUN_SCOPE='lab',
                       DESKBOT_DB_PATH=str(self.directory / 'client.sqlite'))
            self.process = subprocess.Popen([
                env['DESKBOT_HANDOFF_NODE'], str(ROOT / 'integrations/client-handoff/server.mjs'),
                env['DESKBOT_HANDOFF_CLIENT'], env['DESKBOT_DB_PATH']],
                cwd=self.directory, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.stderr, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ready = queue.Queue()
            reader = threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True)
            reader.start()
            try:
                line = ready.get(timeout=15)
            except queue.Empty:
                raise RuntimeError('client_startup_timeout') from None
            if not line:
                self.stderr.seek(0)
                raise RuntimeError('client_startup_failed: ' + self.stderr.read()[-4000:])
            value = json.loads(line)
            if value['scope'] != 'lab':
                raise RuntimeError('client_scope_mismatch')
            self.url = value['url']
            self.http = ClientHTTP(self.url)
        except BaseException:
            self.close(require_success=False)
            raise

    def close(self, *, require_success=True, timeout=5):
        error = None
        if self.process is not None:
            process = self.process
            try:
                process.communicate('stop\n' if process.poll() is None else None, timeout=timeout)
            except (subprocess.TimeoutExpired, OSError, ValueError) as caught:
                process.kill()
                process.communicate(timeout=5)
                error = 'forced_shutdown: ' + type(caught).__name__
            finally:
                self.process = None
            if process.returncode != 0:
                error = (error + '; ' if error else '') + 'exit=' + str(process.returncode)
        self.stderr.close()
        if error and require_success:
            raise RuntimeError('client_shutdown_failed: ' + error)


class RecordingHTTP:
    def __init__(self, http):
        self.http, self.calls, self.drop_ack = http, [], False

    def request(self, method, path, value=None):
        response = self.http.request(method, path, value)
        self.calls.append(dict(method=method, path=path, request=deepcopy(value), response=deepcopy(response)))
        # The server has committed this ACK; only its return is lost.
        if self.drop_ack and method == 'POST' and path.endswith('/ack'):
            self.drop_ack = False
            raise OSError('fixture_ack_reply_lost')
        return response
