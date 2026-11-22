"""Original client HTTP outbox to the existing presentation/USB owner."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from protocol.client_presentation_ledger import canonical
from protocol.presentation_coordinator import PresentationError
from protocol.studio_control_link import expression_request_id
from protocol.studio_display_query import supports
from protocol import studio_timing as timing

SEMANTICS = frozenset(('neutral', 'happy'))
IDENTITY = ('connection_id', 'target_id', 'boot_id', 'session_id')
HANDOFF_BUDGET_SECONDS = (timing.IPC_TIMEOUT_MS + timing.REQUEST_TIMEOUT_MS + timing.ACCEPTED_TIMEOUT_MS) / 1000


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid_command_time')
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None or not math.isfinite(at.timestamp()):
        raise ValueError('invalid_command_time')
    return at.timestamp()


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class ClientHTTPError(OSError):
    def __init__(self, status, body):
        super().__init__('client_http_' + str(status))
        self.status, self.body = status, body


class ClientHTTP:
    def __init__(self, base_url, timeout=5):
        url = urlsplit(base_url)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost')
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('local_client_url_required')
        self.base_url, self.timeout = base_url.rstrip('/'), timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, value=None):
        data = None if value is None else canonical(value).encode('utf-8')
        request = Request(self.base_url + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        except HTTPError as error:
            try:
                body = json.loads(error.read(65536))
            except (ValueError, OSError):
                body = {}
            raise ClientHTTPError(error.code, body) from None
        except URLError as error:
            raise OSError('client_unavailable') from error


class ClientPresentationService:
    def __init__(self, bridge, http, ledger, *, device_id, character_id='shaping-001',
                 client_stage='0d8d4c5+4b1a', presentation_revision='E3f-9d.3', wall=time.time):
        self.bridge, self.http, self.ledger = bridge, http, ledger
        self.device_id, self.character_id = device_id, character_id
        self.client_stage, self.presentation_revision, self.wall = client_stage, presentation_revision, wall
        self.lock = threading.Lock()
        self.error = None
        self.coordinator = bridge.presentation
        self.coordinator.attach(ledger)
        self.coordinator.observers.append(self.observe)

    def chat(self, event_id, correlation_id, message):
        if any(not isinstance(x, str) or not x.strip() for x in (event_id, correlation_id, message)):
            raise ValueError('chat_fields_required')
        if len(event_id) > 128 or len(correlation_id) > 128 or len(message) > 8000:
            raise ValueError('chat_too_large')
        with self.lock:
            row = self.ledger.get('input', event_id)
            if row:
                if row['message'] != message or row['correlation_id'] != correlation_id:
                    raise ValueError('event_id_conflict')
            else:
                with self.bridge.lock:
                    binding = self.coordinator.snapshot()
                    if binding['device_id'] != self.device_id:
                        raise PresentationError('device_mismatch')
                    binding.update(character_id=self.character_id, client_stage=self.client_stage,
                                   presentation_revision=self.presentation_revision)
                    revision = hashlib.sha256(canonical(binding).encode()).hexdigest()
                    row = self.ledger.create('input', event_id, dict(event_id=event_id,
                        correlation_id=correlation_id, message=message, binding=binding, role_revision=revision,
                        body=dict(event_id=event_id, correlation_id=correlation_id, message=message,
                                  character_id=self.character_id, device_id=self.device_id, role_revision=revision)))
            if 'response' not in row:
                row['response'] = self.http.request('POST', '/api/chat', row['body'])
                self.ledger.put('input', event_id, row)
            return deepcopy(row['response'])

    def _finish(self, row, code=None, receipt=None):
        if row['phase'] in ('terminal', 'acknowledged'):
            return
        command = row['command']
        result = dict(source_event_id=command['source_event_id'], role_revision=command.get('role_revision'),
                      binding=row.get('binding'), usb=row.get('usb'), receipt=receipt)
        if code is None:
            result['completion_scope'] = 'player_dispatched'
        row.update(phase='terminal', ack=dict(command_id=command['command_id'],
            status='failed' if code else 'completed', device_id=command['device_id'], target='vocat',
            source='deskbot-client-presentation', occurred_at=iso(self.wall()),
            correlation_id=command.get('correlation_id'), result=result,
            **({'error': dict(code=code)} if code else {})))
        self.ledger.put('command', command['command_id'], row)

    def observe(self, control):
        # Bridge lock -> ledger lock is the only nested lock order. Receipts
        # are captured here even if a later manual command replaces last_command.
        for row in self.ledger.all('command'):
            if row['phase'] != 'prepared':
                continue
            usb = row['usb']
            for receipt in control.get('receipts', []):
                if (receipt.get('request_id') != usb['request_id'] or receipt.get('source') != 'device'
                        or receipt.get('correlated') is not True
                        or any(receipt.get(k) != usb[k] for k in ('target_id', 'boot_id', 'session_id'))):
                    continue
                status = receipt.get('original_status') if receipt.get('status') == 'duplicate' else receipt.get('status')
                if status == 'accepted':
                    continue
                self._finish(row, None if status == 'dispatched' else 'usb_' + str(status), receipt)
                break

    def _claim(self, command):
        key = command['command_id']
        existing = self.ledger.get('command', key)
        if existing:
            if canonical(existing['command']) != canonical(command):
                raise ValueError('command_id_conflict')
            return
        event_id = command['source_event_id'].removeprefix('reply-')
        source = self.ledger.get('input', event_id) if command['source_event_id'].startswith('reply-') else None
        now = self.wall()
        deadline = timestamp(command['queued_at']) + 30
        if command.get('expires_at'):
            deadline = min(deadline, timestamp(command['expires_at']))
        row = dict(command=command, deadline=deadline, claimed_at=now, phase='claimed',
                   binding=source['binding'] if source else None)
        self.ledger.create('command', key, row)
        if (not source or command.get('role_revision') != source['role_revision']
                or command.get('character_id') != self.character_id):
            self._finish(row, 'missing_binding')
        elif command.get('payload', {}).get('expression') not in SEMANTICS:
            self._finish(row, 'unsupported_expression')
        elif timestamp(command['queued_at']) > now + 5:
            self._finish(row, 'clock_invalid')

    def _advance(self, key):
        with self.bridge.lock:
            row = self.ledger.get('command', key)
            if row['phase'] in ('terminal', 'acknowledged'):
                return
            now = self.wall()
            control = self.bridge.control()
            self.coordinator.observe(control)
            row = self.ledger.get('command', key)
            if row['phase'] == 'terminal':
                return
            # Every claimed item is revalidated after reopening, including a
            # crash immediately after create() but before the first check.
            binding, command = row.get('binding'), row['command']
            if (not isinstance(binding, dict)
                    or command.get('role_revision') != hashlib.sha256(canonical(binding).encode()).hexdigest()
                    or command.get('character_id') != binding.get('character_id')
                    or command.get('device_id') != binding.get('device_id')):
                return self._finish(row, 'missing_binding')
            if command.get('payload', {}).get('expression') not in SEMANTICS:
                return self._finish(row, 'unsupported_expression')
            if timestamp(command['queued_at']) > row['claimed_at'] + 5:
                return self._finish(row, 'clock_invalid')
            if row['phase'] == 'prepared':
                last = control.get('last_command') or {}
                local = control.get('local_result') or {}
                if last.get('command_id') == row['usb']['command_id'] and last.get('status') == 'rejected':
                    self._finish(row, 'usb_rejected')
                elif local.get('request_id') == row['usb']['request_id'] and local.get('outcome') == 'cancelled':
                    self._finish(row, 'usb_cancelled')
                elif (row['owner_epoch'] != self.coordinator.owner_epoch
                        or now < row['prepared_at'] - 5
                        or now - row['prepared_at'] >= HANDOFF_BUDGET_SECONDS):
                    self._finish(row, 'delivery_unknown')
                return
            if now >= row['deadline']:
                return self._finish(row, 'expired')
            if now < row['claimed_at'] - 5:
                return self._finish(row, 'clock_invalid')
            if row['binding'].get('device_id') != self.device_id:
                return self._finish(row, 'device_mismatch')
            if (row['binding'].get('client_stage') != self.client_stage
                    or row['binding'].get('presentation_revision') != self.presentation_revision
                    or row['binding'].get('character_id') != self.character_id):
                return self._finish(row, 'superseded')
            try:
                self.coordinator.validate(row['binding'])
            except PresentationError as error:
                if str(error) == 'superseded':
                    self._finish(row, 'superseded')
                return
            if (not control.get('can_submit') or control.get('host_command') or control.get('pending')
                    or (control.get('roles') or {}).get('pending')):
                return
            role_state = (control.get('roles') or {}).get('reply') or {}
            if role_state.get('sleep') == 'sleeping' or role_state.get('blocked'):
                return
            semantic = row['command']['payload']['expression']
            if not supports(control.get('display'), semantic):
                return self._finish(row, 'unsupported_expression')
            value = dict(operation=semantic, command_id='client-' + hashlib.sha256(key.encode()).hexdigest()[:32],
                         **{k: control[k] for k in IDENTITY})

            def prepare():
                row.update(phase='prepared', prepared_at=now, owner_epoch=self.coordinator.owner_epoch,
                           usb=dict(value, request_id=expression_request_id(value)))
                self.ledger.put('command', key, row)

            try:
                self.coordinator.submit(value, expected=row['binding'], prepare=prepare)
            except (ValueError, OSError) as error:
                # Preparation precedes I/O; a transport exception cannot prove
                # that zero bytes reached the worker. This item is never resent.
                current = self.ledger.get('command', key)
                if current['phase'] == 'prepared':
                    current['transport_error'] = str(error)
                    self.ledger.put('command', key, current)

    def _resume_inputs(self):
        for row in self.ledger.all('input'):
            if 'response' in row or 'request_error' in row:
                continue
            try:
                row['response'] = self.http.request('POST', '/api/chat', row['body'])
            except ClientHTTPError as error:
                if error.status >= 500:
                    raise
                row['request_error'] = dict(status=error.status, body=error.body)
            self.ledger.put('input', row['event_id'], row)

    def _acknowledge(self, row):
        if row['phase'] != 'terminal':
            return
        try:
            response = self.http.request('POST', '/api/outbox/' + quote(row['command']['command_id'], safe='') + '/ack', row['ack'])
        except ClientHTTPError as error:
            if error.status != 409:
                raise
            # Preserve both outcomes when the original queue already expired.
            response = self.http.request('GET', '/api/outbox/' + quote(row['command']['command_id'], safe=''))
            if (response.get('command') or {}).get('status') not in ('completed', 'failed'):
                raise
            row['ack_conflict'] = error.body
        row.update(phase='acknowledged', server_response=response)
        self.ledger.put('command', row['command']['command_id'], row)

    def tick(self):
        with self.lock:
            try:
                # Reconcile durable receipts before GET's built-in queue expiry.
                for row in self.ledger.all('command'):
                    self._advance(row['command']['command_id'])
                    self._acknowledge(self.ledger.get('command', row['command']['command_id']))
                self._resume_inputs()
                try:
                    facts = self.coordinator.snapshot()
                except PresentationError:
                    facts = None
                if facts and facts['device_id'] == self.device_id:
                    self.http.request('POST', '/api/devices/hello', dict(device_id=self.device_id,
                        hardware='esp32s3-esp_vocat', firmware=self.presentation_revision,
                        character_id=self.character_id, capabilities={'display.expression': True}))
                response = self.http.request('GET', '/api/outbox?' + urlencode(dict(target='vocat', device_id=self.device_id,
                    type='render.expression', source_event_prefix='reply-')))
                for command in response['commands']:
                    if (command.get('target') == 'vocat' and command.get('device_id') == self.device_id
                            and command.get('type') == 'render.expression'
                            and not str(command.get('source_event_id', '')).startswith('body-perception:')):
                        self._claim(command)
                        self._advance(command['command_id'])
                        self._acknowledge(self.ledger.get('command', command['command_id']))
                self.error = None
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.error = str(error)
            return self.state()

    def state(self):
        return dict(schema='deskbot.client_presentation/v1', device_id=self.device_id,
                    character_id=self.character_id, error=self.error,
                    inputs=[dict(event_id=row['event_id'], status='completed' if 'response' in row else
                                 (row.get('request_error', {}).get('body', {}).get('error') or 'pending'))
                            for row in self.ledger.all('input')],
                    commands=self.ledger.all('command'))

    def close(self):
        with self.bridge.lock:
            self.coordinator.observers.remove(self.observe)
            self.coordinator.detach(self.ledger)
