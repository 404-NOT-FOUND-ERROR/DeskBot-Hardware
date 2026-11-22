import unittest

from tests.studio_source_config import select_config_branch


class SourceConfigTests(unittest.TestCase):
    def test_selects_off_branch_and_preserves_source(self):
        source = "before\n#if CONFIG_SAMPLE_ENABLE\nnew\n#else\nold\n#endif\nafter\n"
        self.assertEqual(select_config_branch(source, "SAMPLE_ENABLE", False), "before\nold\nafter\n")

    def test_selects_on_branch(self):
        source = "#if CONFIG_SAMPLE_ENABLE\non\n#else\noff\n#endif\n"
        self.assertEqual(select_config_branch(source, "SAMPLE_ENABLE", True), "on\n")

    def test_keeps_unrelated_nested_directives(self):
        source = "#if CONFIG_SAMPLE_ENABLE\nnew\n#else\n#if OTHER\nold\n#endif\n#endif\n"
        self.assertEqual(select_config_branch(source, "SAMPLE_ENABLE", False), "#if OTHER\nold\n#endif\n")

    def test_rejects_unterminated_target(self):
        with self.assertRaisesRegex(ValueError, "unterminated"):
            select_config_branch("#if CONFIG_SAMPLE_ENABLE\nnew\n", "SAMPLE_ENABLE", False)


if __name__ == "__main__":
    unittest.main()
