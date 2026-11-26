import json
import os
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / os.environ.get('DESKBOT_LAYERED_ASSET', 'artifacts/layered-face-20261124-r8')


class ContinuityResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ASSET / 'report.json').read_bytes())
        cls.raw = (ASSET / 'face-motion.dfm').read_bytes()

    def clips(self):
        count, at = struct.unpack_from('<II', self.raw, 24)
        return {self.raw[at + i * 48:at + i * 48 + 32].split(b'\0')[0].decode():
                (at + i * 48, struct.unpack_from('<IIHBB', self.raw, at + i * 48 + 32))
                for i in range(count)}

    def test_pinned_source_and_screen_derivation(self):
        self.assertEqual(self.report['source_afm_sha256'],
                         'f97deb06407f1549c142e81da1455a9380c9649f7fda74eff31ac21ea565aa7e')
        self.assertEqual((self.report['master_canvas'], self.report['canvas'], self.report['fps']), (480, 360, 25))
        self.assertEqual(struct.unpack_from('<H', self.raw, 6)[0], 360)
        self.assertEqual(self.report['flags'], 3)

    def test_sleepy_and_tired_share_frames_but_keep_semantic_timing(self):
        clips = self.clips()
        tried = clips['tried'][1]
        sleepy = clips['sleepy'][1]
        self.assertEqual(sleepy[:3], tried[:3])
        self.assertTrue(sleepy[3])
        self.assertFalse(tried[3])
        self.assertEqual(clips['neutral'][1][:3], clips['idle'][1][:3])
        self.assertEqual(self.report['semantics'], 29)

    def test_complete_clip_ranges_fit_shared_keyframe_table(self):
        clips = self.clips()
        key_count = struct.unpack_from('<I', self.raw, 32)[0]
        for name, (_, row) in clips.items():
            first, count, body = row[:3]
            with self.subTest(name=name):
                self.assertGreaterEqual(count, 2)
                self.assertLessEqual(count, body)
                self.assertLessEqual(first + count, key_count)
        tried = clips['tried'][1]
        self.assertEqual(tried[1] * 40, 4040)

    def test_visual_alias_mirror_setting_inherits_and_rejects_conflicts(self):
        from protocol.layered_face import resolve_mirrors
        aliases={'neutral':'idle','sleepy':'tried'}
        for setting in ({'tried':True}, {'sleepy':True}, {'tried':False,'sleepy':False}):
            result=resolve_mirrors(self.clips(),aliases,setting)
            self.assertEqual(result['tried'],result['sleepy'])
            self.assertEqual(result['tried'],next(iter(setting.values())))
        with self.assertRaisesRegex(ValueError,'conflicting_alias_mirror'):
            resolve_mirrors(self.clips(),aliases,{'tried':True,'sleepy':False})


if __name__ == '__main__':
    unittest.main()
