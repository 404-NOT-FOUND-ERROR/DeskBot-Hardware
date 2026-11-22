"""Versioned host-side magnetic registrations with atomic local storage."""
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from protocol.magnetic_recognition import MagneticProfile, MagneticRecognizer, RecognitionConfig
from protocol.shell_repeatability import REPORT_SCHEMA

SCHEMA = 'deskbot.magnetic.profile_archive/v1'
ENGINE = 'deskbot.magnetic.m2/v1'
MAX_ARCHIVE_BYTES = 256 * 1024
DIGEST = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _object(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError(f'{label} fields differ from schema')


def _text(value, limit, label):
    if (not isinstance(value, str) or not 1 <= len(value) <= limit
            or value != value.strip() or any(ord(x) < 32 for x in value)):
        raise ValueError(f'{label} must be trimmed text')


def _hash(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError('SHA-256 digest required')


def _relative(value):
    _text(value, 512, 'source path')
    if '\\' in value or ':' in value or value.startswith('/') or '..' in value.split('/'):
        raise ValueError('source path must be project-relative')


def validate_context(value):
    _object(value, 'sensor_id frame_id sensor_kind units', 'context')
    _text(value['sensor_id'], 128, 'sensor_id')
    _text(value['frame_id'], 128, 'frame_id')
    if (type(value['sensor_kind']) is not int or value['sensor_kind'] not in (1, 2)
            or value['units'] != 'transport_counts'):
        raise ValueError('context sensor or units differ from XYZ transport')
    return dict(value)


def _recognizer(config, profiles):
    _object(config, ' '.join(f.name for f in fields(RecognitionConfig)), 'config')
    if any(type(x) not in (int, float) for x in config.values()):
        raise ValueError('recognition config requires numeric values')
    config = RecognitionConfig(**config)
    if not isinstance(profiles, list) or len(profiles) > config.max_profiles:
        raise ValueError('profile count exceeds configuration')
    parsed = []
    for row in profiles:
        _object(row, ' '.join(f.name for f in fields(MagneticProfile)), 'profile')
        parsed.append(MagneticProfile(**row))
    return MagneticRecognizer.from_profiles(parsed, config=config)


def validate_archive(value):
    _object(value, 'schema engine qualification context config profiles provenance payload_sha256', 'archive')
    if (value['schema'] != SCHEMA or value['engine'] != ENGINE
            or value['qualification'] != 'offline_replay'):
        raise ValueError('archive version, engine or qualification differs')
    _hash(value['payload_sha256'])
    payload = {k: v for k, v in value.items() if k != 'payload_sha256'}
    if digest(canonical(payload)) != value['payload_sha256']:
        raise ValueError('archive digest differs')
    context = validate_context(value['context'])
    recognizer = _recognizer(value['config'], value['profiles'])
    if any(p.kind != context['sensor_kind'] for p in recognizer.profiles):
        raise ValueError('profile sensor differs from context')
    provenance = value['provenance']
    _object(provenance, 'source_suite source_suite_sha256 report_sha256 training', 'provenance')
    _relative(provenance['source_suite'])
    _hash(provenance['source_suite_sha256'])
    _hash(provenance['report_sha256'])
    training = provenance['training']
    if not isinstance(training, list) or len(training) != len(recognizer.profiles):
        raise ValueError('one training source per profile required')
    ids, mounts = set(), set()
    for row in training:
        _object(row, 'id profile_id log sha256 samples_sha256 capture_id mount_id', 'training source')
        for name in ('id', 'profile_id', 'capture_id', 'mount_id'):
            _text(row[name], 64, name)
        _relative(row['log'])
        _hash(row['sha256'])
        _hash(row['samples_sha256'])
        if row['profile_id'] in ids or row['mount_id'] in mounts:
            raise ValueError('training profiles and mounts must be unique')
        ids.add(row['profile_id'])
        mounts.add(row['mount_id'])
    if ids != {p.profile_id for p in recognizer.profiles}:
        raise ValueError('training sources differ from profiles')
    raw = canonical(value)
    if len(raw) + 1 > MAX_ARCHIVE_BYTES:
        raise ValueError('archive byte limit exceeded')
    return json.loads(raw)


def build_archive(report, context):
    """Package the registrations and source hashes from a completed S1a replay."""
    if (not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA
            or report.get('result') != 'replay_complete'
            or report.get('hardware_accessed') is not False):
        raise ValueError('completed offline S1a report required')
    sources = {row['id']: row for row in report['inputs'] if row['use'] == 'train'}
    training = []
    for item in report['training']:
        row = sources[item['recording_id']]
        training.append(dict(id=row['id'], profile_id=item['profile']['profile_id'],
            **{k: row[k] for k in ('log', 'sha256', 'samples_sha256', 'capture_id', 'mount_id')}))
    payload = dict(schema=SCHEMA, engine=ENGINE, qualification='offline_replay',
        context=validate_context(context), config=report['config'],
        profiles=[item['profile'] for item in report['training']],
        provenance=dict(source_suite=report['source_suite'],
            source_suite_sha256=report['source_suite_sha256'],
            report_sha256=digest(canonical(report)), training=training))
    return validate_archive(dict(payload, payload_sha256=digest(canonical(payload))))


def restore_archive(archive, expected_context):
    archive = validate_archive(archive)
    if archive['context'] != validate_context(expected_context):
        raise ValueError('archive sensor or installation context differs')
    return _recognizer(archive['config'], archive['profiles'])


def verify_replay(archive, expected_context, suite_path, root):
    """Replay independent recordings after loading, without registering again."""
    from protocol.shell_repeatability import evaluate_suite
    archive = validate_archive(archive)
    recognizer = restore_archive(archive, expected_context)
    report = evaluate_suite(suite_path, root, saved_profiles=recognizer.profiles)
    # Rebuild provenance from the verified suite as well as the result hash.
    # A self-consistent file digest alone cannot establish its training source.
    if archive != build_archive(report, expected_context):
        raise ValueError('restored replay differs from archive source')
    return report


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON field')
        value[key] = item
    return value


def _read_bytes(path):
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError('archive byte limit exceeded')
    return raw


def read_archive(path, expected_context):
    raw = _read_bytes(path)
    archive = validate_archive(json.loads(raw, object_pairs_hook=_unique))
    restore_archive(archive, expected_context)
    return archive, digest(raw)


def save_archive(path, archive, *, expected_sha256=None):
    """Replace one local archive after comparing the caller's last-read digest."""
    archive = validate_archive(archive)
    raw = canonical(archive) + b'\n'
    path = Path(path)
    if expected_sha256 is not None:
        _hash(expected_sha256)
    lock = path.with_name(path.name + '.lock')
    # Exclusive creation covers the read/compare/replace sequence across processes.
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    staged = None
    try:
        os.close(descriptor)
        current = digest(_read_bytes(path)) if path.exists() else None
        if current != expected_sha256:
            raise ValueError('archive changed since last read')
        with tempfile.NamedTemporaryFile(mode='wb', prefix=path.name + '.',
                suffix='.tmp', dir=path.parent, delete=False) as handle:
            staged = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        return digest(raw)
    finally:
        try:
            if staged is not None:
                staged.unlink(missing_ok=True)
        finally:
            lock.unlink()
