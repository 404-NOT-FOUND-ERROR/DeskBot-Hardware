"""Saved-package audit, source separation and workbench generation."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import studio_installed_catalog as c
from protocol import studio_catalog_audit as audit
from protocol import studio_resource_package as packages

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/deskbot-studio-e3d1-20261105"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def public_data():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)[1])


class DirectoryTests(unittest.TestCase):
    def test_actual_directory_counts_and_shared_resources(self):
        manifest = c.validate_manifest(json.loads((PUBLIC / "installed-directory.json").read_bytes()))
        rows = manifest["entries"]
        self.assertEqual(len(rows), 36)
        self.assertEqual(sum(r["kind"] == "expression" for r in rows), 28)
        self.assertEqual(sum(r["kind"] == "status" for r in rows), 1)
        self.assertEqual(sum(r["kind"] == "action" for r in rows), 5)
        self.assertEqual(sum(bool(r.get("implicit_action_id")) for r in rows), 18)
        self.assertEqual(len({r["resource_id"] for r in rows if "resource_id" in r}), 18)

    def test_saved_directory_and_live_control_catalog_ids_are_separate(self):
        data = public_data()
        saved = data["installed_directory"]
        self.assertEqual(saved["source"], "verified_package_snapshot")
        self.assertFalse(saved["live"])
        self.assertNotEqual(saved["manifest"]["catalog_id"], data["expression_catalog"]["catalog_id"])
        self.assertEqual(saved["usb"]["supported_expression_ids"], ["neutral"])
        self.assertEqual(saved["usb"]["supported_action_ids"], [])
        self.assertFalse(saved["usb"]["device_catalog_query"])

    def test_installed_surprised_mapping_wins_over_source_default(self):
        data = public_data()["installed_directory"]
        surprised = next(r for r in data["manifest"]["entries"] if r["id"] == "surprised")
        self.assertEqual(surprised["resource_id"], "shocked.eaf")
        self.assertEqual(data["audit"]["default_differences"], [
            dict(semantic_id="surprised", source_default="Happy.eaf", installed="shocked.eaf")])

    def test_action_map_is_extracted_from_production_source(self):
        source = (ROOT / "firmware/body/main/display/emote_display.cc").read_text(encoding="utf-8")
        mapping = audit.action_bindings(source)
        rows = public_data()["installed_directory"]["manifest"]["entries"]
        actual = {r["id"]: r["implicit_action_id"] for r in rows if r.get("implicit_action_id")}
        self.assertEqual(mapping, actual)
        self.assertEqual(mapping["embarrassed"], "cat_nuzzle")

    def test_changed_action_source_requires_review(self):
        for source in ("", 'emotion_to_action_map = {\n {"happy", VOCAT_BASE_CMD_SET_ACTION_NEW},\n};',
                'emotion_to_action_map = {\n make_dynamic_mapping(),\n};'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                audit.action_bindings(source)

    def test_fixtures_reassemble_and_fit_existing_frame_bound(self):
        fixture = json.loads((PUBLIC / "catalog-page-fixtures.json").read_bytes())
        self.assertFalse(fixture["live"])
        rows = fixture["pages"]
        collector = c.CatalogCollector(**{k: rows[0][k] for k in ("target_id", "boot_id", "session_id")})
        self.assertEqual(len(rows), 18)
        for row in rows:
            self.assertLessEqual(len(c.canonical(row)) + 1, 2048)
            collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        self.assertEqual(collector.confirmed, public_data()["installed_directory"]["manifest"])

    def test_resource_fingerprints_match_preview_inputs(self):
        data = public_data()
        for row in data["installed_directory"]["manifest"]["entries"]:
            if "resource_id" in row:
                preview = data["assets"][row["resource_id"]]
                self.assertEqual(row["resource_sha256"], preview["deployed_sha256"])
                self.assertEqual(row["bytes"], preview["bytes"])

    def test_public_snapshot_contains_no_candidate_as_installed(self):
        data = public_data()
        for row in data["installed_directory"]["manifest"]["entries"]:
            if "resource_id" in row:
                self.assertEqual(data["assets"][row["resource_id"]]["origin"], "official")
        self.assertIn('id="installed-directory-slot"', (PUBLIC / "index.html").read_text(encoding="utf-8"))

    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), "private saved resource image absent")
    def test_local_and_public_generation_reproduce(self):
        builder = module("e3d_generation_test", "scripts/build-deskbot-studio-e3d.py")
        for local, folder in ((False, builder.PUBLIC), (True, builder.LOCAL)):
            if local and not folder.is_dir():
                continue
            for name, raw in builder.products(local).items():
                self.assertEqual((folder / name).read_bytes(), raw, name)

    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), "private saved resource image absent")
    def test_bad_index_and_changed_side_effect_are_rejected(self):
        parser, _ = packages.load_legacy_tools()
        members = parser.parse_package_bytes(packages.extract_package(packages.BASE_IMAGE.read_bytes(), 0x800000), modern=False)["members"]
        saved = json.loads((PUBLIC / "expression-catalog.json").read_bytes())
        defaults = json.loads((ROOT / "firmware/body/main/boards/esp_vocat/assets/360_360/emote.json").read_bytes())
        source = (ROOT / "firmware/body/main/display/emote_display.cc").read_text(encoding="utf-8")
        index = json.loads(members["index.json"])
        index["emoji_collection"][0]["file"] = "shocked.eaf"
        with self.assertRaises(ValueError):
            audit.audit(members | {"index.json": json.dumps(index).encode()}, saved, defaults, source)
        source = source.replace('{"happy",       VOCAT_BASE_CMD_SET_ACTION_SHARK_HEAD}',
                                '{"happy",       VOCAT_BASE_CMD_SET_ACTION_CAT_NUZZLE}')
        with self.assertRaises(ValueError):
            audit.audit(members, saved, defaults, source)


class ServiceTests(unittest.TestCase):
    def test_new_page_uses_existing_owner_without_connecting(self):
        server_module = module("e3d_service_test", "scripts/serve-deskbot-studio-e3d.py")
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="e3d-http-", dir=ROOT / "tmp") as temp:
            server = server_module.make_server(0, local=False, output=temp)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                opener = build_opener(ProxyHandler({}))
                with opener.open(origin + "/", timeout=5) as response:
                    self.assertIn(b'E3d', response.read())
                self.assertIsNone(server.bridge.process)
                with opener.open(origin + "/api/studio/state", timeout=5) as response:
                    row = json.load(response)
                    self.assertFalse(row["enabled"])
                    self.assertEqual(row["frames_sent"], 0)
                with self.assertRaises(HTTPError) as caught:
                    opener.open(Request(origin + "/api/studio/state", headers={"Origin": "https://example.org"}), timeout=5)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
