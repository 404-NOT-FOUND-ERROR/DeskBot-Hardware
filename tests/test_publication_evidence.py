"""An archive may evolve only through exact, current source records."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.publication_evidence import SourceReview


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="source-evidence-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "source.py").write_bytes(b"current")
        self.old = {"result": "passed", "sources": [{"path": "source.py", "sha256": sha(b"old")}]}
        self.new = {"result": "passed", "sources": [{"path": "source.py", "sha256": sha(b"current")}]}
        (self.root / "old.json").write_bytes(encoded(self.old))
        (self.root / "new.json").write_bytes(encoded(self.new))
        self.review = {"schema": "deskbot.source_review/v1", "edition": "E3d-2",
            "current_report": "new.json", "reports": [{"path": "old.json", "sha256": sha(encoded(self.old)),
                "changes": [{"path": "source.py", "before_sha256": sha(b"old"),
                    "after_sha256": sha(b"current"), "reason": "catalog extension"}]}]}
        self.save()

    def save(self):
        (self.root / "review.json").write_bytes(encoded(self.review))

    def load(self):
        return SourceReview(self.root, "review.json")

    def test_reviewed_revision_and_report_reproduce(self):
        review = self.load()
        self.assertTrue(review.matches("old.json", "source.py", sha(b"old")))
        self.assertTrue(review.product_matches(encoded(self.old), encoded(self.new), "old.json"))
        self.assertEqual((self.root / "old.json").read_bytes(), encoded(self.old))

    def test_other_result_fields_and_unreviewed_digests_are_rejected(self):
        review = self.load()
        self.assertFalse(review.product_matches(encoded(self.old), encoded(self.new | {"result": "changed"}), "old.json"))
        self.assertFalse(review.matches("unreviewed.json", "source.py", sha(b"old")))
        self.assertFalse(review.matches("old.json", "source.py", sha(b"different")))

    def test_modified_archive_is_rejected(self):
        (self.root / "old.json").write_bytes(encoded(self.old | {"result": "modified"}))
        with self.assertRaisesRegex(ValueError, "archived report changed"):
            self.load()

    def test_modified_current_source_or_current_record_is_rejected(self):
        (self.root / "source.py").write_bytes(b"unreviewed change")
        with self.assertRaisesRegex(ValueError, "source revision evidence differs"):
            self.load()
        (self.root / "source.py").write_bytes(b"current")
        (self.root / "new.json").write_bytes(encoded(self.old))
        with self.assertRaisesRegex(ValueError, "source revision evidence differs"):
            self.load()

    def test_missing_original_digest_and_duplicate_records_are_rejected(self):
        self.review["reports"][0]["changes"][0]["before_sha256"] = sha(b"never recorded")
        self.save()
        with self.assertRaises(ValueError):
            self.load()
        self.review["reports"][0]["changes"][0]["before_sha256"] = sha(b"old")
        self.review["reports"] *= 2
        self.save()
        with self.assertRaisesRegex(ValueError, "duplicate archived report"):
            self.load()

    def test_evidence_paths_stay_inside_repository(self):
        self.review["current_report"] = "../outside.json"
        self.save()
        with self.assertRaisesRegex(ValueError, "outside repository"):
            self.load()

    def test_later_edition_has_its_own_exact_review_and_preserves_history(self):
        historical = (self.root / "review.json").read_bytes()
        intermediate = self.new
        (self.root / "intermediate.json").write_bytes(encoded(intermediate))
        (self.root / "source.py").write_bytes(b"later")
        latest = {"result": "passed", "sources": [{"path": "source.py", "sha256": sha(b"later")}]}
        (self.root / "latest.json").write_bytes(encoded(latest))
        review = {"schema": "deskbot.source_review/v1", "edition": "E3e",
            "current_report": "latest.json", "reports": []}
        for name, document in (("old.json", self.old), ("intermediate.json", intermediate)):
            review["reports"].append({"path": name, "sha256": sha(encoded(document)),
                "changes": [{"path": "source.py", "before_sha256": document["sources"][0]["sha256"],
                    "after_sha256": sha(b"later"), "reason": "reviewed later integration"}]})
        (self.root / "later-review.json").write_bytes(encoded(review))
        current = SourceReview(self.root, "later-review.json")
        self.assertTrue(current.product_matches(encoded(self.old), encoded(latest), "old.json"))
        self.assertTrue(current.matches("intermediate.json", "source.py", sha(b"current")))
        self.assertFalse(current.matches("unlisted.json", "source.py", sha(b"old")))
        self.assertEqual((self.root / "review.json").read_bytes(), historical)
        self.assertEqual((self.root / "old.json").read_bytes(), encoded(self.old))

    def test_historical_review_reproduces_recorded_endpoints(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("historical_source_review_test",
            root / "scripts/build-e3d2-source-review.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertEqual(builder.products(), (root / builder.REVIEW).read_bytes())

    def test_e3e_review_reproduces_recorded_endpoints_after_later_source_changes(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("historical_e3e_source_review_test",
            root / "scripts/build-e3e-source-review.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertEqual(builder.products(), (root / builder.REVIEW).read_bytes())
        builder.SourceReview(review=builder.REVIEW, verify_sources=False)

    def test_reviewed_source_digest_map_reproduces_without_rewriting_archive(self):
        old = {"result": "passed", "source_sha256": {"source.py": sha(b"old")}}
        new = {"result": "passed", "source_sha256": {"source.py": sha(b"current")}}
        (self.root / "old-map.json").write_bytes(encoded(old))
        (self.root / "new-map.json").write_bytes(encoded(new))
        self.review["current_report"] = "new-map.json"
        self.review["reports"] = [{"path": "old-map.json", "sha256": sha(encoded(old)),
            "changes": [{"path": "source.py", "before_sha256": sha(b"old"),
                "after_sha256": sha(b"current"), "reason": "state query review"}]}]
        self.save()
        review = self.load()
        self.assertTrue(review.product_matches(encoded(old), encoded(new), "old-map.json"))
        self.assertEqual((self.root / "old-map.json").read_bytes(), encoded(old))

    def test_source_digest_map_rejects_shape_or_unreviewed_change(self):
        review = self.load()
        old = {"result": "passed", "source_sha256": {"source.py": sha(b"old")}}
        changed = {"result": "passed", "source_sha256": {"other.py": sha(b"current")}}
        self.assertFalse(review.product_matches(encoded(old), encoded(changed), "old.json"))
        changed = {"result": "passed", "source_sha256": {"source.py": sha(b"different")}}
        self.assertFalse(review.product_matches(encoded(old), encoded(changed), "old.json"))


if __name__ == "__main__":
    unittest.main()
