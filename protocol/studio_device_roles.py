"""Installed device-role facts, carried over the existing single USB session."""
from copy import deepcopy
import hashlib
import re

from protocol import studio_usb_session as session
from protocol.studio_catalog_query import CatalogQueryClient

SCHEMA = 'deskbot.studio_roles/v1'
VALID_MS = 3000
ACTIVATE = re.compile(r'role\.activate\.([0-3])\.([0-9a-f]{64})\Z', re.ASCII)


def operation(value):
    return isinstance(value, str) and (value == 'read_roles' or ACTIVATE.fullmatch(value) is not None)


def initial(status='idle', error=None):
    return dict(status=status, reply=None, age_ms=None, pending=None, error=error)


def validate_reply(value):
    keys = {'schema', 'message_type', 'request_id', 'target_id', 'boot_id', 'session_id',
            'sequence', 'at_ms', 'lease_ms', 'result', 'reason', 'loaded', 'graph_sha256', 'roles',
            'default_index', 'target_index', 'current_index', 'active_index', 'phase', 'blocked',
            'sleep', 'saved_index', 'save_state', 'activation'}
    if (not isinstance(value, dict) or set(value) != keys or value['schema'] != SCHEMA
            or value['message_type'] != 'roles' or type(value['loaded']) is not bool
            or value['result'] not in ('state', 'accepted', 'duplicate', 'rejected')
            or not session._id(value['reason']) or value['lease_ms'] != session.LEASE_MS):
        raise ValueError('invalid_role_reply')
    common = {key: value[key] for key in ('request_id', 'target_id', 'boot_id', 'session_id', 'sequence', 'at_ms', 'lease_ms')}
    session.validate_reply(dict(common, schema=session.SCHEMA, message_type='session', state='ready',
                                reason='roles_read', controls_available=False))
    roles = value['roles']
    if (not isinstance(roles, list) or len(roles) > 4 or len(set(roles)) != len(roles)
            or any(not session._id(x) or len(x) > 64 for x in roles)
            or value['loaded'] != bool(roles)
            or not isinstance(value['graph_sha256'], str)
            or (re.fullmatch('[0-9a-f]{64}', value['graph_sha256']) is None if roles else value['graph_sha256'] != '')):
        raise ValueError('invalid_role_directory')
    for key in ('default_index', 'target_index', 'current_index', 'active_index', 'saved_index'):
        if type(value[key]) is not int or not -1 <= value[key] < len(roles):
            raise ValueError('invalid_role_index')
    if (roles and min(value['default_index'], value['target_index']) < 0
            or value['phase'] not in ('none', 'single', 'entry', 'loop', 'exit', 'held')
            or type(value['blocked']) is not int or not 0 <= value['blocked'] <= 7
            or value['sleep'] not in ('unknown', 'awake', 'sleeping')
            or value['save_state'] not in ('none', 'restored', 'pending', 'saved', 'failed')):
        raise ValueError('invalid_role_state')
    attempt = value['activation']
    if (not isinstance(attempt, dict) or set(attempt) != {'request_id', 'status'}
            or attempt['status'] not in ('idle', 'queued', 'switching', 'applied', 'cancelled', 'failed')
            or not (attempt['request_id'] == '' or session._id(attempt['request_id']))
            or (attempt['status'] == 'idle') != (attempt['request_id'] == '')):
        raise ValueError('invalid_role_activation')
    return deepcopy(value)


def validate_snapshot(value, identity):
    if (not isinstance(value, dict) or set(value) != {'status', 'reply', 'age_ms', 'pending', 'error'}
            or value['status'] not in ('idle', 'querying', 'current', 'stale', 'disconnected', 'failed')
            or value['age_ms'] is not None and (type(value['age_ms']) is not int or value['age_ms'] < 0)
            or value['pending'] is not None and not operation(value['pending'])
            or value['error'] is not None and not session._id(value['error'])):
        raise ValueError('invalid_roles_snapshot')
    if value['reply'] is not None:
        reply = validate_reply(value['reply'])
        if (any(reply[k] != identity[k] for k in ('target_id', 'boot_id', 'session_id'))
                or value['age_ms'] is None or value['status'] != 'current' or value['age_ms'] >= VALID_MS):
            raise ValueError('stale_roles_snapshot')
    elif value['status'] == 'current':
        raise ValueError('missing_roles_reply')
    return deepcopy(value)


class RoleQueryClient(CatalogQueryClient):
    def request_roles(self, operation, now):
        self._tick(now)
        if (operation != 'role.query' and ACTIVATE.fullmatch(operation) is None
                or not self.connected or not self.session_id or self._pending
                or self._last_request is not None and now - self._last_request < session.REPLY_INTERVAL_MS
                or self._counter == 0xffffffff):
            raise ValueError('role_request_not_ready')
        self._counter += 1
        token = hashlib.sha256(self.nonce.encode('ascii')).hexdigest()[:24]
        request_id = f'rq-{token}-{self._counter}'
        wire = session.encode_request(operation=operation, request_id=request_id, target_id=self.target_id,
            boot_id=self.boot_id, client_nonce=self.nonce, session_id=self.session_id)
        self._pending = dict(operation=operation, request_id=request_id, sent=now, session_id=self.session_id)
        self._last_request = now
        return wire

    def accept_roles(self, value, now):
        self._tick(now)
        pending = self._pending
        if not self.connected or not pending or not pending['operation'].startswith('role.'):
            raise ValueError('unsolicited_role_reply')
        row = validate_reply(value)
        if row['target_id'] != self.target_id or row['boot_id'] != self.boot_id:
            self.disconnect()
            raise ValueError('role_identity_changed')
        if (row['request_id'] != pending['request_id'] or row['session_id'] != self.session_id
                or row['session_id'] != pending['session_id'] or row['sequence'] <= self._sequence):
            raise ValueError('role_correlation_failed')
        if self._device_time is not None and row['at_ms'] < self._device_time:
            self.disconnect()
            raise ValueError('role_clock_moved_backwards')
        if pending['operation'] == 'role.query' and row['result'] != 'state':
            raise ValueError('role_operation_mismatch')
        if pending['operation'] != 'role.query' and row['result'] == 'state':
            raise ValueError('role_operation_mismatch')
        self._sequence, self._device_time = row['sequence'], row['at_ms']
        self._lease = pending['sent'] + session.LEASE_MS
        self._pending = None
        return row
