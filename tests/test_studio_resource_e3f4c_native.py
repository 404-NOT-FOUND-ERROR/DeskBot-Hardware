"""Actual E1.45 bytes through the production E3f-4c catalog and layout owner."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from protocol import studio_installed_catalog as catalog
from protocol import studio_resource_package as packages
from tests import test_studio_asset_layout_native as native

ROOT = Path(__file__).resolve().parents[1]


class E3f4cResourceNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.AssetLayoutNativeTests.setUpClass.__func__(cls)
        parser, _ = packages.load_legacy_tools()
        raw = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.package = packages.extract_package(raw, 0x800000)
        cls.members = parser.parse_package_bytes(cls.package, modern=False)["members"]

    fixture = native.AssetLayoutNativeTests.fixture
    run_player = native.AssetLayoutNativeTests.run_player
    directory_manifest = native.AssetLayoutNativeTests.directory_manifest

    def test_real_e145_directory_keeps_full_frame_and_changes_only_embarrassed(self):
        actual = self.directory_manifest()
        expected = json.loads((ROOT / "artifacts/studio-layout-e3f4c-device/expected-directory.json").read_bytes())
        entries = deepcopy(expected["entries"])
        shy = next(row for row in entries if row["kind"] == "expression" and row["id"] == "embarrassed")
        shy.update(resource_id="shy_candidate.eaf", bytes=len(self.members["shy_candidate.eaf"]),
                   resource_sha256=packages.sha(self.members["shy_candidate.eaf"]), fps=25)
        expected = catalog.build_manifest(entries, packages.sha(self.package), packages.sha(self.members["index.json"]))
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual["entries"]), 37)
        output = ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(packages.json_bytes(actual))

    def test_real_e145_eat_neutral_repeated_layout_switches(self):
        rows = self.run_player("eat,neutral,eat,neutral,eat,eat")
        self.assertTrue(all(row["covered"] and row["result"] == 0 for row in rows))
        self.assertEqual([row["mirror"] for row in rows], [False, True, False, True, False, False])
        for row in rows:
            self.assertEqual((row["x"], row["y"], row["width"], row["height"]),
                             (0, 0, 360, 360) if row["full"] else (10, 110, 340, 160))


if __name__ == "__main__":
    unittest.main()
