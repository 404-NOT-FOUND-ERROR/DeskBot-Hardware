"""Real decoder/recognizer integration and mounting independence checks."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

from protocol import shell_repeatability as subject

ROOT = Path(__file__).resolve().parents[1]


class RepeatabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        self.number = 0

    def add(self, use, profile=None, *, mount=None, capture='capture-1',
            scenario=None, xyz=(0, 0, 0), values=None, kind=1, start=None):
        self.number += 1
        ident = f'record-{self.number}'
        start = self.number * 10 if start is None else start
        origin = datetime(2026, 11, 12, tzinfo=timezone.utc) + timedelta(seconds=start)
        lines = []
        for i, value in enumerate(values or [xyz] * 12):
            payload = bytes((kind,)) + b''.join(v.to_bytes(2, 'big', signed=True) for v in value)
            stamp = (origin + timedelta(seconds=i * .1)).isoformat()
            lines.append(f'{stamp}\tHandle: cmd=06, {payload.hex(" ")}\n')
        log = self.root / (ident + '.log')
        log.write_text(''.join(lines), encoding='utf-8')
        row = dict(id=ident, capture_id=capture, mount_id=mount or ident, use=use,
                   log=log.name, sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        if use == 'train':
            row['profile_id'] = profile
        elif use == 'evaluate':
            row.update(expected_profile_id=profile,
                       scenario=scenario or ('remount' if profile else 'removed'))
        self.rows.append(row)
        return row

    def edit(self, row, text):
        path = self.root / row['log']
        path.write_text(text, encoding='utf-8')
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def suite(self, config=None):
        value = dict(schema=subject.SUITE_SCHEMA, title='fixture', config=config or dict(
            window_size=4, min_window_span_s=.2, max_gap_s=.5,
            stable_std_max=5, stable_range_max=12, match_radius=30, confirmations_required=2),
            recordings=self.rows)
        path = self.root / 'suite.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def evaluate(self):
        return subject.evaluate_suite(self.suite(), self.root)

    def train(self):
        return self.add('train', 'layout-a')

    def test_known_remount_and_separate_negative_coverage(self):
        self.train()
        self.add('evaluate', 'layout-a')
        self.add('evaluate', scenario='removed', xyz=(200, 0, 0))
        report = self.evaluate()
        self.assertEqual([w['outcome'] for w in report['windows']], ['matched', 'matched'])
        self.assertEqual([r['independent_mounts'] for r in report['coverage']], [1, 1, 0])
        self.assertEqual([r['additional_mounts_needed'] for r in report['coverage']], [9, 9, 10])
        self.assertFalse(report['coverage_complete'])
        self.assertTrue(report['all_evaluated_mounts_matched'])
        self.assertEqual(report['sensor_kind'], 'BMM150')
        self.assertFalse(report['hardware_accessed'])
        self.assertEqual(report['manual_acceptance'], 'pending')

    def test_same_mount_multiple_windows_count_once(self):
        self.train()
        self.add('evaluate', 'layout-a', mount='mount-a')
        self.add('evaluate', 'layout-a', mount='mount-a')
        report = self.evaluate()
        self.assertEqual(report['coverage'][0]['independent_mounts'], 1)
        self.assertEqual(report['coverage'][0]['matched_mounts'], 1)

    def test_same_mount_across_capture_sessions_counts_once(self):
        self.train()
        self.add('evaluate', 'layout-a', mount='physical-1', capture='first')
        self.add('evaluate', 'layout-a', mount='physical-1', capture='second')
        report = self.evaluate()
        self.assertEqual(report['coverage'][0]['independent_mounts'], 1)
        self.assertEqual(report['coverage'][0]['matched_mounts'], 1)

    def test_one_bad_window_fails_mount_even_if_last_window_recovers(self):
        self.train()
        self.add('evaluate', 'layout-a', mount='mount-a', xyz=(200, 0, 0))
        self.add('evaluate', 'layout-a', mount='mount-a')
        self.assertEqual(self.evaluate()['coverage'][0]['matched_mounts'], 0)

    def test_training_mount_cannot_be_evaluation(self):
        self.train()
        self.add('evaluate', 'layout-a', mount='record-1')
        with self.assertRaisesRegex(ValueError, 'physical mount'):
            self.evaluate()

    def test_training_mount_cannot_be_evaluation_across_capture_sessions(self):
        self.add('train', 'layout-a', mount='physical-1', capture='first')
        self.add('evaluate', 'layout-a', mount='physical-1', capture='second')
        with self.assertRaisesRegex(ValueError, 'physical mount'):
            self.evaluate()

    def test_same_mount_cannot_have_conflicting_scenarios(self):
        self.train()
        self.add('evaluate', scenario='removed', mount='same', xyz=(200, 0, 0))
        self.add('evaluate', scenario='unknown_layout', mount='same', xyz=(210, 0, 0))
        with self.assertRaisesRegex(ValueError, 'physical mount'):
            self.evaluate()

    def test_copied_sample_subset_with_different_file_bytes_is_rejected(self):
        source = self.train()
        copy = self.add('evaluate', 'layout-a', capture='other-capture')
        lines = (self.root / source['log']).read_text(encoding='utf-8').splitlines(True)
        self.edit(copy, ''.join(lines[1:]))
        with self.assertRaisesRegex(ValueError, 'reuse the same samples'):
            self.evaluate()

    def test_overlapping_acquisition_times_are_rejected_even_with_different_values(self):
        self.train()
        self.add('evaluate', 'layout-a', xyz=(1, 0, 0), start=10.2)
        with self.assertRaisesRegex(ValueError, 'times overlap'):
            self.evaluate()

    def test_utc_equivalent_sample_times_do_not_avoid_duplicate_check(self):
        source = self.train()
        copy = self.add('evaluate', 'layout-a', capture='other-capture')
        self.edit(copy, (self.root / source['log']).read_text(encoding='utf-8').replace('+00:00', 'Z'))
        with self.assertRaisesRegex(ValueError, 'reuse the same samples'):
            self.evaluate()

    def test_rejects_changed_digest(self):
        row = self.train()
        row['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.evaluate()

    def test_rejects_path_escape(self):
        row = self.train()
        row['log'] = '../' + self.root.name + '/' + row['log']
        # Resolves back inside, which is a valid normalized path.
        self.evaluate()
        with tempfile.TemporaryDirectory(dir=self.root.parent) as outside:
            outside_log = Path(outside) / 'outside.log'
            outside_log.write_text('', encoding='utf-8')
            row['log'] = '../' + Path(outside).name + '/outside.log'
            with self.assertRaisesRegex(ValueError, 'escapes'):
                self.evaluate()

    def test_rejects_missing_timezone_and_duplicate_sample_time(self):
        for mode in ('timezone', 'duplicate'):
            with self.subTest(mode=mode):
                self.rows = []
                row = self.train()
                text = (self.root / row['log']).read_text(encoding='utf-8')
                if mode == 'timezone':
                    text = text.replace('+00:00', '')
                else:
                    lines = text.splitlines(True)
                    lines[1] = lines[0]
                    text = ''.join(lines)
                self.edit(row, text)
                with self.assertRaisesRegex(ValueError, 'timezone|increase'):
                    self.evaluate()

    def test_rejects_multiple_sensor_kinds_and_scalar_sensor(self):
        self.train()
        self.add('evaluate', 'layout-a', kind=2)
        with self.assertRaisesRegex(ValueError, 'one sensor kind'):
            self.evaluate()
        self.rows.pop()
        self.add('evaluate', 'layout-a', kind=3)
        with self.assertRaisesRegex(ValueError, 'XYZ sensor'):
            self.evaluate()

    def test_rejects_malformed_magnetic_payload(self):
        row = self.train()
        self.edit(row, '2026-11-12T00:00:00Z\tHandle: cmd=06, 01 00\n')
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_rejects_train_gap_and_unstable_training(self):
        row = self.train()
        text = (self.root / row['log']).read_text(encoding='utf-8')
        lines = text.splitlines(True)
        self.edit(row, ''.join([lines[0], *lines[10:]]))
        with self.assertRaisesRegex(ValueError, 'sampling gap'):
            self.evaluate()
        self.rows.clear()
        self.add('train', 'layout-a', values=[(i % 2 * 100, 0, 0) for i in range(12)])
        with self.assertRaisesRegex(ValueError, 'stable window'):
            self.evaluate()

    def test_rejects_unregistered_profile_and_invalid_scenario(self):
        self.train()
        row = self.add('evaluate', 'missing')
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            self.evaluate()
        row['expected_profile_id'] = None
        with self.assertRaisesRegex(ValueError, 'scenario'):
            self.evaluate()

    def test_records_wrong_known_even_if_final_rejects(self):
        self.train()
        self.add('evaluate', scenario='removed', values=[(0, 0, 0)] * 8 + [(200, 0, 0)] * 12)
        row = self.evaluate()['windows'][0]
        self.assertEqual(row['final']['status'], 'unknown')
        self.assertGreater(row['known_profile_counts']['layout-a'], 0)
        self.assertEqual(row['outcome'], 'mismatch')

    def test_short_and_unstable_recordings_remain_unconfirmed(self):
        self.train()
        self.add('evaluate', 'layout-a', values=[(0, 0, 0)] * 2)
        self.add('evaluate', 'layout-a', values=[(i % 2 * 100, 0, 0) for i in range(12)])
        report = self.evaluate()
        self.assertEqual([r['outcome'] for r in report['windows']], ['unconfirmed', 'unconfirmed'])
        self.assertEqual(report['windows'][1]['final']['status'], 'unstable')

    def test_ambiguous_is_not_assigned_a_profile(self):
        self.train()
        self.add('train', 'layout-b', xyz=(20, 0, 0))
        self.add('evaluate', 'layout-a', xyz=(10, 0, 0))
        row = self.evaluate()['windows'][0]
        self.assertEqual(row['final']['status'], 'ambiguous')
        self.assertEqual(row['outcome'], 'unconfirmed')
        self.assertIsNone(row['final']['profile_id'])

    def test_observation_never_counts_toward_mount_coverage(self):
        self.train()
        self.add('observe')
        report = self.evaluate()
        self.assertEqual(report['windows'][0]['outcome'], 'observed')
        self.assertTrue(all(row['independent_mounts'] == 0 for row in report['coverage']))
        self.assertFalse(report['all_evaluated_mounts_matched'])

    def test_training_only_has_no_matching_result(self):
        self.train()
        report = self.evaluate()
        self.assertEqual(report['windows'], [])
        self.assertFalse(report['coverage_complete'])
        self.assertFalse(report['all_evaluated_mounts_matched'])

    def test_ten_labelled_fixture_mounts_in_each_group_complete_coverage(self):
        self.train()
        for i in range(10):
            self.add('evaluate', 'layout-a')
            self.add('evaluate', scenario='removed', xyz=(200, 0, 0))
            self.add('evaluate', scenario='unknown_layout', xyz=(300, 0, 0))
        report = self.evaluate()
        self.assertTrue(report['coverage_complete'])
        self.assertTrue(report['all_evaluated_mounts_matched'])

    def test_suite_log_and_total_sample_limits(self):
        self.train()
        suite = self.suite()
        for constant, limit in [('MAX_MANIFEST_BYTES', 1), ('MAX_LOG_BYTES', 1),
                                ('MAX_TOTAL_BYTES', 1), ('MAX_TOTAL_SAMPLES', 3)]:
            with self.subTest(constant=constant), patch.object(subject, constant, limit):
                with self.assertRaisesRegex(ValueError, 'limit'):
                    subject.evaluate_suite(suite, self.root)

    def test_unknown_fields_and_invalid_config_fail(self):
        self.train()
        path = self.suite()
        value = json.loads(path.read_bytes())
        value['recordings'][0]['extra'] = True
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'fields'):
            subject.evaluate_suite(path, self.root)
        path = self.suite(config={'window_size': True})
        with self.assertRaises(ValueError):
            subject.evaluate_suite(path, self.root)


class RepeatabilityIntegrationTests(unittest.TestCase):
    def test_actual_repair_recordings_have_one_remount_and_one_removed_case(self):
        report = subject.evaluate_suite(ROOT / 'protocol/shell-repeatability-s1a-history.example.json', ROOT)
        self.assertEqual(report['sensor_kind'], 'BMM150')
        self.assertEqual(report['config'], as_config())
        self.assertEqual([v['independent_mounts'] for v in report['coverage']], [1, 0, 1, 0])
        self.assertEqual([v['outcome'] for v in report['windows'][:2]], ['matched', 'matched'])
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['manual_acceptance'], 'pending')

    def test_cli_report_preview_and_reused_destination(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temp:
            target = Path(temp) / 'report'
            args = [sys.executable, str(ROOT / 'scripts/evaluate-shell-repeatability.py'),
                    '--suite', str(ROOT / 'protocol/shell-repeatability-s1a-history.example.json'),
                    '--output', str(target)]
            run = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((target / 'report.json').read_bytes())
            self.assertFalse(report['coverage_complete'])
            self.assertIn('未登记布局', (target / 'index.html').read_text(encoding='utf-8'))
            self.assertEqual(subprocess.run(args, cwd=ROOT, capture_output=True).returncode, 1)

    def test_preview_escapes_input_text(self):
        spec = importlib.util.spec_from_file_location('repeatability_cli', ROOT / 'scripts/evaluate-shell-repeatability.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        report = subject.evaluate_suite(ROOT / 'protocol/shell-repeatability-s1a-history.example.json', ROOT)
        report['title'] = '<script>bad()</script>'
        text = cli.preview(report)
        self.assertNotIn('<script>bad()', text)
        self.assertIn('&lt;script&gt;', text)


def as_config():
    from dataclasses import asdict
    return asdict(subject.RecognitionConfig())


if __name__ == '__main__':
    unittest.main()
