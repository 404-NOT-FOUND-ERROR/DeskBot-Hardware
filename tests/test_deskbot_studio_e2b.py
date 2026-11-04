from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protocol import studio_resource_package as P

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


B = module("studio_e2b_builder_test", "scripts/build-deskbot-studio-e2b.py")
S = module("studio_e2b_server_test", "scripts/serve-deskbot-studio-e2b.py")


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = P.load_catalog()

    def test_utf8_bom_and_chinese_round_trip(self):
        draft = self.catalog["initial"]
        self.assertEqual(P.parse_draft(b"\xef\xbb\xbf" + P.json_bytes(draft)), draft)

    def test_duplicate_json_keys_and_non_finite_values_rejected(self):
        for raw in (b'{"roles":[],"roles":[]}', b'{"revision":NaN}', b'{"revision":Infinity}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                P.parse_draft(raw)

    def test_oversized_draft_rejected(self):
        with self.assertRaisesRegex(ValueError, "512"):
            P.parse_draft(b" " * (P.MAX_DRAFT_BYTES + 1))

    def test_invalid_drafts_rejected(self):
        mutations = {
            "catalog": lambda d: d.update(catalog_id="0" * 64),
            "scope": lambda d: d.update(scope="device"),
            "boolean revision": lambda d: d.update(revision=True),
            "revision range": lambda d: d.update(revision=9007199254740992),
            "unknown field": lambda d: d.update(device="COM4"),
            "missing semantic": lambda d: d["roles"][0]["bindings"].pop("happy"),
            "unconnected": lambda d: d["roles"][0]["bindings"].update(happy=None),
            "unknown asset": lambda d: d["roles"][0]["bindings"].update(happy="other.eaf"),
            "eye versus status": lambda d: d["roles"][0]["bindings"].update(listen="Happy.eaf"),
            "status versus eye": lambda d: d["roles"][0]["bindings"].update(happy="listen.eaf"),
            "duplicate role": lambda d: d["roles"].append(copy.deepcopy(d["roles"][0])),
            "missing role": lambda d: d.update(selected_role_id="missing"),
        }
        for label, mutation in mutations.items():
            draft = copy.deepcopy(self.catalog["initial"])
            mutation(draft)
            with self.subTest(label=label), self.assertRaises(ValueError):
                P.validate_draft(draft, self.catalog)

    def test_only_selected_role_affects_package_input(self):
        draft = copy.deepcopy(self.catalog["initial"])
        before = P.selected_input(draft, self.catalog)
        draft["revision"] += 1
        draft["roles"][1]["name"] = "另一个角色"
        self.assertEqual(P.selected_input(draft, self.catalog), before)
        draft["roles"][0]["name"] = "当前角色"
        self.assertNotEqual(P.selected_input(draft, self.catalog), before)

    def test_target_matches_existing_upper_layout(self):
        target = P.load_target()
        self.assertEqual(target["assets"], {"name": "assets", "offset": 0x1000000,
                         "bytes": 0x800000, "soft_limit_bytes": 0x700000})
        self.assertEqual(target["flash_bytes"], 32 * 1024 * 1024)
        self.assertEqual(target["psram_bytes"], 16 * 1024 * 1024)

    def test_public_build_reproduces_saved_page(self):
        first = B.products()
        self.assertEqual(first, B.products())
        for name, raw in first.items():
            self.assertEqual((B.PUBLIC / name).read_bytes(), raw)
        self.assertNotIn(b"__STUDIO_", first["index.html"])
        self.assertIn(b'package-report-file', first["index.html"])
        self.assertLess(first["index.html"].index(b'<input id="package-report-file"'),
                        first["index.html"].index(b'  release.init();'))
        self.assertIn(b'"atlas":null', first["index.html"])


@unittest.skipUnless(P.BASE_IMAGE.is_file() and P.CANDIDATE.is_file(), "retained E1.18/E1.45 resources required")
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.target = P.load_catalog(), P.load_target()
        cls.baseline = P.BASE_IMAGE.read_bytes()
        cls.candidate = P.CANDIDATE.read_bytes()

    def assemble(self, draft=None, baseline=None, candidate=None):
        return P.assemble(draft or self.catalog["initial"], self.catalog, self.target,
                          self.baseline if baseline is None else baseline,
                          {"shy_candidate.eaf": self.candidate if candidate is None else candidate})

    def test_default_reproduces_exact_accepted_image(self):
        report, package, image = self.assemble()
        self.assertEqual(image, self.baseline)
        self.assertEqual(P.sha(package), P.BASE_PACKAGE_SHA)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["package"]["bytes"], 5538351)
        self.assertFalse(report["device_written"])

    def test_candidate_reproduces_independently_audited_e145_image(self):
        draft = copy.deepcopy(self.catalog["initial"])
        draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        report, package, image = self.assemble(draft)
        reference = json.loads((ROOT / "artifacts/expression-e1-45-staging-20261104/report.json").read_bytes())
        self.assertEqual(report["package"]["bytes"], reference["candidate"]["package_bytes"])
        self.assertEqual(P.sha(package), reference["candidate"]["package_sha256"])
        self.assertEqual(P.sha(image), reference["candidate"]["partition_sha256"])
        self.assertEqual(report["members"]["added"], ["shy_candidate.eaf"])
        self.assertEqual([row["semantic"] for row in report["changes"]], ["embarrassed"])

    def test_shared_candidate_is_stored_once_and_other_index_paths_preserved(self):
        draft = copy.deepcopy(self.catalog["initial"])
        draft["roles"][0]["bindings"].update(embarrassed="shy_candidate.eaf", happy="shy_candidate.eaf")
        report, package, _ = self.assemble(draft)
        resources, _ = P.load_legacy_tools()
        before = resources.parse_package_bytes(P.extract_package(self.baseline, 0x800000), modern=False)["members"]
        after = resources.parse_package_bytes(package, modern=False)["members"]
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(report["members"]["added"], ["shy_candidate.eaf"])
        old_index, new_index = json.loads(before["index.json"]), json.loads(after["index.json"])
        self.assertEqual({k: v for k, v in new_index.items() if k != "emoji_collection"},
                         {k: v for k, v in old_index.items() if k != "emoji_collection"})
        for name in set(before) - {"index.json"}:
            self.assertEqual(after[name], before[name])
        for row in new_index["emoji_collection"]:
            if row["name"] not in ("happy", "embarrassed"):
                self.assertIn(row, old_index["emoji_collection"])

    def test_existing_animation_rewire_changes_only_selected_index(self):
        draft = copy.deepcopy(self.catalog["initial"])
        draft["roles"][0]["bindings"]["happy"] = "Sad.eaf"
        report, _, _ = self.assemble(draft)
        self.assertEqual(report["members"]["added"], [])
        self.assertEqual(report["members"]["changed"], ["index.json"])
        self.assertEqual(report["changes"][0]["after"]["file"], "Sad.eaf")

    def test_corrupt_baseline_and_candidate_rejected(self):
        with self.assertRaisesRegex(ValueError, "镜像摘要"):
            self.assemble(baseline=self.baseline[:-1] + b"\x00")
        draft = copy.deepcopy(self.catalog["initial"])
        draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        with self.assertRaisesRegex(ValueError, "资源内容"):
            self.assemble(draft, candidate=self.candidate[:-1] + b"\x00")

    def test_repeated_output_reuses_bytes_and_rejects_corruption(self):
        report, package, image = self.assemble()
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="studio-e2b-test-") as output:
            first = P.persist(report, package, image, output)
            self.assertEqual(first, P.persist(report, package, image, output))
            (first / "expression-package.bin").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "不符"):
                P.persist(report, package, image, output)


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="studio-e2b-http-test-")
        cls.server = S.StudioServer(0, output=cls.temp.name, local=False)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def request(self, path, value=None, headers=None):
        raw = None if value is None else P.json_bytes(value)
        request = Request(self.origin + path, data=raw,
                          headers=headers or {"Content-Type": "application/json", "X-DeskBot-Studio": "e2b"})
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_listener_and_health(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, raw, _ = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["target_id"], P.TARGET_ID)

    def test_only_workbench_and_package_paths_are_served(self):
        status, raw, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertEqual(raw, S.PUBLIC.read_bytes())
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/README.md", "/protocol/studio_resource_package.py", "/../README.md",
                     "/packages/" + "0" * 64 + "/sdkconfig", "/api/flash"):
            self.assertEqual(self.request(path)[0], 404)

    def test_foreign_host_and_origin_rejected(self):
        for headers in ({"Host": "example.org"}, {"Origin": "https://example.org"}):
            self.assertEqual(self.request("/api/health", headers=headers)[0], 403)

    def test_invalid_post_does_not_create_output(self):
        self.assertEqual(self.request("/api/package", {"invalid": True})[0], 422)
        self.assertEqual(self.request("/api/package", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    @unittest.skipUnless(P.BASE_IMAGE.is_file(), "retained E1.18 resources required")
    def test_package_response_downloads_exact_generated_image(self):
        status, raw, _ = self.request("/api/package", self.server.catalog["initial"])
        self.assertEqual(status, 200, raw)
        value = json.loads(raw)
        status, image, headers = self.request(value["downloads"]["expression-assets-8m.bin"])
        self.assertEqual(status, 200)
        self.assertEqual(P.sha(image), P.BASE_IMAGE_SHA)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertFalse(value["report"]["hardware_accessed"])


if __name__ == "__main__":
    (ROOT / "tmp").mkdir(exist_ok=True)
    unittest.main()
