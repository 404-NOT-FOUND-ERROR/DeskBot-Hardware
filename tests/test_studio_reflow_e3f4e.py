"""Exercise real E1.45 geometry in both production reflow configurations."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from protocol import studio_resource_package as packages
from tests import test_studio_asset_layout_native as native

ROOT = Path(__file__).resolve().parents[1]


class ReflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.AssetLayoutNativeTests.setUpClass.__func__(cls)
        cls.player_off = cls.player.with_name('player-off.exe')
        cls.player.rename(cls.player_off)
        spec = importlib.util.spec_from_file_location('reflow_compiler', ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler()
        flags = ['-UNDEBUG', '-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"',
                 '-DCONFIG_DESKBOT_ASSET_LAYOUT_ENABLE=1', '-DCONFIG_DESKBOT_ASSET_REFLOW_ENABLE=1']
        flags += ['-I', str(ROOT/'tests/cpp')]
        for path in (native.LAYOUT_STUBS, native.STUBS, native.BOARD,
                     native.VENDOR/'cjson', native.VENDOR/'mbedtls/include'):
            flags += ['-I', str(path)]
        common = command + (['c++'] if kind == 'zig-c++' else []) + flags + [
            '-include', str(native.STUBS/'sdkconfig.h'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread',
            str(native.BOARD/'studio_asset_layout.cc'), str(ROOT/'tests/cpp/studio_reflow_gfx_mock.cc')]
        objects = [str(cls.folder/'json.o'), str(cls.folder/'sha.o')]
        cls.compile(common + [str(ROOT/'tests/cpp/studio_reflow_test.cc'), *objects, '-o', str(cls.player)])
        cls.compile(common + [str(native.BOARD/'studio_catalog_builder.cc'), str(native.BOARD/'studio_catalog_adapter.cc'),
                             str(ROOT/'tests/cpp/studio_catalog_test.cc'), *objects, '-o', str(cls.directory)])
        parser, _ = packages.load_legacy_tools()
        raw = (ROOT/'tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin').read_bytes()
        cls.package = packages.extract_package(raw, 0x800000)
        cls.members = parser.parse_package_bytes(cls.package, modern=False)['members']

    fixture = native.AssetLayoutNativeTests.fixture
    run_player = native.AssetLayoutNativeTests.run_player
    directory_manifest = native.AssetLayoutNativeTests.directory_manifest

    def assert_frames(self, rows):
        for row in rows:
            self.assertEqual(row['result'], 0)
            self.assertTrue(row['covered'], row)
            if row['full']:
                self.assertEqual((row['x'], row['y'], row['width'], row['height']), (0, 0, 360, 360))
                self.assertFalse(row['mirror'])
            else:
                self.assertEqual((row['x'], row['width']), (10, 340))
                self.assertEqual(row['y'], (360-row['height'])//2 + 10)
                self.assertTrue(row['mirror'])

    def test_disabled_control_reproduces_old_height_boundary(self):
        self.player = self.player_off
        try:
            rows = self.run_player('neutral,embarrassed')
        finally:
            del self.player
        self.assertFalse(rows[-1]['covered'])

    def test_eye_160_to_200_and_back_first_frame_coverage(self):
        rows = self.run_player('neutral,embarrassed,neutral,embarrassed,neutral')
        self.assert_frames(rows)
        self.assertEqual([row['height'] for row in rows], [160, 200, 160, 200, 160])

    def test_first_play_and_full_frame_transitions(self):
        self.assert_frames(self.run_player('embarrassed,eat,embarrassed,neutral,eat,neutral'))

    def test_same_size_repeat_uses_original_refresh_path(self):
        rows = self.run_player('neutral,neutral,neutral,embarrassed,embarrassed')
        self.assert_frames(rows)
        self.assertEqual([row['refreshes'] for row in rows], [1, 1, 1, 2, 2])

    def test_new_play_before_first_image_replaces_pending_geometry(self):
        for seq in ('neutral,stage:embarrassed,neutral,embarrassed',
                    'embarrassed,stage:neutral,embarrassed,neutral',
                    'stage:embarrassed,stage:eat,neutral'):
            with self.subTest(sequence=seq):
                rows = self.run_player(seq)
                self.assert_frames([rows[-1]])

    def test_metadata_before_first_image_is_skipped(self):
        members = deepcopy(self.members)
        raw = members['shy_candidate.eaf']
        count = int.from_bytes(raw[4:8], 'little')
        table, payload = raw[16:16+count*8], raw[16+count*8:]
        entry = (4).to_bytes(4, 'little') + len(payload).to_bytes(4, 'little')
        body = entry + table + payload + b'ZZ_C'
        members['shy_candidate.eaf'] = raw[:4] + (count+1).to_bytes(4, 'little') + raw[8:12] + len(body).to_bytes(4,'little') + body
        rows = self.run_player('no-early-render,neutral,embarrassed,neutral', members)
        self.assert_frames(rows)
        self.assertEqual(rows[-1]['metadata_events'], 1)

    def test_failed_new_play_retains_prior_pending_image(self):
        rows = self.run_player('stage:neutral,fail:play,embarrassed,finish,embarrassed')
        self.assertNotEqual(rows[1]['result'], 0)
        self.assert_frames([rows[-2], rows[-1]])
        self.assertEqual([rows[-2]['height'], rows[-1]['height']], [160, 200])

    def test_callback_failures_retry_without_replaying(self):
        for fault in ('getsize', 'align', 'refresh'):
            with self.subTest(fault=fault):
                rows = self.run_player(f'stage:embarrassed,fail:{fault},finish')
                row = rows[-1]
                self.assertEqual(row['result'], 0)
                self.assertTrue(row['last_image_covered'])
                self.assertEqual(row['first_image_covered'], fault == 'refresh')
                self.assertEqual((row['x'], row['y'], row['width'], row['height']), (10, 90, 340, 200))
                self.assertEqual((row['calls'], row['refreshes']), (1, 1))

    def test_bad_ordinary_frame_table_rejects_without_changing_layout(self):
        members = deepcopy(self.members)
        raw = bytearray(members['shy_candidate.eaf'])
        raw[4:8] = (0xFFFFFFFF).to_bytes(4, 'little')
        members['shy_candidate.eaf'] = bytes(raw)
        rows = self.run_player('neutral,embarrassed,neutral', members)
        self.assertNotEqual(rows[1]['result'], 0)
        self.assertEqual(rows[1]['calls'], 1)
        self.assert_frames([rows[-1]])

    def test_reset_clears_pending_reflow(self):
        rows = self.run_player('stage:embarrassed,reset,finish')
        self.assertEqual(rows[-1]['refreshes'], 0)

    def test_real_e145_catalog_is_unchanged(self):
        expected = json.loads((ROOT/'artifacts/studio-resources-e3f4d/expected-directory.json').read_bytes())
        self.assertEqual(self.directory_manifest(), expected)


if __name__ == '__main__':
    unittest.main()
