import unittest
from pathlib import Path
from protocol.base_insert_expression import patch


class BaseInsertExpressionTests(unittest.TestCase):
    def test_only_reconnect_display_call_uses_layered_expression_entry(self):
        source = (b'void connected() {\n'
                  b'    if (was_offline) {\n'
                  b'        ESP_LOGI(TAG, "Echo base connected (reinserted)");\n'
                  b'        emote_display->InsertAnimDialog("insert", 3000);\n'
                  b'    }\n}\n'
                  b'void other() { emote_display->InsertAnimDialog("eat", 3500); }\n')
        result = patch(source)
        self.assertEqual(result.count(b'emote_display->ReplayEmotion("insert");'), 1)
        self.assertEqual(result.count(b'emote_display->InsertAnimDialog("eat", 3500);'), 1)
        self.assertIn(b'if (was_offline)', result)
        with self.assertRaisesRegex(ValueError, 'anchor_changed'):
            patch(result)

    def test_windows_line_endings_are_preserved(self):
        source = (b'emote_display->InsertAnimDialog("insert", 3000);\r\n'
                  b'emote_display->InsertAnimDialog("eat", 3500);\r\n')
        self.assertEqual(patch(source),
            b'emote_display->ReplayEmotion("insert");\r\n'
            b'emote_display->InsertAnimDialog("eat", 3500);\r\n')

    def test_production_event_gate_and_other_behaviour_are_preserved(self):
        source = (Path(__file__).resolve().parents[1] /
                  'firmware/body/main/boards/esp_vocat/base_control.cc').read_bytes()
        result = patch(source)
        self.assertEqual(result.replace(b'ReplayEmotion("insert")', b'InsertAnimDialog("insert", 3000)'), source)
        self.assertIn(b'if (was_offline)', result)
        self.assertIn(b'bool was_offline = !vocat_base_online_;', result)

    def test_missing_or_ambiguous_anchor_fails(self):
        for source in (b'', b'emote_display->InsertAnimDialog("insert", 3000);' * 2):
            with self.assertRaisesRegex(ValueError, 'anchor_changed'):
                patch(source)


if __name__ == '__main__':
    unittest.main()
