"""Offline tests for expression/action export and safe candidate validation."""
from __future__ import annotations

import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "expression_capabilities", ROOT / "tools/export-expression-capabilities.py"
)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)

BODY = ROOT / "firmware" / "body"


def make_eaf(path: Path, *, checksum_delta: int = 0) -> None:
    # Minimal valid _S frame: 24-bit 1x1 image, one RAW block.  This mirrors
    # the header layout consumed by gfx_eaf_dec.c without requiring an image
    # encoder in the host test.
    frame_data = bytearray(b"_S" + b"\0" + b"000000" + bytes([24]))
    frame_data += struct.pack("<HHHH", 1, 1, 1, 1)
    frame_data += struct.pack("<I", 2)  # encoding byte + one raw byte
    frame_data += bytes([5, 0])         # EAF_ENCODING_RAW
    frame = b"ZZ" + bytes(frame_data)
    table = struct.pack("<II", len(frame), 0)
    payload = table + frame
    checksum = (sum(payload) + checksum_delta) & 0xFFFFFFFF
    raw = b"\x89EAF" + struct.pack("<III", 1, checksum, len(payload)) + payload
    path.write_bytes(raw)


def make_overlapping_eaf(path: Path) -> None:
    first_path = path.with_name("_first.eaf")
    make_eaf(first_path)
    frame = first_path.read_bytes()[24:]
    first_path.unlink()
    second_offset = 28
    combined = frame + (b"\0" * (second_offset - len(frame))) + frame
    table = struct.pack("<II", len(combined), 0)
    table += struct.pack("<II", len(frame), second_offset)
    payload = table + combined
    raw = b"\x89EAF" + struct.pack("<III", 2, sum(payload) & 0xFFFFFFFF, len(payload)) + payload
    path.write_bytes(raw)


