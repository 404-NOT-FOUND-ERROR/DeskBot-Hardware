"""Bounded magnetic events from the existing console owner; no I/O."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

from protocol.body_console_observation import ANSI
from protocol.magnetic_telemetry import callback_payload_from_line, decode_payload
from protocol.studio_timing import monotonic_ms
from protocol import studio_usb_session as session

SCHEMA = 'deskbot.magnetic_stream/v1'
IDENTITY = ('connection_id', 'target_id', 'boot_id', 'session_id')
LIMIT = 64
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def sample_time(at_ms):
    # M2 needs timezone-aware timestamps. Both sample and freshness clocks use this epoch.
    if not monotonic_ms(at_ms):
        raise ValueError('磁场事件时间无效')
    try:
        return EPOCH + timedelta(milliseconds=at_ms)
    except OverflowError as exc:
        raise ValueError('磁场事件时间超出可识别范围') from exc


def validate_identity(value):
    patterns = (re.compile(r'connection-[a-f0-9]{32}\Z'), session.DEVICE, session.BOOT, session.SESSION)
    if (type(value) is not dict or set(value) != set(IDENTITY)
            or any(v is not None and (type(v) is not str or not pattern.fullmatch(v))
                   for v, pattern in zip((value[k] for k in IDENTITY), patterns))):
        raise ValueError('磁场事件连接身份无效')
    return value


def validate_stream(value):
    if (type(value) is not dict or set(value) != {'schema','identity','sequence','reset_sequence','rejected','events'}
            or value['schema'] != SCHEMA or type(value['events']) is not list or len(value['events']) > LIMIT
            or any(not monotonic_ms(value[k]) for k in ('sequence','reset_sequence','rejected'))
            or value['reset_sequence'] > value['sequence']):
        raise ValueError('磁场事件流格式无效')
    validate_identity(value['identity'])
    previous = value['reset_sequence']
    previous_time = -1
    for row in value['events']:
        if (type(row) is not dict or set(row) != {'sequence','at_ms','payload_hex'}
                or not monotonic_ms(row['sequence']) or not previous < row['sequence'] <= value['sequence']
                or not monotonic_ms(row['at_ms']) or row['at_ms'] < previous_time
                or type(row['payload_hex']) is not str or not re.fullmatch(r'06[0-9A-F]{14}',row['payload_hex'])):
            raise ValueError('磁场事件格式或顺序无效')
        sample = decode_payload(bytes.fromhex(row['payload_hex']))
        sample_time(row['at_ms'])
        if not sample.xyz_available:
            raise ValueError('磁场事件需要三轴传感器')
        previous, previous_time = row['sequence'], row['at_ms']
    return value


class MagneticStream:
    def __init__(self):
        self.identity = {k:None for k in IDENTITY}
        self.sequence = self.reset_sequence = self.rejected = 0
        self.events = deque(maxlen=LIMIT)

    def sync(self, control):
        identity = {k:control.get(k) for k in IDENTITY}
        validate_identity(identity)
        if identity != self.identity:
            self.identity = identity
            self.events.clear()
            self.sequence = self.reset_sequence = self.rejected = 0

    def line(self, raw, control, at_ms):
        self.sync(control)
        if not monotonic_ms(at_ms):
            raise ValueError('磁场事件时间无效')
        line = ANSI.sub(b'', raw).decode('utf-8',errors='replace').rstrip('\r\n')
        try:
            payload = callback_payload_from_line(line)
            if payload is None:
                return
            sample = decode_payload(payload)
            if not sample.xyz_available:
                raise ValueError('磁场事件需要三轴传感器')
        except ValueError:
            self.sequence += 1
            self.reset_sequence = self.sequence
            self.rejected += 1
            self.events.clear()
            return
        self.sequence += 1
        self.events.append(dict(sequence=self.sequence,at_ms=at_ms,payload_hex=payload.hex().upper()))

    def snapshot(self, control):
        self.sync(control)
        return dict(schema=SCHEMA,identity=deepcopy(self.identity),sequence=self.sequence,
                    reset_sequence=self.reset_sequence,rejected=self.rejected,events=list(deepcopy(self.events)))
