"""Compile the production builder, mounted adapter and USB session with real cJSON/SHA-256."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

import jsonschema
from protocol import studio_catalog_audit as audit
from protocol import studio_installed_catalog as catalog
from protocol import studio_resource_package as packages
from protocol import studio_usb_session as session

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"
VENDOR = ROOT / "tests/vendor/studio_catalog"
STUBS = ROOT / "tests/cpp/catalog_stubs"
DEVICE, BOOT = "device-" + "a" * 32, "boot-" + "b" * 32
SESSION = "session-" + "b" * 32 + "-1"


def sample_index():
    return dict(version=1, emoji_collection=[
        dict(name="neutral", file="eye.eaf", eaf=dict(fps=20, loop=False)),
        dict(name="happy", file="eye.eaf", eaf=dict(fps=24, loop=True))],
        icon_collection=[dict(name="listen", file="listen.eaf")], layout=[
        dict(name="eye_anim", type="anim", align="GFX_ALIGN_LEFT_MID", x=10, y=10, anim=dict(mirror="auto")),
        dict(name="listen_anim", type="anim", align="GFX_ALIGN_TOP_MID", x=0, y=25, anim=dict(mirror="false"))])


class CatalogNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("catalog_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        if not command:
            raise RuntimeError("C++17 compiler required")
        cls.compiler = command
        cls.kind = kind
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="e3d2-native-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        includes = [STUBS, BOARD, VENDOR / "cjson", VENDOR / "mbedtls/include"]
        flags = ["-UNDEBUG", '-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"']
        for folder in includes:
            flags += ["-I", str(folder)]
        objects = []
        for name, source in (("cjson", VENDOR / "cjson/cJSON.c"), ("sha256", VENDOR / "mbedtls/library/sha256.c")):
            output = cls.folder / (name + ".o")
            c_command = command + (["cc"] if kind == "zig-c++" else ["-x", "c"])
            cls.compile(c_command + flags + ["-std=c11", "-c", str(source), "-o", str(output)])
            objects.append(str(output))
        cls.exe = cls.folder / ("catalog.exe" if os.name == "nt" else "catalog")
        cls.compile(command + (["c++"] if kind == "zig-c++" else []) + flags + [
            "-include", str(STUBS / "sdkconfig.h"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-pthread", str(BOARD / "studio_catalog_builder.cc"), str(BOARD / "studio_catalog_adapter.cc"),
            str(ROOT / "tests/cpp/studio_catalog_test.cc"), *objects, "-o", str(cls.exe)])
        cls.parser, cls.packer = packages.load_legacy_tools()
        cls.schema = json.loads((ROOT / "protocol/deskbot-studio-catalog-page-v1.schema.json").read_bytes())
        cls.validator = jsonschema.Draft202012Validator(cls.schema)

    @classmethod
    def compile(cls, command):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def fixture(self, *, index=None, raw=None, members=None):
        folder = self.folder / "fixture"
        folder.mkdir(exist_ok=True)
        for path in folder.iterdir():
            path.unlink()
        if members is None:
            members = {"eye.eaf": b"synthetic-eye-bytes", "listen.eaf": b"synthetic-listen-bytes",
                "index.json": raw if raw is not None else json.dumps(index or sample_index(), separators=(",", ":")).encode()}
        package = self.packer.build_legacy_package(members)
        for name, content in members.items():
            (folder / name).write_bytes(content)
        (folder / "package.bin").write_bytes(package)
        return folder, package

    def run_native(self, folder, mode="pages", fault="", trace=None):
        command = [str(self.exe), mode, str(folder), fault]
        if trace is not None:
            command.append(str(trace))
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()], result.stderr

    def collect(self, rows):
        self.assertTrue(rows and rows[0])
        collector = catalog.CatalogCollector(DEVICE, BOOT, SESSION)
        for row in rows:
            self.validator.validate(row)
            collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        return collector.confirmed

    def wire(self, operation="catalog.0", **changes):
        return session.encode_request(**(dict(operation=operation, request_id="query-1", target_id=DEVICE,
            boot_id=BOOT, client_nonce="client-native", session_id=SESSION) | changes))

    def trace(self, *steps):
        folder, _ = self.fixture()
        raw = bytearray()
        for step in steps:
            if step is None:
                raw += b"X"
            elif step == "invalidate":
                raw += b"I"
            elif isinstance(step, int):
                raw += b"T" + struct.pack("<Q", step)
            elif step[0] == "capacity":
                raw += b"C" + struct.pack("<I", step[1])
            elif step[0] == "provider":
                raw += b"Q" + bytes([step[1]])
            else:
                raw += b"D" + struct.pack("<QI", step[0], len(step[1])) + step[1]
        path = self.folder / "trace.bin"
        path.write_bytes(raw)
        rows, reads = self.run_native(folder, mode="trace", trace=path)
        return rows, int(reads)

    def opening(self):
        return self.wire("open", session_id=None)

    def test_vendor_bytes_and_versions_locked(self):
        record = json.loads((VENDOR / "SOURCES.json").read_bytes())
        self.assertEqual((record["cjson"], record["mbedtls"]), ("1.7.19", "3.6.6"))
        for name, digest in record["sha256"].items():
            self.assertEqual(hashlib.sha256((VENDOR / name).read_bytes()).hexdigest(), digest, name)

    def test_real_parser_and_hash_output_roundtrip(self):
        folder, package = self.fixture()
        manifest = self.collect(self.run_native(folder)[0])
        self.assertEqual(manifest["package_sha256"], packages.sha(package))
        self.assertEqual(manifest["index_sha256"], packages.sha((folder / "index.json").read_bytes()))
        row = next(r for r in manifest["entries"] if r["id"] == "neutral")
        self.assertEqual(row["resource_sha256"], packages.sha(b"synthetic-eye-bytes"))
        self.assertEqual(row["fps"], 20)

    def test_layered_package_survives_real_mounted_catalog(self):
        image=ROOT/'artifacts/layered-face-20261122/assets-8m.bin'
        members=self.parser.parse_package_bytes(packages.extract_package(image.read_bytes(),0x800000),modern=False)['members']
        folder,_=self.fixture(members=members)
        manifest=self.collect(self.run_native(folder)[0])
        semantics={row['id'] for row in manifest['entries'] if row['kind']=='expression'}
        self.assertTrue({'neutral','happy','eat','winking','confused','crying'} <= semantics)
        self.assertEqual({row['resource_id'] for row in manifest['entries'] if row['kind']=='expression'},{'face_idle.eaf'})

    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), "private saved E1.18 image absent")
    def test_actual_e1_18_bytes_match_saved_python_manifest(self):
        raw = packages.extract_package(packages.BASE_IMAGE.read_bytes(), 0x800000)
        members = self.parser.parse_package_bytes(raw, modern=False)["members"]
        folder, rebuilt = self.fixture(members=members)
        self.assertEqual(rebuilt, raw)
        expected = json.loads((ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())
        self.assertEqual(self.collect(self.run_native(folder)[0]), expected)

    def test_rejects_size_depth_tokens_trailing_and_duplicate_keys(self):
        original = json.dumps(sample_index(), separators=(",", ":")).encode()
        cases = (b"", b" " * 16385, b"[" * 13 + b"]" * 13, b"[" + b"0," * 2049 + b"0]",
            original + b"{}", original[:-1] + b',"version":1}', b"[]", b'{"version":1e999}', original + b"\0")
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                folder, _ = self.fixture(raw=raw or b" ")
                self.assertEqual(self.run_native(folder)[0], [None])

    def test_rejects_decoded_nul_and_non_ascii(self):
        original = json.dumps(sample_index()).encode()
        for raw in (original.replace(b"neutral", b"neutral\\u0000hidden"),
                    original.replace(b"neutral", b"n\\u0065utral"), original.replace(b"neutral", "平静".encode())):
            folder, _ = self.fixture(raw=raw)
            self.assertEqual(self.run_native(folder)[0], [None])

    def test_rejects_invalid_fields_and_duplicate_semantics(self):
        changes = [lambda x: x.update(version=2), lambda x: x["emoji_collection"].append(deepcopy(x["emoji_collection"][0])),
            lambda x: x["emoji_collection"][0].update(name="listen"), lambda x: x["emoji_collection"][0].update(file="../eye.eaf"),
            lambda x: x["emoji_collection"][0]["eaf"].update(fps=True), lambda x: x["emoji_collection"][0]["eaf"].update(fps=61),
            lambda x: x["emoji_collection"][0]["eaf"].update(loop=1), lambda x: x["layout"][0]["anim"].update(mirror="unknown"),
            lambda x: x["layout"][0].update(x=361), lambda x: x["layout"].pop(), lambda x: x["icon_collection"].clear()]
        for change in changes:
            index = sample_index(); change(index)
            with self.subTest(index=index):
                folder, _ = self.fixture(index=index)
                self.assertEqual(self.run_native(folder)[0], [None])

    def test_missing_loaded_assets_hashes_and_psram_fail_closed(self):
        folder, _ = self.fixture()
        for fault in ("missing_asset", "missing_loaded", "missing_icon", "loaded_fps", "loaded_loop", "loaded_pointer",
                      "loaded_size", "allocation", "checksum", "partition_size", "oversize_partition", "partition_read"):
            with self.subTest(fault=fault):
                self.assertEqual(self.run_native(folder, fault=fault)[0], [None])

    def test_odd_last_page_and_maximum_frame(self):
        index = sample_index()
        index["emoji_collection"].pop()
        folder, _ = self.fixture(index=index)
        rows = self.run_native(folder)[0]
        self.collect(rows)
        self.assertEqual(len(rows[-1]["entries"]), 1)
        self.assertIsNone(rows[-1]["next_cursor"])
        index = sample_index()
        index["emoji_collection"] = [dict(name="e" * 29 + f"{i:02}", file="eye.eaf", eaf=dict(fps=60, loop=True)) for i in range(60)]
        folder, _ = self.fixture(index=index)
        rows = self.run_native(folder)[0]
        self.collect(rows)
        self.assertLess(max(len(catalog.canonical(row)) + 96 + 1 for row in rows), 2048)

    def test_adapter_reload_generation_concurrent_copies_and_allocations(self):
        folder, _ = self.fixture()
        self.run_native(folder, mode="adapter")

    def test_session_identity_precedes_directory_read(self):
        for change in (dict(target_id="device-wrong"), dict(boot_id="boot-wrong"), dict(client_nonce="client-wrong"),
                       dict(session_id=None), dict(session_id="session-wrong")):
            rows, reads = self.trace((0, self.opening()), (500, self.wire(**change)))
            self.assertEqual((rows[-1]["state"], reads), ("rejected", 0))

    def test_session_cursor_provider_capacity_and_unload(self):
        for operation in ("catalog.1", "catalog.-2", "catalog.00", "catalog.128", "catalog.2x"):
            rows, reads = self.trace((0, self.opening()), (500, self.wire(operation)))
            self.assertEqual((rows[-1]["reason"], reads), ("unsupported_operation", 0))
        rows, reads = self.trace((0, self.opening()), ("capacity", 1024), (500, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("catalog_buffer_unavailable", 0))
        rows, reads = self.trace((0, self.opening()), "invalidate", (500, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("catalog_unavailable", 1))

    def test_session_throttle_lease_and_fragmentation(self):
        rows, reads = self.trace((0, self.opening()), (249, self.wire()), (250, self.wire()))
        self.assertEqual((len(rows), reads), (2, 1)); catalog.validate_page(rows[-1])
        rows, reads = self.trace((0, self.opening()), (30000, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("lease_expired", 0))
        wire = self.wire(request_id="r" * 96)
        rows, reads = self.trace((0, self.opening()), *((500 + i, bytes([byte])) for i, byte in enumerate(wire)))
        self.assertEqual(reads, 1); catalog.validate_page(rows[-1])
        self.assertLess(len(json.dumps(rows[-1]).encode()) + 96, 2048)

    def test_action_mirror_matches_production_source_and_component_enum(self):
        mirror = (BOARD / "studio_catalog_actions.h").read_text(encoding="utf-8")
        bindings = re.search(r"kBindings\[\] = \{(.*?)\n\};", mirror, re.S)[1]
        self.assertEqual(dict(re.findall(r'\{"(\w+)", "(\w+)"\}', bindings)),
            audit.action_bindings((ROOT / "firmware/body/main/display/emote_display.cc").read_text(encoding="utf-8")))
        values = re.search(r"kActions\[\] = \{(.*?)\n\};", mirror, re.S)[1]
        self.assertEqual(dict((k, int(v)) for k, v in re.findall(r'\{"(\w+)", (\d+)\}', values)),
                         dict(shark_head=0, shark_head_decay=1, look_around=2, beat_swing=3, cat_nuzzle=4))
        component = ROOT / "tmp/e3d2-catalog-20261105/p/managed_components/espressif2022__esp_vocat_base_control/include/vocat_base_control.h"
        if component.is_file():
            source = component.read_text(encoding="utf-8")
            for constant, name in audit.ACTION_CONSTANTS.items():
                wire = re.search(r"#define VOCAT_BASE_CMD_SET_ACTION_" + constant + r"\s+(0x[0-9A-Fa-f]+)", source)[1]
                self.assertEqual(int(wire, 16), dict(shark_head=0, shark_head_decay=1, look_around=2, beat_swing=3, cat_nuzzle=4)[name])


if __name__ == "__main__":
    unittest.main()
