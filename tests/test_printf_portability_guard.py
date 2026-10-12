"""Offline regression tests for nano libc source-format deployment gate."""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "audit-printf-portability.py"
SPEC = importlib.util.spec_from_file_location("printf_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class PrintfPortabilityTests(unittest.TestCase):
    def test_real_stage4b_log_failure_is_blocked(self):
        before = guard.detect('ESP_LOGW(TAG, "Echo base disconnected (timeout: %lld ms)", age);', "base.cc")
        after = guard.detect('ESP_LOGW(TAG, "Echo base disconnected (timeout: %lld ms)", age);\n'
                             'ESP_LOGI(TAG, "Magnetic state seq=%llu status=%s", seq, state);', "base.cc")
        report = guard.classify(before, after, True)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(len(report["preexisting"]), 1)
        self.assertEqual(report["new_blocking"][0]["line"], 2)

    def test_new_json_formatter_is_blocked(self):
        hit = guard.detect('std::snprintf(text, sizeof(text), "%llu", value);', "new.h")
        self.assertEqual(guard.classify([], hit, True)["status"], "fail")

    def test_manual_digits_and_string_logging_are_safe(self):
        after = guard.detect('digits[i] = char(\'0\' + value % 10);\n'
                             'ESP_LOGI(TAG, "Magnetic state seq=%s status=%s", digits, status);', "fixed.cc")
        self.assertEqual(after, [])
        self.assertEqual(guard.classify([], after, True)["status"], "pass")

    def test_multiline_macro_and_extended_formats(self):
        hits = guard.detect('printf("seq="\n PRIu64, seq); printf("%020llu %*.*lld %jd %I64u", a,b,c,d);', "x.cc")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["formats"], ["%020llu", "%*.*lld", "%jd", "%I64u"])
        self.assertEqual(hits[1]["formats"], ["PRIu64"])

    def test_comments_escaped_percent_and_characters_are_not_formats(self):
        hits = guard.detect('// printf("%llu", x);\n/* PRIu64 */\n'
                            'printf("%%llu %%jd"); char c=\'"\';', "x.cc")
        self.assertEqual(hits, [])
        self.assertEqual(len(guard.detect('printf("%%%llu", x);', "x.cc")), 1)

    def test_configuration_enabled_disabled_and_unknown(self):
        self.assertTrue(guard.nano_configuration('CONFIG_LIBC_NEWLIB_NANO_FORMAT=y\n')["nano_enabled"])
        self.assertTrue(guard.nano_configuration('CONFIG_NEWLIB_NANO_FORMAT=y\n')["nano_enabled"])
        self.assertFalse(guard.nano_configuration('# CONFIG_LIBC_NEWLIB_NANO_FORMAT is not set\n')["nano_enabled"])
        self.assertIsNone(guard.nano_configuration('CONFIG_ESP_ROM_HAS_NEWLIB_NANO_FORMAT=y\n')["nano_enabled"])
        hit = guard.detect('printf("%llu", x);', "x.cc")
        self.assertEqual(guard.classify([], hit, False)["status"], "pass")
        self.assertEqual(guard.classify([], hit, None)["status"], "fail")

    def test_moved_original_is_not_new_but_added_duplicate_is(self):
        before = guard.detect('printf("%lld", age);', "x.cc")
        after = guard.detect('\n\nprintf("%lld", age);', "x.cc")
        self.assertEqual(guard.classify(before, after, True)["status"], "pass")
        duplicate = guard.detect('printf("%lld", age); printf("%lld", another);', "x.cc")
        report = guard.classify(before, duplicate, True)
        self.assertEqual(len(report["new_blocking"]), 1)


if __name__ == "__main__":
    unittest.main()
