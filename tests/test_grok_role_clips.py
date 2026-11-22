from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from protocol.eaf_preview import decode_eaf
from protocol.grok_role_clips import PHASES, STATES, capacity, verify_seams

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/grok-role-clips-20261111'
BINARY = ROOT / 'tmp/grok-role-clips-20261111/build/grok-role-clips-render.exe'


class SeamTests(unittest.TestCase):
    def setUp(self):
        self.home = bytes(360 * 360 * 4)
        self.poses = {state: bytes([i + 1]) * len(self.home) for i, state in enumerate(STATES)}
        self.endpoints = {state: dict(entry=(self.home, pose), loop=(pose, pose), exit=(pose, self.home))
                          for state, pose in self.poses.items()}

    def test_all_shared_home_pose_loops_and_thirty_directed_pairs(self):
        result = verify_seams(self.endpoints)
        self.assertEqual(result['directed_pair_count'], 30)
        self.assertEqual(result['phase_seams'], 24)
        self.assertEqual(len(set(result['pose_rgba_sha256'].values())), 6)

    def test_single_alpha_byte_breaks_a_seam(self):
        pose = bytearray(self.poses['sad'])
        pose[3] += 1
        self.endpoints['sad']['loop'] = (bytes(pose), self.poses['sad'])
        with self.assertRaisesRegex(ValueError, 'pose or loop seam'):
            verify_seams(self.endpoints)

    def test_non_shared_neutral_fails_even_with_locally_loopable_clips(self):
        pose = self.poses['happy']
        self.endpoints['happy']['entry'] = (pose, pose)
        self.endpoints['happy']['exit'] = (pose, pose)
        with self.assertRaisesRegex(ValueError, 'neutral seam'):
            verify_seams(self.endpoints)

    def test_missing_phase_short_frame_and_missing_state_fail(self):
        del self.endpoints['idle']['exit']
        with self.assertRaises(ValueError):
            verify_seams(self.endpoints)
        self.endpoints['idle']['exit'] = (b'', b'')
        with self.assertRaises(ValueError):
            verify_seams(self.endpoints)
        del self.endpoints['idle']
        with self.assertRaises(ValueError):
            verify_seams(self.endpoints)


class CapacityTests(unittest.TestCase):
    def test_dedup_counts_full_identical_eaf_once(self):
        result = capacity({'a': b'EAF123', 'b': b'EAF123', 'c': b'EAF456'}, base_bytes=100)
        self.assertEqual((result['logical_bytes'], result['unique_eaf_bytes']), (18, 12))
        self.assertEqual((result['unique_files'], result['combined_payload_estimate_bytes']), (2, 112))
        self.assertFalse(result['package_overhead_included'])

    def test_exact_budget_and_overflow_are_reported(self):
        result = capacity({'a': b'A'}, base_bytes=7 * 1024 * 1024 - 1)
        self.assertTrue(result['estimate_within_soft_budget'])
        self.assertEqual(result['soft_remaining_estimate_bytes'], 0)
        result = capacity({'a': b'A'}, base_bytes=8 * 1024 * 1024)
        self.assertFalse(result['estimate_within_partition'])
        self.assertEqual(result['physical_remaining_estimate_bytes'], -1)

    def test_invalid_capacity_inputs_rejected(self):
        for files, size in [({}, 0), ({'a': b''}, 0), ({'a': 'bytes'}, 0), ({'a': b'a'}, -1)]:
            with self.assertRaises(ValueError):
                capacity(files, size)


@unittest.skipUnless(BINARY.exists(), 'build the native clip adapter first')
class NativeTests(unittest.TestCase):
    def test_decomposed_rotation_preserves_size_and_takes_shortest_arc(self):
        process = subprocess.run([str(BINARY), '--self-test'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout)['geometry_tests'], 'passed')

    def test_invalid_state_leaves_no_outputs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            for name in ('radar', 'idle/../happy', 'Happy', ''):
                result = subprocess.run([str(BINARY), name, directory], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless((OUTPUT / 'manifest.json').exists(), 'build the composable role package first')
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((OUTPUT / 'manifest.json').read_text(encoding='utf-8'))

    def test_actual_files_decode_and_all_seams_hold(self):
        endpoints, seen, files = {}, {}, {}
        for state in STATES:
            endpoints[state] = {}
            for phase, count in PHASES.items():
                item = self.manifest['expressions'][state]['clips'][phase]
                path = OUTPUT / item['file']
                raw = path.read_bytes()
                files[state + '/' + phase] = raw
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
                self.assertEqual(len(raw), item['bytes'])
                if item['file'] not in seen:
                    animation = decode_eaf(path, fps=20)
                    self.assertEqual(len(animation.frames), count)
                    seen[item['file']] = (animation.frames[0], animation.frames[-1])
                    del animation
                endpoints[state][phase] = seen[item['file']]
        self.assertEqual(verify_seams(endpoints), self.manifest['seams'])
        self.assertEqual(capacity(files), self.manifest['capacity'])

    def test_frozen_inputs_layout_and_no_raw_exports(self):
        for name, expected in self.manifest['generator_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)
        self.assertEqual(self.manifest['image_frames'], 426)
        self.assertFalse(self.manifest['display']['mirror'])
        self.assertEqual(self.manifest['display']['layout'], 'full_face')
        self.assertEqual(self.manifest['device_operations'], [])
        self.assertFalse((ROOT / 'tmp/grok-role-clips-20261111/raw').exists())
        for state in STATES:
            native = self.manifest['expressions'][state]['native']
            self.assertEqual((native['native_first_tick'], native['native_last_tick']), (36, 132))
            self.assertFalse(native['particles'])
            self.assertFalse(native['mirror'])

    def test_capacity_projection_repackages_existing_members_and_unique_clips(self):
        spec = importlib.util.spec_from_file_location('role_clip_builder', ROOT / 'scripts/build-grok-role-clips.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        unique = {}
        for state in STATES:
            for phase in PHASES:
                item = self.manifest['expressions'][state]['clips'][phase]
                unique.setdefault(item['sha256'], (state + '/' + phase, (OUTPUT / item['file']).read_bytes()))
        result = builder.package_projection(dict(unique.values()))
        self.assertEqual(result, self.manifest['capacity_projection'])
        self.assertEqual(result['added_unique_eaf_files'], 17)
        self.assertEqual(result['added_eaf_bytes'], self.manifest['capacity']['unique_eaf_bytes'])
        self.assertTrue(result['fits_soft_budget'])
        self.assertFalse(result['emitted_package'])
        self.assertFalse(result['device_package_eligible'])
        self.assertEqual(result['projected_package_bytes'], result['base_package_bytes'] +
                         result['added_eaf_bytes'] + result['package_overhead_bytes'])
        for clip in result['logical_clip_index']:
            self.assertEqual(clip['loop'], clip['asset_key'].endswith('/loop'))


if __name__ == '__main__':
    unittest.main()