class ExpressionCapabilitiesTests(unittest.TestCase):
    def test_export_official_inventory(self) -> None:
        source = BODY
        if not (source / "main/boards/esp_vocat/assets/360_360/emote.json").is_file():
            self.skipTest("official source checkout is unavailable")
        report = MOD.export_capabilities(source)
        self.assertEqual(report["schema"], MOD.SCHEMA)
        self.assertEqual(len(report["expressions"]), 28)
        self.assertTrue(all("sha256" in item and "eaf" in item for item in report["expressions"]))
        self.assertFalse(report["runtime"]["activation_supported"])
        self.assertEqual(report["provenance"]["assets_partition_bytes"], 6_144_000)
        # Git provenance describes the caller's checkout.  A cloned repository,
        # a source archive, and a dirty development tree are all valid inputs.
        self.assertIn(report["provenance"]["tracked_inputs_clean"], (True, False, None))
        self.assertIn(report["provenance"]["source_dirty"], (True, False, None))

    def test_default_source_is_the_packaged_body(self) -> None:
        self.assertEqual(MOD.find_source_root(None), BODY.resolve())

    def test_default_capacity_matches_actual_official_partition_csv(self) -> None:
        source = BODY
        if not (source / "partitions/v2/16m.csv").is_file():
            self.skipTest("official partition CSV is unavailable")
        self.assertEqual(MOD.assets_partition_bytes(source), 6_144_000)
        self.assertEqual(MOD.DEFAULT_ASSETS_PARTITION_BYTES,
                         MOD.assets_partition_bytes(source))

    def test_tracked_input_status_checks_head_and_reports_changes(self) -> None:
        source = Path("source")
        change = "M\tmain/display/emote_display.cc"
        with patch.object(MOD.subprocess, "check_output", return_value=change + "\n") as read:
            report = MOD.tracked_expression_input_status(source)
        self.assertFalse(report["tracked_inputs_clean"])
        self.assertEqual(report["tracked_input_changes"], [change])
        command = read.call_args.args[0]
        self.assertEqual(command[:7], ["git", "-C", str(source), "diff", "HEAD",
                                       "--name-status", "--"])
        self.assertIn("main/boards/esp_vocat/assets", command)
        self.assertIn("main/display/emote_display.cc", command)
        self.assertIn("main/boards/esp_vocat/dev_tools.cc", command)

    def test_import_replace_alias_and_custom_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "new.eaf")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [{"id": "chef_smile", "src": "new.eaf", "loop": False, "fps": 20}],
                "bindings": [{"id": "happy", "target": "chef_smile", "mode": "replace"}],
                "actions": [{"id": "chef_greet", "steps": [{"action": "look_around", "duration_ms": 500}]}],
            }), encoding="utf-8")
            report = MOD.validate_manifest(manifest, root, False)
            self.assertEqual(report["result"], "offline_validated")
            self.assertEqual(report["bindings"][0]["target"], "chef_smile")
            self.assertEqual(report["bindings"][0]["mode"], "replace")

    def test_reject_protected_traversal_duplicate_bad_checksum_and_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "good.eaf")
            make_eaf(root / "bad.eaf", checksum_delta=1)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [
                    {"id": "x", "src": "good.eaf", "loop": True, "fps": 20},
                    {"id": "x", "src": "bad.eaf", "loop": True, "fps": 20},
                    {"id": "escape", "src": "../good.eaf", "loop": True, "fps": 20},
                ],
                "bindings": [{"id": "sleepy", "target": "x"}],
                "actions": [{"id": "bad", "steps": [{"action": "raw_uart", "duration_ms": 1}]}],
            }), encoding="utf-8")
            report = MOD.validate_manifest(manifest, root, False)
            self.assertEqual(report["result"], "rejected")
            messages = " ".join(error["error"] for error in report["errors"])
            self.assertIn("duplicate", messages)
            self.assertIn("traversal", messages)
            self.assertIn("protected", messages)
            self.assertIn("unknown base action", messages)

    def test_bad_eaf_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.eaf"
            make_eaf(path, checksum_delta=1)
            with self.assertRaises(ValueError):
                MOD.parse_eaf(path)

    def test_reject_truncated_unknown_encoding_and_partial_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.eaf"
            make_eaf(good)
            raw = bytearray(good.read_bytes())
            # Keep checksum valid while making the block encoding unknown.
            frame_start = 16 + 8 + 2
            raw[frame_start + 22] = 0xFF
            checksum = sum(raw[16:]) & 0xFFFFFFFF
            struct.pack_into("<I", raw, 8, checksum)
            (root / "unknown.eaf").write_bytes(raw)
            with self.assertRaises(ValueError):
                MOD.parse_eaf(root / "unknown.eaf")

            truncated = root / "truncated.eaf"
            truncated.write_bytes(good.read_bytes()[:-1])
            with self.assertRaises(ValueError):
                MOD.parse_eaf(truncated)

            overlapping = root / "overlapping.eaf"
            make_overlapping_eaf(overlapping)
            with self.assertRaises(ValueError):
                MOD.parse_eaf(overlapping)

    def test_semantic_tool_actions_are_not_raw_composition_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "new.eaf")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [{"id": "x", "src": "new.eaf", "loop": False, "fps": 20}],
                "actions": [{"id": "bad_composition", "steps": [{"action": "calibrate", "duration_ms": 0}]}],
            }), encoding="utf-8")
            report = MOD.validate_manifest(manifest, root, False)
            self.assertEqual(report["result"], "rejected")
            self.assertIn("unknown base action", report["errors"][0]["error"])

    def test_reject_illegal_names_and_non_eaf_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "good.eaf")
            (root / "note.txt").write_text("not an animation", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [
                    {"id": "bad id", "src": "good.eaf", "loop": False, "fps": 20},
                    {"id": "bad-src", "src": "note.txt", "loop": False, "fps": 20},
                    {"id": "bad-nul", "src": "good\u0000.eaf", "loop": False, "fps": 20},
                ],
                "bindings": [{"id": "bad id", "target": "bad-src"}],
                "actions": [{"id": "bad id", "steps": [{"action": "look_around"}]}],
            }), encoding="utf-8")
            report = MOD.validate_manifest(manifest, root, False)
            self.assertEqual(report["result"], "rejected")
            messages = " ".join(error["error"] for error in report["errors"])
            self.assertIn("illegal name", messages)
            self.assertIn("EAF", messages)

    def test_package_capacity_and_prebuilt_binary_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "good.eaf"
            make_eaf(asset)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [{"id": "x", "src": "good.eaf", "loop": False, "fps": 20}],
            }), encoding="utf-8")

            source_report = MOD.validate_manifest(manifest, root, False,
                                                  max_package_bytes=asset.stat().st_size)
            self.assertEqual(source_report["result"], "offline_validated")
            self.assertEqual(source_report["package"]["measured_bytes"], asset.stat().st_size)
            self.assertTrue(source_report["package"]["source_files_checked"])
            self.assertFalse(source_report["package"]["built_package_capacity_verified"])
            self.assertEqual(source_report["package"]["measurement_kind"], "source_asset_sum")

            oversized_report = MOD.validate_manifest(manifest, root, False,
                                                      max_package_bytes=asset.stat().st_size - 1)
            self.assertEqual(oversized_report["result"], "rejected")
            self.assertIn("exceeds limit", oversized_report["errors"][-1]["error"])

            package_bin = root / "assets.bin"
            package_bin.write_bytes(b"built-package")
            binary_report = MOD.validate_manifest(manifest, root, False,
                                                   max_package_bytes=package_bin.stat().st_size,
                                                   package_bin=package_bin)
            self.assertEqual(binary_report["result"], "offline_validated")
            self.assertEqual(binary_report["package"]["package_binary_bytes"], package_bin.stat().st_size)
            self.assertEqual(binary_report["package"]["measured_bytes"], package_bin.stat().st_size)
            self.assertTrue(binary_report["package"]["source_files_checked"])
            self.assertTrue(binary_report["package"]["built_package_capacity_verified"])
            self.assertEqual(binary_report["package"]["measurement_kind"], "built_package")
            oversized_package = MOD.validate_manifest(
                manifest, root, False, max_package_bytes=package_bin.stat().st_size - 1,
                package_bin=package_bin,
            )
            self.assertEqual(oversized_package["result"], "rejected")
            self.assertFalse(oversized_package["package"]["built_package_capacity_verified"])

            with self.assertRaises(ValueError):
                MOD.validate_manifest(manifest, root, False,
                                       package_bin=root / "missing-assets.bin")
            with self.assertRaises(ValueError):
                MOD.validate_manifest(manifest, root, False, package_bin=root)
            package_bin.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "empty"):
                MOD.validate_manifest(manifest, root, False, package_bin=package_bin)

    def test_partition_size_parser_and_type_rejections(self) -> None:
        self.assertEqual(MOD._partition_size_bytes("6000K"), 6_144_000)
        self.assertEqual(MOD._partition_size_bytes("1M"), 1_048_576)
        with self.assertRaises(ValueError):
            MOD._partition_size_bytes("6000KiB")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "good.eaf")
            manifest = root / "manifest.json"
            for value in (20.0, True, "20", None):
                with self.subTest(value=value):
                    manifest.write_text(json.dumps({
                        "expressions": [{"id": "x", "src": "good.eaf", "loop": False, "fps": value}],
                        "actions": [{"id": "move", "steps": [{"action": "look_around", "duration_ms": value}]}],
                    }), encoding="utf-8")
                    report = MOD.validate_manifest(manifest, root, False)
                    messages = " ".join(error["error"] for error in report["errors"])
                    self.assertEqual(report["result"], "rejected")
                    self.assertIn("fps must be an integer", messages)
                    self.assertIn("step duration must be an integer", messages)

    def test_reject_manifest_schema_and_collection_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            for data in ([], None, {"schema": "unsupported/v2"}, {"schema": None},
                         {"schema": {}}, {"expressions": {}}, {"expressions": None}):
                with self.subTest(data=data):
                    manifest.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        MOD.validate_manifest(manifest, root, False)
            for data in ({"bindings": {}}, {"actions": {}}, {"bindings": [False]},
                         {"actions": [False]}, {"expressions": [False]}):
                with self.subTest(data=data):
                    manifest.write_text(json.dumps(data), encoding="utf-8")
                    report = MOD.validate_manifest(manifest, root, False)
                    self.assertEqual(report["result"], "rejected")
            manifest.write_text(json.dumps({"schema": MOD.SCHEMA}), encoding="utf-8")
            self.assertEqual(MOD.validate_manifest(manifest, root, False)["result"],
                             "offline_validated")

    def test_invalid_expression_cannot_be_a_binding_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_eaf(root / "bad.eaf", checksum_delta=1)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "expressions": [{"id": "invalid", "src": "bad.eaf", "loop": False, "fps": 20}],
                "bindings": [{"id": "happy", "target": "invalid", "mode": "replace"}],
            }), encoding="utf-8")
            report = MOD.validate_manifest(manifest, root, False)
            self.assertEqual(report["result"], "rejected")
            self.assertEqual(report["expressions"], [])
            self.assertEqual(report["bindings"], [])
            self.assertFalse(report["package"]["source_files_checked"])
            self.assertEqual(report["package"]["imported_asset_bytes"], 0)
            self.assertIn("binding target", report["errors"][-1]["error"])

    def test_reject_symlink_escape_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = Path(tmp).parent / (Path(tmp).name + "-outside")
            outside.mkdir()
            try:
                make_eaf(outside / "outside.eaf")
                link = root / "linked"
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self.skipTest("directory symlinks are unavailable")
                with self.assertRaises(ValueError):
                    MOD.safe_asset(root, "linked/outside.eaf")
            finally:
                for path in outside.glob("*"):
                    path.unlink()
                outside.rmdir()


if __name__ == "__main__":
    unittest.main()
