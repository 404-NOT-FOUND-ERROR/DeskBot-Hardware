"""Daily network settings on the existing workbench; serial access is explicit."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time
from urllib.request import build_opener, ProxyHandler, Request

from protocol import body_network_settings as network

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'deskbot.body_network_workbench/v1'
spec = importlib.util.spec_from_file_location('body_network_maintenance', ROOT / 'scripts/configure-body-network.py')
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class Busy(ValueError):
    pass


def probe_services(profile):
    services = {}
    for channel in ('touch', 'expression'):
        try:
            path = profile.parent / channel
            config = json.loads((path / 'receiver.json').read_bytes())
            context = ssl.create_default_context(cafile=str(path / config['cert_file']))
            with socket.create_connection((config['host'], config['port']), timeout=0.6) as stream:
                # Both profiles use prepare-head-touch-wifi's certificate name.
                with context.wrap_socket(stream, server_hostname='deskbot-touch.local'):
                    services[channel] = 'ready'
        except (OSError, ValueError, KeyError, TypeError):
            services[channel] = 'unavailable'
    try:
        opener = build_opener(ProxyHandler({}))
        request = Request('http://127.0.0.1:4311/api/companion/state', headers={'X-DeskBot-Client': 'companion'})
        with opener.open(request, timeout=1.2) as response:
            row = json.loads(response.read(16384))
        # WebSocket open precedes the cloud's tools/list handshake.
        services['xiaozhi'] = 'ready' if row['xiaozhi']['state'] == 'ready' else 'waiting'
    except (OSError, ValueError, KeyError, TypeError):
        services['xiaozhi'] = 'unavailable'
    return services


class NetworkWorkbench:
    def __init__(self, bridge, profile=None, *, runner=subprocess.run, probe=probe_services):
        self.bridge = bridge
        self.profile = Path(profile).resolve() if profile else None
        self.runner, self.probe = runner, probe
        self.lock = threading.RLock()
        self.task = self.thread = None
        self.settings = None
        self.history = []
        self.closed = False
        self.services = dict(touch='checking', expression='checking', xiaozhi='checking')
        self.probe_at, self.probing = 0.0, False
        self.binding = None
        if self.profile:
            self.binding = maintenance.binding(self.profile)
            self._read_recommended()

    def _read_recommended(self):
        touch = json.loads((self.profile.parent / 'touch/receiver.json').read_bytes())
        expression = json.loads((self.profile / 'receiver.json').read_bytes())
        if (touch['host'] != expression['host'] or any(
                row.get(key) != value for row in (touch, expression) for key, value in self.binding.items())):
            raise ValueError('paired_network_profiles_required')
        recommended = dict(host=expression['host'], touch_port=touch['port'], expression_port=expression['port'])
        network.operation('set', expected=0, **recommended)
        return recommended

    def snapshot(self):
        with self.lock:
            # Passive host probes run independently; page polling never opens USB.
            if self.profile and not self.closed and not self.probing and time.monotonic() - self.probe_at > 4:
                self.probing = True
                threading.Thread(target=self._probe, daemon=True).start()
            recommended = None
            if self.profile and not self.closed:
                try:
                    recommended = self._read_recommended()
                except (OSError, ValueError, KeyError, TypeError):
                    # The two files can temporarily disagree while a hotspot address is updated.
                    pass
            return dict(schema=SCHEMA, available=self.profile is not None,
                binding=deepcopy(self.binding), recommended=recommended,
                settings=deepcopy(self.settings), task=deepcopy(self.task), services=deepcopy(self.services))

    def _probe(self):
        try:
            result = self.probe(self.profile)
            with self.lock:
                self.services = result
        except (OSError, ValueError, KeyError, TypeError):
            with self.lock:
                self.services = dict(touch='unavailable', expression='unavailable', xiaozhi='unavailable')
        finally:
            with self.lock:
                self.probing = False
                self.probe_at = time.monotonic()

    def busy(self):
        with self.lock:
            return self.task is not None and self.task['status'] == 'running'

    def start(self, value):
        if type(value) is not dict or value.get('action') not in ('get', 'set', 'reset'):
            raise ValueError('invalid_network_action')
        action = value['action']
        keys = {'request_id', 'action', 'port'}
        if action != 'get': keys.add('expected_revision')
        if action == 'set': keys.update(('host', 'touch_port', 'expression_port'))
        if (set(value) != keys or type(value.get('request_id')) is not str or type(value.get('port')) is not str
                or not re.fullmatch(r'network-[a-f0-9]{32}', value['request_id'])
                or not re.fullmatch(r'COM[1-9][0-9]{0,3}', value['port'])):
            raise ValueError('invalid_network_fields')
        network.operation(action, expected=value.get('expected_revision'), host=value.get('host'),
            touch_port=value.get('touch_port', 4318), expression_port=value.get('expression_port', 4319))
        with self.lock:
            if self.closed or self.profile is None or not self.bridge.available():
                raise ValueError('network_settings_unavailable')
            duplicate = next((row for row in self.history if row['value']['request_id'] == value['request_id']), None)
            if duplicate:
                if duplicate['value'] != value: raise ValueError('network_request_changed')
                return dict(self.snapshot(), task=deepcopy(duplicate['task']))
            if self.task and self.task['status'] == 'running': raise Busy('network_settings_busy')
            if not self.bridge.operation.acquire(blocking=False): raise Busy('usb_connection_busy')
            try:
                port = next((row for row in self.bridge.ports()['ports'] if row['port'] == value['port']), None)
                if port is None: raise ValueError('head_usb_port_required')
                previous = self.bridge.snapshot()['connection']
                if previous['status'] in ('connected', 'connecting') and previous['port'] != port['port']:
                    raise Busy('other_usb_port_connected')
                with self.bridge.lock:
                    control = self.bridge.control()
                    if control.get('pending') or control.get('host_command'):
                        raise Busy('usb_command_in_progress')
                    # Background shell/role requests share this same guard.
                    self.bridge.network_maintenance = True
                self.task = dict(request_id=value['request_id'], action=action, status='running', error=None,
                                 observation_restored=False)
                self.history.append(dict(value=deepcopy(value), task=self.task))
                self.history = self.history[-32:]
                self.thread = threading.Thread(target=self._run, args=(deepcopy(value), port), daemon=True)
                self.thread.start()
            except BaseException:
                with self.bridge.lock:
                    self.bridge.network_maintenance = False
                self.bridge.operation.release()
                raise
            return self.snapshot()

    def _run(self, value, port):
        previous = self.bridge.snapshot()['connection']
        restore = previous['status'] in ('connected', 'connecting') and previous['port'] == port['port']
        error_code = None
        try:
            self.bridge._disconnect()
            command = [str(self.bridge.python), str(ROOT / 'scripts/configure-body-network.py'), value['action'],
                       '--profile', str(self.profile), '--port', port['port']]
            if value['action'] != 'get': command += ['--expected-revision', str(value['expected_revision'])]
            if value['action'] == 'set':
                for key in ('host', 'touch_port', 'expression_port'):
                    command += ['--' + key.replace('_', '-'), str(value[key])]
            result = self.runner(command, cwd=ROOT, capture_output=True, text=True, timeout=110, check=False)
            if result.returncode:
                known = ('body_network_conflict', 'body_network_save_uncertain', 'body_network_save_failed',
                    'connected_device_or_build_mismatch', 'device_restarted_during_settings', 'body_network_reply_timeout')
                raise ValueError(next((code for code in known if code in result.stderr), 'network_settings_failed'))
            row = json.loads(result.stdout)
            if type(row) is not dict:
                raise ValueError('network_settings_reply_mismatch')
            settings = network.validate_reply(row['settings'])
            if (row['schema'] != 'deskbot.body_network_maintenance/v1' or row['device_id'] != self.binding['device_id']
                    or row['firmware_sha256'] != self.binding['firmware_sha256'] or settings['target_id'] != self.binding['device_id']):
                raise ValueError('network_settings_reply_mismatch')
            with self.lock:
                self.settings = settings
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            codes = {'body_network_conflict', 'body_network_save_uncertain', 'body_network_save_failed',
                'connected_device_or_build_mismatch', 'device_restarted_during_settings', 'body_network_reply_timeout',
                'network_settings_reply_mismatch'}
            error_code = str(error) if str(error) in codes else 'network_settings_failed'
        finally:
            try:
                if restore and not self.closed:
                    self.bridge._connect(port['port'], port['key'])
                    with self.lock:
                        self.task['observation_restored'] = True
            except (OSError, ValueError):
                with self.lock:
                    self.task['observation_restored'] = False
            finally:
                with self.bridge.lock:
                    self.bridge.network_maintenance = False
                self.bridge.operation.release()
                # Completion means serial ownership has also been handed back.
                with self.lock:
                    self.task.update(status='failed' if error_code else 'completed', error=error_code)

    def close(self):
        with self.lock:
            self.closed = True
            worker = self.thread
        if worker:
            worker.join(timeout=115)


class NetworkRoutes:
    def do_GET(self):
        if self.path != '/api/body/network': return super().do_GET()
        if self.allowed(): self.json(200, self.server.body_network.snapshot())

    def do_POST(self):
        if self.path != '/api/body/network':
            if self.path in ('/api/body/connect', '/api/body/disconnect', '/api/studio/command'):
                # Include body parsing and the old route's operation in the
                # same gate: a slow request cannot queue a late disconnect.
                with self.server.body_network.lock:
                    if self.server.body_network.busy():
                        if self.allowed():
                            self.close_connection = True
                            self.json(409, dict(error='连接设置正在处理，请稍后操作'))
                        return
                    return super().do_POST()
            return super().do_POST()
        if not self.allowed(): return
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'body-network-v1' or self.headers.get('Transfer-Encoding')):
            return self.json(415, dict(error='请从连接设置页面操作'))
        try:
            lengths = self.headers.get_all('Content-Length', [])
            size = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < size <= 1024: raise ValueError('invalid_network_fields')
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size: raise ValueError('invalid_network_fields')
            from protocol.studio_usb_session import _unique
            value = json.loads(raw, object_pairs_hook=_unique)
            self.json(202, self.server.body_network.start(value))
        except Busy as error:
            self.json(409, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            codes = {'invalid_network_action', 'invalid_network_fields', 'invalid_network_revision',
                'invalid_network_address', 'network_settings_unavailable', 'network_request_changed', 'head_usb_port_required'}
            self.json(422, dict(error=str(error) if str(error) in codes else 'invalid_network_fields'))
