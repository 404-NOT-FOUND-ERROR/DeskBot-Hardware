"""Persist real registrations and verify reload through the production M2 path."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from protocol.magnetic_recognition import MagneticRecognizer
from protocol import shell_profile_archive as archive
from protocol.shell_repeatability import _read_samples, evaluate_suite

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'protocol/shell-repeatability-s1a-history.example.json'
CONTEXT = dict(sensor_id='history-bmm150-20261107', frame_id='repair-fixed-base-20261107',
               sensor_kind=1, units='transport_counts')


def reseal(value):
    value = deepcopy(value)
    value['payload_sha256'] = archive.digest(archive.canonical(
        {k: v for k, v in value.items() if k != 'payload_sha256'}))
    return value


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate_suite(SUITE, ROOT)
        cls.good = archive.build_archive(cls.report, CONTEXT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'profiles.json'

    def test_archive_round_trip_preserves_profiles_config_and_provenance(self):
        saved = archive.save_archive(self.path, self.good)
        loaded, version = archive.read_archive(self.path, CONTEXT)
        self.assertEqual(loaded, self.good)
        self.assertEqual(saved, version)
        self.assertEqual(version, archive.digest(self.path.read_bytes()))
        self.assertEqual(loaded['qualification'], 'offline_replay')
        self.assertEqual(len(loaded['profiles']), 2)
        self.assertEqual(loaded['context'], CONTEXT)
        self.assertEqual(loaded['provenance']['source_suite_sha256'], self.report['source_suite_sha256'])

    def test_restored_recognizer_has_no_window_confirmation_or_current_identity(self):
        restored = archive.restore_archive(self.good, CONTEXT)
        self.assertEqual(restored.last_result.status, 'missing')
        self.assertEqual(restored.last_result.reason, 'no_sample')
        self.assertEqual(restored.last_result.confirmation_count, 0)
        self.assertIsNone(restored.last_result.profile_id)
        self.assertFalse(restored.window)
        self.assertEqual(asdict(restored.config), self.report['config'])

    def test_restored_profile_requires_fresh_full_window_and_confirmations(self):
        restored = archive.restore_archive(self.good, CONTEXT)
        row = next(r for r in self.report['inputs'] if r['id'] == 'up-reinstalled')
        samples = _read_samples((ROOT / row['log']).read_bytes())
        states = [restored.feed(s).status for s in samples[:10]]
        self.assertTrue(all(s == 'warming_up' for s in states[:9]))
        self.assertEqual(states[9], 'known')

    def test_live_state_is_never_transferred_by_from_profiles(self):
        source = archive.restore_archive(self.good, CONTEXT)
        row = next(r for r in self.report['inputs'] if r['id'] == 'up-reinstalled')
        for sample in _read_samples((ROOT / row['log']).read_bytes())[:12]:
            source.feed(sample)
        self.assertEqual(source.last_result.status, 'known')
        restored = MagneticRecognizer.from_profiles(source.profiles, config=source.config)
        self.assertEqual(restored.last_result.reason, 'no_sample')
        self.assertIsNone(restored.last_result.profile_id)

    def test_sensor_id_frame_sensor_kind_and_units_must_match(self):
        for field, value in [('sensor_id', 'another'), ('frame_id', 'rotated'),
                             ('sensor_kind', 2), ('units', 'microtesla')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                archive.restore_archive(self.good, dict(CONTEXT, **{field: value}))

    def test_invalid_context_and_source_paths_fail(self):
        for context in [dict(CONTEXT, sensor_kind=True), dict(CONTEXT, sensor_id=' '),
                        dict(CONTEXT, frame_id='x\n'), dict(CONTEXT, extra='x')]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                archive.build_archive(self.report, context)
        value = deepcopy(self.good)
        value['provenance']['training'][0]['log'] = '../outside.log'
        with self.assertRaisesRegex(ValueError, 'project-relative'):
            archive.validate_archive(reseal(value))

    def test_digest_change_and_schema_engine_qualification_fail(self):
        value = deepcopy(self.good)
        value['profiles'][0]['center'][0] += 1
        with self.assertRaisesRegex(ValueError, 'digest'):
            archive.validate_archive(value)
        for field in ['schema', 'engine', 'qualification']:
            value = deepcopy(self.good)
            value[field] = 'unknown'
            with self.subTest(field=field), self.assertRaises(ValueError):
                archive.validate_archive(reseal(value))

    def test_duplicate_json_fields_are_rejected_even_with_valid_original_digest(self):
        raw = archive.canonical(self.good).decode('utf-8')
        self.path.write_text(raw.replace('{', '{"schema":"ignored",', 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            archive.read_archive(self.path, CONTEXT)

    def test_nonfinite_and_boolean_numeric_fields_are_rejected(self):
        for key, value in [('center', [True, 0, 0]), ('stddev', [-1, 0, 0]),
                           ('ranges', [float('nan'), 0, 0]), ('window_span_s', float('inf'))]:
            item = deepcopy(self.good)
            item['profiles'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                archive.validate_archive(reseal(item))

    def test_saved_profile_boundaries_and_configuration_are_validated(self):
        source = archive.restore_archive(self.good, CONTEXT)
        p = source.profiles[0]
        cases = [dict(profile_id=' '), dict(label=''), dict(kind=True), dict(kind=3),
                 dict(kind_name='QMC6309'), dict(center=(0, 0)), dict(center=(32768, 0, 0)),
                 dict(center=(10**500, 0, 0)), dict(stddev=(13, 0, 0)),
                 dict(stddev=(5, 0, 0), ranges=(1, 0, 0)), dict(ranges=(41, 0, 0)),
                 dict(sample_count=True), dict(sample_count=7), dict(window_span_s=.1),
                 dict(window_span_s=15), dict(window_span_s=10**500),
                 dict(registered_at=None), dict(registered_at='2026-11-12T00:00:00')]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                MagneticRecognizer.from_profiles([replace(p, **change)], config=source.config)
        with self.assertRaises(ValueError):
            MagneticRecognizer.from_profiles([p], config={})
        with self.assertRaises(ValueError):
            MagneticRecognizer.from_profiles([{}])
        with self.assertRaises(ValueError):
            MagneticRecognizer.from_profiles([replace(p, window_span_s=10**500)],
                config=replace(source.config, max_gap_s=1e308))

    def test_profile_limit_duplicate_id_and_mutable_vector_copy(self):
        source = archive.restore_archive(self.good, CONTEXT)
        first = source.profiles[0]
        with self.assertRaisesRegex(ValueError, 'maximum'):
            MagneticRecognizer.from_profiles(source.profiles,
                config=replace(source.config, max_profiles=1))
        with self.assertRaisesRegex(ValueError, 'unique'):
            MagneticRecognizer.from_profiles([first, first], config=source.config)
        values = list(first.center)
        restored = MagneticRecognizer.from_profiles([replace(first, center=values)], config=source.config)
        values[0] = 123
        self.assertEqual(restored.profiles[0].center, first.center)

    def test_auto_number_avoids_loaded_profile_identifiers(self):
        source = archive.restore_archive(self.good, CONTEXT)
        p = replace(source.profiles[0], profile_id='profile-0001')
        restored = MagneticRecognizer.from_profiles([p], config=source.config)
        row = next(r for r in self.report['inputs'] if r['id'] == 'up-reinstalled')
        samples = _read_samples((ROOT / row['log']).read_bytes())[:12]
        for sample in samples:
            restored.feed(sample)
        registered = restored.register('another shell', as_of=samples[-1].host_received_at)
        self.assertEqual(registered.profile_id, 'profile-0002')

    def test_unknown_fields_and_config_type_changes_fail(self):
        for change in [('extra', 1), ('match_radius', '80'), ('confirmations_required', True)]:
            value = deepcopy(self.good)
            value['config'][change[0]] = change[1]
            with self.subTest(change=change), self.assertRaises(ValueError):
                archive.validate_archive(reseal(value))
        value = deepcopy(self.good)
        value['profiles'][0]['extra'] = 1
        with self.assertRaises(ValueError):
            archive.validate_archive(reseal(value))

    def test_training_sources_and_profiles_must_correspond(self):
        for edit in ('count', 'profile', 'mount', 'digest', 'kind'):
            value = deepcopy(self.good)
            if edit == 'count':
                value['provenance']['training'].pop()
            elif edit == 'profile':
                value['provenance']['training'][0]['profile_id'] = 'different'
            elif edit == 'mount':
                value['provenance']['training'][1]['mount_id'] = value['provenance']['training'][0]['mount_id']
            elif edit == 'digest':
                value['provenance']['training'][0]['samples_sha256'] = 'invalid'
            else:
                value['context']['sensor_kind'] = 2
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                archive.validate_archive(reseal(value))

    def test_create_is_exclusive_and_update_requires_exact_file_digest(self):
        first = archive.save_archive(self.path, self.good)
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive.save_archive(self.path, self.good)
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive.save_archive(self.path, self.good, expected_sha256='0' * 64)
        changed = deepcopy(self.good)
        changed['profiles'][0]['label'] = '更换名称'
        changed = reseal(changed)
        next_version = archive.save_archive(self.path, changed, expected_sha256=first)
        self.assertNotEqual(next_version, first)
        self.assertEqual(archive.read_archive(self.path, CONTEXT)[0], changed)
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive.save_archive(self.path, self.good, expected_sha256=first)
        self.assertFalse(self.path.with_suffix('.json.lock').exists())

    def test_compare_version_uses_actual_file_bytes(self):
        first = archive.save_archive(self.path, self.good)
        self.path.write_text(json.dumps(self.good, indent=4), encoding='utf-8')
        loaded, version = archive.read_archive(self.path, CONTEXT)
        self.assertEqual(loaded, self.good)
        self.assertNotEqual(version, first)
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive.save_archive(self.path, self.good, expected_sha256=first)
        self.assertEqual(archive.save_archive(self.path, self.good, expected_sha256=version), first)

    def test_write_flush_replace_failures_preserve_original_and_remove_temporary_files(self):
        original = archive.save_archive(self.path, self.good)
        for function in ['tempfile.NamedTemporaryFile', 'os.fsync', 'os.replace']:
            with self.subTest(function=function), patch(
                    'protocol.shell_profile_archive.' + function, side_effect=OSError('injected')):
                with self.assertRaisesRegex(OSError, 'injected'):
                    archive.save_archive(self.path, self.good, expected_sha256=original)
            self.assertEqual(archive.digest(self.path.read_bytes()), original)
            self.assertEqual([p.name for p in self.root.iterdir()], ['profiles.json'])

    def test_lock_keeps_another_writer_out_without_removing_its_lock(self):
        lock = self.path.with_name(self.path.name + '.lock')
        lock.write_bytes(b'other writer')
        with self.assertRaises(FileExistsError):
            archive.save_archive(self.path, self.good)
        self.assertEqual(lock.read_bytes(), b'other writer')
        self.assertFalse(self.path.exists())

    def test_oversized_or_malformed_archive_does_not_replace_existing(self):
        version = archive.save_archive(self.path, self.good)
        with patch.object(archive, 'MAX_ARCHIVE_BYTES', 20):
            with self.assertRaisesRegex(ValueError, 'byte limit'):
                archive.read_archive(self.path, CONTEXT)
            with self.assertRaisesRegex(ValueError, 'byte limit'):
                archive.save_archive(self.path, self.good, expected_sha256=version)
        self.assertEqual(archive.digest(self.path.read_bytes()), version)
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            archive.read_archive(self.path, CONTEXT)

    def test_restored_replay_matches_original_without_registering_again(self):
        with patch.object(MagneticRecognizer, 'register', side_effect=AssertionError('must not retrain')):
            report = archive.verify_replay(self.good, CONTEXT, SUITE, ROOT)
        self.assertEqual(archive.canonical(report), archive.canonical(self.report))
        self.assertEqual([r['outcome'] for r in report['windows'][:2]], ['matched', 'matched'])
        self.assertFalse(report['coverage_complete'])

    def test_restored_replay_rejects_changed_source_or_parameters(self):
        for edit in ('source', 'config', 'profile'):
            value = deepcopy(self.good)
            if edit == 'source':
                value['provenance']['source_suite_sha256'] = '0' * 64
            elif edit == 'config':
                value['config']['match_radius'] = 79
            else:
                value['profiles'][0]['center'][0] += 1
            with self.subTest(edit=edit), self.assertRaisesRegex(ValueError, 'differs'):
                archive.verify_replay(reseal(value), CONTEXT, SUITE, ROOT)

    def test_replay_checks_each_training_source_even_with_a_new_valid_payload_digest(self):
        for field, replacement in [('sha256', '0' * 64), ('samples_sha256', '0' * 64),
                ('log', 'tmp/nonexistent.log'), ('id', 'another-record'),
                ('capture_id', 'another-capture'), ('mount_id', 'another-mount')]:
            value = deepcopy(self.good)
            value['provenance']['training'][0][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differs'):
                archive.verify_replay(reseal(value), CONTEXT, SUITE, ROOT)

    def test_build_accepts_only_completed_offline_report(self):
        for change in [dict(result='partial'), dict(schema='old'), dict(hardware_accessed=True)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                archive.build_archive(dict(self.report, **change), CONTEXT)


class ArchiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, target, *, restore=None, frame=CONTEXT['frame_id']):
        args = [sys.executable, str(ROOT / 'scripts/prepare-shell-profile-archive.py'),
            '--suite', str(SUITE), '--output', str(target), '--sensor-id', CONTEXT['sensor_id'],
            '--frame-id', frame, '--sensor-kind', '1']
        if restore:
            args += ['--restore', str(restore)]
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')

    def test_separate_process_create_and_reload_from_actual_history(self):
        create = self.root / 'create'
        run = self.command(create)
        self.assertEqual(run.returncode, 0, run.stderr)
        restore = self.root / 'restore'
        run = self.command(restore, restore=create / 'profiles.json')
        self.assertEqual(run.returncode, 0, run.stderr)
        first = json.loads((create / 'report.json').read_bytes())
        second = json.loads((restore / 'report.json').read_bytes())
        self.assertNotEqual(first['process_id'], second['process_id'])
        self.assertEqual(first['coverage'], second['coverage'])
        self.assertTrue(second['replay_matches_source'])
        self.assertTrue(second['empty_stream_on_load'])
        self.assertFalse(second['hardware_accessed'])
        self.assertEqual(second['qualification'], 'offline_replay')
        self.assertEqual(second['initial_state']['status'], 'missing')
        self.assertIn('missing / no_sample', (restore / 'index.html').read_text(encoding='utf-8'))

    def test_cli_rejects_context_mismatch_reused_output_and_external_output(self):
        create = self.root / 'create'
        self.assertEqual(self.command(create).returncode, 0)
        self.assertNotEqual(self.command(create).returncode, 0)
        bad = self.root / 'bad'
        run = self.command(bad, restore=create / 'profiles.json', frame='rotated')
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(bad.exists())
        with tempfile.TemporaryDirectory() as external:
            run = self.command(Path(external) / 'report')
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(external) / 'report').exists())

    def test_two_process_updates_allow_only_one_writer_for_the_read_version(self):
        target = self.root / 'profiles.json'
        good = archive.build_archive(evaluate_suite(SUITE, ROOT), CONTEXT)
        archive.save_archive(target, good)
        code = '''
import json, sys
from pathlib import Path
from protocol.shell_profile_archive import read_archive, save_archive, canonical, digest
path = Path(sys.argv[1])
context = json.loads(sys.argv[2])
value, version = read_archive(path, context)
value['profiles'][0]['label'] = sys.argv[3]
value['payload_sha256'] = digest(canonical({k:v for k,v in value.items() if k != 'payload_sha256'}))
print('ready', flush=True)
sys.stdin.readline()
try:
    save_archive(path, value, expected_sha256=version)
    print('saved', flush=True)
except (OSError, ValueError):
    print('conflict', flush=True)
'''
        processes = []
        try:
            for label in ('first', 'second'):
                processes.append(subprocess.Popen([sys.executable, '-B', '-c', code,
                    str(target), json.dumps(CONTEXT), label], cwd=ROOT,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8'))
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
            for process in processes:
                process.stdin.write('go\n')
                process.stdin.flush()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(stdout.strip())
            self.assertEqual(sorted(results), ['conflict', 'saved'])
            self.assertIn(archive.read_archive(target, CONTEXT)[0]['profiles'][0]['label'], ('first', 'second'))
            self.assertFalse(target.with_name(target.name + '.lock').exists())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)

    def test_preview_escapes_context_and_labels(self):
        spec = importlib.util.spec_from_file_location('archive_cli', ROOT / 'scripts/prepare-shell-profile-archive.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        value = archive.build_archive(evaluate_suite(SUITE, ROOT), CONTEXT)
        value['context']['sensor_id'] = '<script>alert(1)</script>'
        value['profiles'][0]['profile_id'] = '<b>id</b>'
        result = cli.preview(dict(mode='create'), value)
        self.assertNotIn('<script>', result)
        self.assertNotIn('<b>id</b>', result)
        self.assertIn('&lt;script&gt;', result)


if __name__ == '__main__':
    unittest.main()
