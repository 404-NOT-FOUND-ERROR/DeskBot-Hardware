"""Body service address settings over the established, caller-owned USB session."""
from copy import deepcopy
import ipaddress
import re

from protocol import studio_usb_session as session
from protocol.studio_runtime_query import RuntimeQueryClient

SCHEMA = 'deskbot.body_network/v1'


def valid_host(host):
    if type(host) is not str or not 1 <= len(host) <= 63:
        return False
    if re.fullmatch(r'[0-9.]+', host):
        try:
            address = ipaddress.IPv4Address(host)
            return any(address in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        except ValueError:
            return False
    return all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))


def operation(kind='get', *, expected=None, host=None, touch_port=4318, expression_port=4319):
    if kind == 'get': return 'net-get'
    if type(expected) is not int or not 0 <= expected <= 0xffffffff:
        raise ValueError('invalid_network_revision')
    if kind == 'reset': return f'net-reset:{expected}'
    if (kind != 'set' or not valid_host(host)
            or any(type(p) is not int or not 1 <= p <= 65535 for p in (touch_port, expression_port))
            or touch_port == expression_port):
        raise ValueError('invalid_network_address')
    return f'net-set:{expected}:{host}:{touch_port}:{expression_port}'


def valid_operation(text):
    if type(text) is not str: return False
    try:
        parts = text.split(':')
        if parts == ['net-get']: return True
        if len(parts) == 2 and parts[0] == 'net-reset':
            return text == operation('reset', expected=int(parts[1]))
        if len(parts) == 5 and parts[0] == 'net-set':
            return text == operation('set', expected=int(parts[1]), host=parts[2],
                touch_port=int(parts[3]), expression_port=int(parts[4]))
    except ValueError:
        pass
    return False


def validate_reply(value):
    fields = {'schema', 'message_type', 'request_id', 'target_id', 'boot_id', 'session_id', 'sequence',
        'at_ms', 'lease_ms', 'result', 'storage', 'host', 'touch_port', 'expression_port', 'revision', 'source', 'applied_revision'}
    if (type(value) is not dict or set(value) != fields or value['schema'] != SCHEMA
            or value['message_type'] != 'network'
            or value['result'] not in ('state', 'saved', 'unchanged', 'conflict', 'invalid', 'save_failed', 'save_uncertain', 'revision_exhausted')
            or value['storage'] not in ('defaults', 'restored', 'saved', 'invalid_record', 'read_failed', 'write_uncertain')
            or value['source'] not in ('saved', 'defaults') or type(value['revision']) is not int
            or not 0 <= value['revision'] <= 0xffffffff
            or value['applied_revision'] is not None and (type(value['applied_revision']) is not int
                or not 0 <= value['applied_revision'] <= value['revision'])):
        raise ValueError('invalid_body_network_reply')
    operation('set', expected=value['revision'], host=value['host'],
        touch_port=value['touch_port'], expression_port=value['expression_port'])
    common = {key: value[key] for key in ('request_id', 'target_id', 'boot_id', 'session_id', 'sequence', 'at_ms', 'lease_ms')}
    session.validate_reply(dict(common, schema=session.SCHEMA, message_type='session', state='ready',
        reason='network_settings_read', controls_available=False))
    return deepcopy(value)


class Client(RuntimeQueryClient):
    QUERY_OPERATION = 'net-get'
    REPLY_SCHEMA = SCHEMA
    validate_state_reply = staticmethod(validate_reply)

    def request(self, op, now):
        if op != 'open':
            if not valid_operation(op): raise ValueError('invalid_network_operation')
            self.QUERY_OPERATION = op
        return super().request(op, now)

    def accept(self, value, now):
        if value.get('schema') == SCHEMA and self._pending:
            read = self._pending['operation'] == 'net-get'
            if read != (value.get('result') == 'state'):
                raise ValueError('network_operation_mismatch')
        return super().accept(value, now)
