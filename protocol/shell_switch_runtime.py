"""Consume the console owner's magnetic events and produce role commands; no I/O."""
from copy import deepcopy

from protocol.character_switch import OBSERVATION_MS
from protocol.magnetic_stream import IDENTITY, sample_time, validate_stream
from protocol.magnetic_telemetry import decode_payload
from protocol.shell_profile_archive import restore_archive, validate_archive
from protocol.shell_switch_adapter import ShellSwitchAdapter


class ShellSwitchRuntime:
    def __init__(self, bindings, *, device_id, archive, context):
        self.archive = validate_archive(archive)
        self.context = deepcopy(context)
        self.recognizer = restore_archive(self.archive, self.context)
        self.adapter = ShellSwitchAdapter(bindings, device_id=device_id)
        self.identity = None
        self.sequence = self.last_sample_at = None
        self.motion_active = False
        self.reason = 'await_stream'
        self.gaps = self.discarded = 0
        self.fresh_after_ms = -1

    def _clock(self, now_ms):
        sample_time(now_ms)
        if now_ms < self.adapter.at:
            raise ValueError('运行时间应为递增的毫秒整数')

    @property
    def max_age_ms(self):
        return min(OBSERVATION_MS, self.recognizer.config.max_age_s*1000)

    def _expire(self, now_ms):
        # IPC delivery and repeated polling never renew the original sample age.
        if self.last_sample_at is not None and now_ms-self.last_sample_at > self.max_age_ms:
            self._reset(now_ms, 'sample_expired')

    def _reset(self, now_ms, reason):
        self.recognizer.reset_stream()
        self.last_sample_at = None
        self.adapter.invalidate_observation(now_ms)
        self.reason = reason

    def update(self, control, stream, now_ms):
        self._clock(now_ms)
        validate_stream(stream)
        self.adapter.update(control, now_ms)
        self._expire(now_ms)
        key = tuple(control[k] for k in IDENTITY) if control['enabled'] and control['session_id'] else None
        # A late, retired control snapshot must not reset the current recognition stream.
        if key != self.adapter.identity:
            self.discarded += 1
            return self.snapshot()
        if key != self.identity:
            self.identity = key
            self.sequence = None
            self._reset(now_ms, 'connection_changed')
        if not key or any(stream['identity'][k] != control[k] for k in IDENTITY):
            self._reset(now_ms, 'await_stream')
            return self.snapshot()
        if self.sequence is not None and stream['sequence'] < self.sequence:
            self.discarded += 1
            return self.snapshot()
        if self.sequence is None:
            self.sequence = 0
        if stream['reset_sequence'] > self.sequence:
            self.sequence = stream['reset_sequence']
            self._reset(now_ms, 'invalid_sample')
        for event in stream['events']:
            if event['sequence'] <= self.sequence:
                continue
            if event['sequence'] != self.sequence+1:
                self.gaps += 1
                self._reset(now_ms, 'sample_gap')
            self.sequence = event['sequence']
            if self.motion_active or event['at_ms'] <= self.fresh_after_ms:
                self.discarded += 1
                continue
            stamp, reference = sample_time(event['at_ms']), sample_time(now_ms)
            age = now_ms-event['at_ms']
            if age < 0 or age > self.max_age_ms:
                self.discarded += 1
                self._reset(now_ms, 'sample_expired')
                continue
            sample = decode_payload(bytes.fromhex(event['payload_hex']),host_received_at=stamp,
                                    host_time_source='console_owner_monotonic',as_of=reference)
            if sample.kind != self.context['sensor_kind']:
                self.discarded += 1
                self._reset(now_ms, 'sensor_changed')
                continue
            result = self.recognizer.feed(sample,as_of=reference)
            self.last_sample_at = event['at_ms']
            self.reason = result.reason
            # A batch can warm the recognizer; stability still advances only on fresh observations over time.
            self.adapter.observe(dict(identity=deepcopy(stream['identity']),sequence=self.sequence,
                archive_sha256=self.archive['payload_sha256'],observation=result.as_dict()),now_ms)
        if stream['sequence'] > self.sequence:
            self.gaps += 1
            self.sequence = stream['sequence']
            self._reset(now_ms, 'sample_gap')
        return self.snapshot()

    def motion(self, active, now_ms):
        self._clock(now_ms)
        self.adapter.motion(active, now_ms)
        if active != self.motion_active:
            self.motion_active = active
            self.fresh_after_ms = now_ms
            self._reset(now_ms, 'moving' if active else 'await_new_samples')
        self._expire(now_ms)
        return self.snapshot()

    def reset_segment(self, now_ms):
        self._clock(now_ms)
        self.fresh_after_ms = now_ms
        self._reset(now_ms, 'recording_boundary')
        return self.snapshot()

    def replace(self, bindings, *, archive, context, now_ms):
        self._clock(now_ms)
        archive = validate_archive(archive)
        recognizer = restore_archive(archive, context)
        self.adapter.replace_bindings(bindings,now_ms)
        self.archive, self.context, self.recognizer = archive, deepcopy(context), recognizer
        self.fresh_after_ms = now_ms
        self._reset(now_ms, 'archive_changed')
        return self.snapshot()

    def next_command(self, now_ms, *, allow_activation=True):
        self._clock(now_ms)
        self._expire(now_ms)
        return self.adapter.next_command(now_ms, allow_activation=allow_activation)

    def snapshot(self):
        return dict(schema='deskbot.shell_switch_runtime/v1',identity=self.identity,
            archive_sha256=self.archive['payload_sha256'],sequence=self.sequence,
            last_sample_at_ms=self.last_sample_at,reason=self.reason,gaps=self.gaps,discarded=self.discarded,
            recognition=self.recognizer.last_result.as_dict(),adapter=self.adapter.snapshot())
