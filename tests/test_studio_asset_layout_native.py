"""Compile the layout owner and catalog with real resource bytes, cJSON and SHA."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import jsonschema
from protocol import studio_installed_catalog as catalog
from protocol import studio_resource_package as packages
from tests import test_studio_catalog_native as native
DEVICE, BOOT, SESSION = native.DEVICE, native.BOOT, native.SESSION

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"
STUBS = ROOT / "tests/cpp/catalog_stubs"
LAYOUT_STUBS = ROOT / "tests/cpp/layout_stubs"
VENDOR = ROOT / "tests/vendor/studio_catalog"


class AssetLayoutNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("layout_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        if not command:
            raise RuntimeError("C++17 compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="layout-native-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        flags = ["-UNDEBUG", '-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"', "-DCONFIG_DESKBOT_ASSET_LAYOUT_ENABLE=1"]
        for path in (LAYOUT_STUBS, STUBS, BOARD, VENDOR / "cjson", VENDOR / "mbedtls/include"):
            flags += ["-I", str(path)]
        cls.compile = native.CatalogNativeTests.compile.__func__.__get__(cls, cls.__class__)
        objects = []
        for name, source in (("json", VENDOR / "cjson/cJSON.c"), ("sha", VENDOR / "mbedtls/library/sha256.c")):
            output = cls.folder / (name + ".o")
            cls.compile(command + (["cc"] if kind == "zig-c++" else ["-x", "c"]) + flags + [
                "-std=c11", "-c", str(source), "-o", str(output)])
            objects.append(str(output))
        common = command + (["c++"] if kind == "zig-c++" else []) + flags + [
            "-include", str(STUBS / "sdkconfig.h"), "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
            str(BOARD / "studio_asset_layout.cc"), str(ROOT / "tests/cpp/studio_layout_gfx_mock.cc")]
        cls.player = cls.folder / ("player.exe" if os.name == "nt" else "player")
        cls.directory = cls.folder / ("directory.exe" if os.name == "nt" else "directory")
        cls.compile(common + [str(ROOT / "tests/cpp/studio_asset_layout_test.cc"), *objects, "-o", str(cls.player)])
        cls.compile(common + [str(BOARD / "studio_catalog_builder.cc"), str(BOARD / "studio_catalog_adapter.cc"),
                             str(ROOT / "tests/cpp/studio_catalog_test.cc"), *objects, "-o", str(cls.directory)])
        parser, _ = packages.load_legacy_tools()
        raw = packages.extract_package(packages.BASE_IMAGE.read_bytes(), 0x800000)
        cls.members = parser.parse_package_bytes(raw, modern=False)["members"]
        cls.schema = json.loads((ROOT / "protocol/deskbot-studio-catalog-page-v1.schema.json").read_bytes())

    def fixture(self, members=None):
        files = deepcopy(self.members if members is None else members)
        folder = self.folder / self.id().rsplit(".", 1)[-1]
        folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            old.unlink()
        for name, data in files.items():
            (folder / name).write_bytes(data)
        _, packer = packages.load_legacy_tools()
        (folder / "package.bin").write_bytes(packer.build_legacy_package(files))
        return folder

    def run_player(self, sequence, members=None):
        result = subprocess.run([str(self.player), str(self.fixture(members)), sequence], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def directory_manifest(self, members=None):
        result = subprocess.run([str(self.directory), "pages", str(self.fixture(members))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        collector = catalog.CatalogCollector(DEVICE, BOOT, SESSION)
        for line in result.stdout.splitlines():
            row = json.loads(line)
            jsonschema.Draft202012Validator(self.schema).validate(row)
            collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        return collector.confirmed

    def test_eat_neutral_eat_restores_mirror_and_first_frame_position(self):
        rows = self.run_player("eat,neutral,eat,neutral,eat,eat")
        self.assertEqual([r["result"] for r in rows], [0] * 6)
        self.assertEqual([r["mirror"] for r in rows], [False, True, False, True, False, False])
        for row in rows:
            self.assertTrue(row["covered"])
            self.assertEqual((row["x"], row["y"], row["width"], row["height"]),
                             (0, 0, 360, 360) if row["full"] else (10, 110, 340, 160))
        self.assertEqual(rows[-1]["calls"], 6)
        self.assertEqual(rows[-1]["refreshes"], 6)
        self.assertGreater(rows[-1]["image_events"], 6)

    def test_first_frame_before_render_and_metadata_callbacks(self):
        members = deepcopy(self.members)
        # Put a metadata frame before the first eye image. The real reviewed
        # eat file's metadata is at the end and is not an initial callback.
        raw = members["winking.eaf"]
        count = int.from_bytes(raw[4:8], "little")
        table, payload = raw[16:16 + count * 8], raw[16 + count * 8:]
        entry = (4).to_bytes(4, "little") + len(payload).to_bytes(4, "little")
        body = entry + table + payload + b"ZZ_C"
        members["winking.eaf"] = (raw[:4] + (count + 1).to_bytes(4, "little") + raw[8:12]
                                   + len(body).to_bytes(4, "little") + body)
        rows = self.run_player("no-early-render,eat,neutral,eat", members)
        self.assertTrue(all(row["covered"] and row["result"] == 0 for row in rows))
        self.assertEqual(rows[-1]["refreshes"], 3)
        self.assertEqual(rows[-1]["metadata_events"], 1)

    def test_new_play_and_reset_replace_pending_reflow(self):
        for sequence in ("stage:eat,neutral", "stage:neutral,eat"):
            with self.subTest(sequence=sequence):
                rows = self.run_player(sequence)
                self.assertEqual(rows[-1]["refreshes"], 1)
                self.assertTrue(rows[-1]["covered"])
        rows = self.run_player("stage:eat,reset,finish")
        self.assertEqual(rows[-1]["refreshes"], 0)

    def test_failed_new_play_preserves_previous_pending_reflow(self):
        rows = self.run_player("stage:eat,fail:play,neutral,finish")
        self.assertNotEqual(rows[1]["result"], 0)
        self.assertEqual(rows[-1]["refreshes"], 1)
        self.assertFalse(rows[-1]["mirror"])
        self.assertTrue(rows[-1]["covered"])

    def test_second_eye_play_before_first_image_retains_reflow(self):
        rows = self.run_player("eat,stage:neutral,neutral")
        self.assertTrue(rows[-1]["covered"])
        self.assertEqual(rows[-1]["refreshes"], 2)
        self.assertEqual((rows[-1]["x"], rows[-1]["y"]), (10, 110))

    def test_callback_align_and_refresh_retry_without_replaying(self):
        for fault in ("align", "refresh"):
            with self.subTest(fault=fault):
                rows = self.run_player(f"stage:eat,fail:{fault},finish")
                self.assertEqual(rows[-1]["calls"], 1)
                self.assertEqual(rows[-1]["refreshes"], 1)

    def test_callback_size_failure_retries_next_image_without_replaying(self):
        row = self.run_player("fail:getsize,eat")[-1]
        self.assertEqual(row["result"], 0)
        self.assertEqual(row["calls"], 1)
        self.assertEqual(row["refreshes"], 1)

    def test_truncated_frame_table_rejects_switch_without_mutation(self):
        members = deepcopy(self.members)
        raw = bytearray(members["winking.eaf"])
        raw[4:8] = (0xFFFFFFFF).to_bytes(4, "little")
        members["winking.eaf"] = bytes(raw)
        rows = self.run_player("eat,neutral", members)
        self.assertNotEqual(rows[1]["result"], 0)
        self.assertFalse(rows[1]["mirror"])
        self.assertEqual(rows[1]["calls"], 1)

    def test_each_failure_restores_prior_layout_and_next_play_works(self):
        for source, target in (("eat", "neutral"), ("neutral", "eat")):
            for fault in ("mirror", "position", "align", "play", "getpos", "object", "lock"):
                with self.subTest(source=source, fault=fault):
                    rows = self.run_player(f"{source},fail:{fault},{target},{target}")
                    self.assertNotEqual(rows[1]["result"], 0)
                    self.assertEqual(rows[1]["mirror"], rows[0]["mirror"])
                    self.assertEqual((rows[1]["x"], rows[1]["y"]), (rows[0]["x"], rows[0]["y"]))
                    self.assertEqual(rows[2]["result"], 0)

    def test_missing_semantic_preserves_previous_layout(self):
        rows = self.run_player("eat,missing,neutral")
        self.assertNotEqual(rows[1]["result"], 0)
        self.assertFalse(rows[1]["mirror"])
        self.assertTrue(rows[2]["mirror"])

    def test_semantic_alias_uses_actual_loaded_resource(self):
        members = deepcopy(self.members)
        index = json.loads(members["index.json"])
        next(r for r in index["emoji_collection"] if r["name"] == "neutral")["file"] = "eat.eaf"
        members["index.json"] = json.dumps(index).encode()
        self.assertTrue(self.run_player("neutral", members)[0]["full"])
        manifest = self.directory_manifest(members)
        self.assertEqual(next(r for r in manifest["entries"] if r["id"] == "neutral")["layout_id"], "full_frame")

    def test_same_size_changed_content_never_inherits_reviewed_layout(self):
        members = deepcopy(self.members)
        raw = bytearray(members["eat.eaf"])
        raw[-1] ^= 1
        members["eat.eaf"] = bytes(raw)
        row = self.run_player("eat", members)[0]
        self.assertFalse(row["full"])
        manifest = self.directory_manifest(members)
        self.assertEqual(next(r for r in manifest["entries"] if r["id"] == "eat")["layout_id"], "eye_anim")
        self.assertFalse(any(r["id"] == "full_frame" for r in manifest["entries"]))

    def test_real_manifest_changes_only_eat_route_and_adds_full_layout(self):
        manifest = self.directory_manifest()
        expected = json.loads((ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())
        entries = expected["entries"]
        next(r for r in entries if r["id"] == "eat")["layout_id"] = "full_frame"
        entries.append(dict(kind="layout", id="full_frame", align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false"))
        entries.sort(key=lambda r: (r["kind"], r["id"]))
        expected = catalog.build_manifest(entries, expected["package_sha256"], expected["index_sha256"])
        self.assertEqual(manifest, expected)
        self.assertEqual(len(manifest["entries"]), 37)

    def test_missing_eat_retains_original_other_resource_layout(self):
        members = deepcopy(self.members)
        index = json.loads(members["index.json"])
        index["emoji_collection"] = [r for r in index["emoji_collection"] if r["file"] != "eat.eaf"]
        members["index.json"] = json.dumps(index).encode()
        del members["eat.eaf"]
        row = self.run_player("neutral", members)[0]
        self.assertEqual(row["result"], 0)
        self.assertTrue(row["mirror"])

    def test_reset_removes_content_registration(self):
        row = self.run_player("neutral,reset,eat")[-1]
        self.assertFalse(row["full"])

    def test_empty_semantic_does_not_mutate_or_submit(self):
        rows = self.run_player(",eat")
        self.assertNotEqual(rows[0]["result"], 0)
        self.assertEqual(rows[0]["calls"], 0)
        self.assertTrue(rows[0]["mirror"])
        self.assertEqual(rows[1]["result"], 0)

    def test_unusable_original_layout_keeps_legacy_route(self):
        members = deepcopy(self.members)
        index = json.loads(members["index.json"])
        next(r for r in index["layout"] if r["name"] == "eye_anim")["align"] = "unknown"
        members["index.json"] = json.dumps(index).encode()
        row = self.run_player("eat", members)[0]
        self.assertFalse(row["configured"])
        self.assertFalse(row["full"])

    def test_full_frame_schema_and_python_agree_on_fixed_geometry(self):
        valid = dict(kind="layout", id="full_frame", align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false")
        schema = self.schema["$defs"]["layout"]
        for key, value in (("mirror", "auto"), ("x", 10), ("align", "GFX_ALIGN_LEFT_MID")):
            row = valid | {key: value}
            with self.assertRaises(ValueError):
                catalog.validate_entry(row)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(row, schema)


if __name__ == "__main__":
    unittest.main()
