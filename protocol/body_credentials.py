"""USB renewal codec. Secret bytes stay in the caller's local files."""
from copy import deepcopy
import hashlib
import re
import ssl
import struct

from protocol import studio_usb_session as session
from protocol.studio_runtime_query import RuntimeQueryClient

SCHEMA = 'deskbot.body_credentials/v1'
HEADER = struct.Struct('>4s16s32s32sHH')
MAX_CERTIFICATE = 1536
ID = re.compile('[a-f0-9]{32}')


def bundle_digest(payload):
    return hashlib.sha256(b'deskbot.body_credentials/v1\0' + payload).digest()[:16]


def encode_bundle(touch_token, expression_token, touch_cert, expression_cert):
    if (any(not re.fullmatch('[a-f0-9]{64}', token) for token in (touch_token, expression_token))
            or touch_token == expression_token):
        raise ValueError('invalid_credential_pair')
    certs = [ssl.PEM_cert_to_DER_cert(cert.strip()) for cert in (touch_cert, expression_cert)]
    if any(not 1 <= len(cert) <= MAX_CERTIFICATE for cert in certs):
        raise ValueError('credential_certificate_size')
    payload = bytes.fromhex(touch_token + expression_token) + struct.pack('>HH', *map(len, certs)) + b''.join(certs)
    return b'DBK1' + bundle_digest(payload) + payload


def bundle_id(raw):
    if not isinstance(raw, bytes) or not HEADER.size + 2 <= len(raw) <= HEADER.size + 2 * MAX_CERTIFICATE:
        raise ValueError('invalid_credential_bundle')
    magic, identifier, touch, expression, tsize, esize = HEADER.unpack(raw[:HEADER.size])
    if (magic != b'DBK1' or identifier != bundle_digest(raw[20:]) or touch == expression
            or any(not 1 <= size <= MAX_CERTIFICATE for size in (tsize, esize))
            or len(raw) != HEADER.size + tsize + esize
            or raw[HEADER.size] != 0x30 or raw[HEADER.size + tsize] != 0x30):
        raise ValueError('invalid_credential_bundle')
    return identifier.hex()


def upload(raw, expected):
    bundle_id(raw)
    if not ID.fullmatch(expected):
        raise ValueError('invalid_active_credential')
    yield f'key-begin:{len(raw)}:{expected}'
    for offset in range(0, len(raw), 32):
        yield f'key-data:{offset}:{raw[offset:offset + 32].hex()}'


def valid_operation(op):
    if op in ('key-get', 'key-test', 'key-check'):
        return True
    if type(op) is not str or len(op) > 96:
        return False
    match = re.fullmatch(r'key-begin:([1-9][0-9]{0,3}):[a-f0-9]{32}', op)
    if match:
        return HEADER.size + 2 <= int(match[1]) <= HEADER.size + 2 * MAX_CERTIFICATE
    match = re.fullmatch(r'key-data:(0|[1-9][0-9]{0,3}):([a-f0-9]{2,80})', op)
    if match:
        return int(match[1]) < HEADER.size + 2 * MAX_CERTIFICATE and len(match[2]) % 2 == 0
    return bool(re.fullmatch(r'key-save:[a-f0-9]{32}', op))


def validate_reply(value):
    common = {'schema', 'message_type', 'request_id', 'target_id', 'boot_id', 'session_id',
              'sequence', 'at_ms', 'lease_ms'}
    if (type(value) is not dict or set(value) != common | {'result', 'storage', 'active', 'candidate', 'trial'}
            or value['schema'] != SCHEMA or value['message_type'] != 'credentials'
            or value['result'] not in ('state', 'staging', 'staged', 'testing', 'saved', 'unchanged',
                                      'invalid', 'conflict', 'no_memory', 'not_verified', 'save_failed', 'save_uncertain')
            or value['storage'] not in ('builtin', 'restored', 'saved', 'write_uncertain', 'read_failed', 'invalid_record')
            or not isinstance(value['active'], str)
            or not ID.fullmatch(value['active'])
            or not isinstance(value['candidate'], str)
            or value['candidate'] and not ID.fullmatch(value['candidate'])
            or value['trial'] not in ('none', 'queued', 'running', 'passed', 'failed')):
        raise ValueError('invalid_body_credentials_reply')
    envelope = {key: value[key] for key in common - {'schema', 'message_type'}}
    session.validate_reply(dict(envelope, schema=session.SCHEMA, message_type='session', state='ready',
                                reason='credentials_read', controls_available=False))
    return deepcopy(value)


class Client(RuntimeQueryClient):
    QUERY_OPERATION = 'key-get'
    REPLY_SCHEMA = SCHEMA
    validate_state_reply = staticmethod(validate_reply)

    def request(self, op, now):
        if op != 'open':
            if not valid_operation(op):
                raise ValueError('invalid_credential_operation')
            self.QUERY_OPERATION = op
        return super().request(op, now)
