"""Evaluate explicitly labelled, independently mounted shell recordings."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from protocol.magnetic_recognition import MagneticRecognizer, RecognitionConfig
from protocol.magnetic_telemetry import callback_payload_from_line, decode_payload

SUITE_SCHEMA = 'deskbot.magnetic.repeatability_suite/v1'
REPORT_SCHEMA = 'deskbot.magnetic.repeatability_report/v1'
MAX_MANIFEST_BYTES = 256 * 1024
MAX_RECORDINGS = 100
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_TOTAL_SAMPLES = 100_000
TARGET_MOUNTS = 10
IDENTIFIER = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z', re.ASCII)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid recording, capture, mount or profile identifier')
    return value


def _path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('input path must be project-relative')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('input path escapes project')
    return path


def _time(value):
    when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if when.tzinfo is None:
        raise ValueError('sample timestamp requires timezone')
    return when.astimezone(timezone.utc)


def _read_samples(raw):
    samples = []
    previous = None
    for number, line in enumerate(raw.decode('utf-8', errors='strict').splitlines(), 1):
        stamp, separator, payload = line.partition('\t')
        if not separator:
            raise ValueError(f'line {number}: timestamp separator required')
        when = _time(stamp)
        callback = callback_payload_from_line(payload)
        if callback is None:
            continue
        sample = decode_payload(callback, host_received_at=when,
                                host_time_source='capture_log_prefix')
        if not sample.xyz_available:
            raise ValueError('XYZ sensor required')
        if previous is not None and when <= previous:
            raise ValueError('sample timestamps must increase')
        previous = when
        samples.append(sample)
        if len(samples) > MAX_TOTAL_SAMPLES:
            raise ValueError('sample limit exceeded')
    if not samples:
        raise ValueError('magnetic samples required')
    if len({sample.kind for sample in samples}) != 1:
        raise ValueError('one sensor kind required per recording')
    return samples


def _load_suite(path, root):
    root = root.resolve(strict=True)
    path = path.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('suite path escapes project')
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('suite byte limit exceeded')
    raw = path.read_bytes()
    suite = json.loads(raw)
    if (not isinstance(suite, dict) or set(suite) != {'schema', 'title', 'config', 'recordings'}
            or suite['schema'] != SUITE_SCHEMA):
        raise ValueError('invalid repeatability suite')
    if not isinstance(suite['title'], str) or not 1 <= len(suite['title']) <= 160:
        raise ValueError('suite title required')
    if not isinstance(suite['config'], dict):
        raise ValueError('recognition config must be an object')
    config = RecognitionConfig(**suite['config'])
    rows = suite['recordings']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RECORDINGS:
        raise ValueError('recording limit exceeded')
    ids, recordings = set(), []
    bytes_read = sample_count = 0
    for row in rows:
        common = {'id', 'capture_id', 'mount_id', 'log', 'sha256', 'use'}
        if not isinstance(row, dict) or row.get('use') not in ('train', 'evaluate', 'observe'):
            raise ValueError('invalid recording use')
        required = common | ({'profile_id'} if row['use'] == 'train' else
                             {'expected_profile_id', 'scenario'} if row['use'] == 'evaluate' else set())
        if set(row) != required:
            raise ValueError('recording fields do not match use')
        for field in ('id', 'capture_id', 'mount_id'):
            _identifier(row[field])
        if row['id'] in ids:
            raise ValueError('recording identifiers must be unique')
        ids.add(row['id'])
        for field in ('profile_id', 'expected_profile_id'):
            if field in row and row[field] is not None:
                _identifier(row[field])
        if row['use'] == 'train' and row['profile_id'] is None:
            raise ValueError('training profile identifier required')
        if row['use'] == 'evaluate' and (row['scenario'] not in ('remount', 'removed', 'unknown_layout')
                or (row['scenario'] == 'remount') != (row['expected_profile_id'] is not None)):
            raise ValueError('scenario and expected profile do not agree')
        log = _path(root, row['log'])
        size = log.stat().st_size
        if size > MAX_LOG_BYTES or bytes_read + size > MAX_TOTAL_BYTES:
            raise ValueError('recording byte limit exceeded')
        contents = log.read_bytes()
        if sha(contents) != row['sha256']:
            raise ValueError('recording digest changed')
        samples = _read_samples(contents)
        sample_count += len(samples)
        bytes_read += size
        if sample_count > MAX_TOTAL_SAMPLES:
            raise ValueError('suite sample limit exceeded')
        canonical = [[sample.kind, _time(sample.host_received_at).isoformat(),
                      sample.x, sample.y, sample.z] for sample in samples]
        recordings.append(dict(row, samples=samples, sample_keys={tuple(item) for item in canonical},
            start=_time(samples[0].host_received_at),
            end=_time(samples[-1].host_received_at),
            samples_sha256=sha(json.dumps(canonical, separators=(',', ':')).encode())))
    if len({r['samples'][0].kind for r in recordings}) != 1:
        raise ValueError('suite requires one sensor kind')
    trains = [r for r in recordings if r['use'] == 'train']
    if not 1 <= len(trains) <= config.max_profiles:
        raise ValueError('one training recording per registered profile required')
    profiles = {r['profile_id'] for r in trains}
    if len(profiles) != len(trains):
        raise ValueError('one training recording per registered profile required')
    expectations = {}
    for r in recordings:
        # A physical installation keeps its identity across capture sessions.
        key = r['mount_id']
        if r['use'] == 'observe':
            continue
        expected = r.get('profile_id', r.get('expected_profile_id'))
        identity = (r['use'] == 'train', expected, r.get('scenario'))
        if key in expectations and expectations[key] != identity:
            raise ValueError('same physical mount has conflicting use or identity')
        expectations[key] = identity
        if r['use'] == 'evaluate' and expected is not None and expected not in profiles:
            raise ValueError('evaluation refers to an unregistered profile')
    # An acquisition can contain several remounts. Independent windows may
    # share its log, but never share time or samples with another scored row.
    scored = [r for r in recordings if r['use'] != 'observe']
    for i, left in enumerate(scored):
        for right in scored[i + 1:]:
            if (left['samples_sha256'] == right['samples_sha256']
                    or left['sha256'] == right['sha256']
                    or not left['sample_keys'].isdisjoint(right['sample_keys'])):
                raise ValueError('scored recordings reuse the same samples')
            if (left['capture_id'] == right['capture_id']
                    and max(left['start'], right['start']) <= min(left['end'], right['end'])):
                raise ValueError('scored recording times overlap within acquisition')
    return suite, config, recordings, sha(raw), path.relative_to(root).as_posix()


def evaluate_suite(path, root, *, saved_profiles=None):
    """Return replay measurements; physical labels come from the input suite."""
    suite, config, recordings, suite_sha, source = _load_suite(Path(path), Path(root))
    recognizer = (MagneticRecognizer(config) if saved_profiles is None else
                  MagneticRecognizer.from_profiles(saved_profiles, config=config))
    saved = {p.profile_id: p for p in recognizer.profiles}
    if saved_profiles is not None and set(saved) != {
            r['profile_id'] for r in recordings if r['use'] == 'train'}:
        raise ValueError('saved profile identifiers differ from suite')
    training = []
    for r in recordings:
        if r['use'] != 'train':
            continue
        if saved_profiles is not None:
            profile = saved[r['profile_id']]
            if profile.kind != r['samples'][0].kind:
                raise ValueError('saved profile sensor differs from suite')
            training.append(dict(recording_id=r['id'], profile=profile.as_dict()))
            continue
        recognizer.reset_stream()
        samples = r['samples']
        if any((_time(b.host_received_at) - _time(a.host_received_at)).total_seconds()
               > config.max_gap_s for a, b in zip(samples, samples[1:])):
            raise ValueError('training recording has a sampling gap')
        for sample in samples:
            recognizer.feed(sample)
        profile = recognizer.register(r['profile_id'], profile_id=r['profile_id'],
                                      as_of=samples[-1].host_received_at)
        training.append(dict(recording_id=r['id'], profile=profile.as_dict()))
    windows = []
    mounts = {}
    for r in recordings:
        if r['use'] == 'train':
            continue
        recognizer.reset_stream()
        counts = Counter()
        known_counts = Counter()
        first_known_s = None
        for sample in r['samples']:
            result = recognizer.feed(sample)
            counts[result.status] += 1
            if result.status == 'known':
                known_counts[result.profile_id] += 1
                if first_known_s is None:
                    first_known_s = (_time(sample.host_received_at) - r['start']).total_seconds()
        expected = r.get('expected_profile_id')
        outcome = 'observed'
        if r['use'] == 'evaluate':
            wrong = sum(n for profile, n in known_counts.items() if profile != expected)
            matched = (result.status == 'known' and result.profile_id == expected
                       if expected is not None else result.status in ('unknown', 'ambiguous'))
            outcome = 'mismatch' if wrong else 'matched' if matched else 'unconfirmed'
            key = r['mount_id']
            mounts.setdefault(key, dict(expected_profile_id=expected,
                scenario=r['scenario'], outcomes=[]))['outcomes'].append(outcome)
        windows.append(dict(id=r['id'], use=r['use'], capture_id=r['capture_id'],
            mount_id=r['mount_id'], expected_profile_id=expected, sample_count=len(r['samples']),
            scenario=r.get('scenario'),
            status_counts=dict(counts), known_profile_counts=dict(known_counts),
            first_known_after_s=first_known_s, final=result.as_dict(), outcome=outcome))
    coverage = []
    groups = [(r['profile_id'], 'remount') for r in recordings if r['use'] == 'train']
    groups += [(None, 'removed'), (None, 'unknown_layout')]
    for profile, scenario in groups:
        selected = [v for v in mounts.values() if v['expected_profile_id'] == profile
                    and v['scenario'] == scenario]
        # All windows belonging to a physical mount must match. Several
        # windows from one installation contribute one trial to coverage.
        matched = sum(all(x == 'matched' for x in v['outcomes']) for v in selected)
        coverage.append(dict(profile_id=profile, scenario=scenario, target_mounts=TARGET_MOUNTS,
            independent_mounts=len(selected), matched_mounts=matched,
            mismatched_mounts=sum('mismatch' in v['outcomes'] for v in selected),
            additional_mounts_needed=max(0, TARGET_MOUNTS - len(selected))))
    coverage_complete = all(r['independent_mounts'] >= TARGET_MOUNTS for r in coverage)
    # With no evaluated mounts there is no matching result to report as a pass.
    matching_complete = bool(mounts) and all(
        r['matched_mounts'] == r['independent_mounts'] for r in coverage)
    return dict(schema=REPORT_SCHEMA, title=suite['title'], source_suite=source,
        source_suite_sha256=suite_sha, config=asdict(config),
        sensor_kind=recordings[0]['samples'][0].kind_name, units='transport_counts',
        source_kind='operator_labelled_recordings', label_source='suite_operator_mount_annotations',
        result='replay_complete', coverage_complete=coverage_complete,
        all_evaluated_mounts_matched=matching_complete, coverage=coverage,
        training=training, windows=windows, hardware_accessed=False,
        manual_acceptance='pending', similarity_is_probability=False,
        inputs=[dict(id=r['id'], log=r['log'], sha256=r['sha256'], samples_sha256=r['samples_sha256'],
            capture_id=r['capture_id'], mount_id=r['mount_id'], use=r['use'],
            sample_count=len(r['samples'])) for r in recordings])
