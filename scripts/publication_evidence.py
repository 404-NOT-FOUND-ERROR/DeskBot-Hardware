"""Compare archived source records with explicitly reviewed later revisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVIEW = "artifacts/deskbot-studio-e3d2-20261105/source-review.json"
E3E_REVIEW = "artifacts/deskbot-studio-e3e-20261108/source-review.json"
REVIEW = "artifacts/publication-20261127/source-review.json"


def source_pairs(report):
    pairs = set(report.get("source_sha256", {}).items())
    pairs.update(report.get("inputs", {}).get("public_source_sha256", {}).items())
    pairs.update((row["path"], row["sha256"]) for row in report.get("sources", []))
    pairs.update((row["path"], row["sha256"]) for row in report.get("source_evidence", [])
                 if row.get("origin") == "repo")
    return pairs


class SourceReview:
    def __init__(self, root=ROOT, review=REVIEW, verify_sources=True):
        self.root = Path(root).resolve()
        self.verify_sources = verify_sources
        data = json.loads(self.path(review).read_bytes())
        if (data.get("schema") != "deskbot.source_review/v1"
                or data.get("edition") not in {"E3d-2", "E3e", "PUB-2026-11-22", "PUB-2026-11-27"}):
            raise ValueError("unexpected source review edition")
        current = source_pairs(json.loads(self.path(data["current_report"]).read_bytes()))
        self.current = dict(current)
        self.current_report = self.relative(data["current_report"])
        self.changes = set()
        self.report_paths = set()
        for report in data["reports"]:
            relative = self.relative(report["path"])
            if relative in self.report_paths:
                raise ValueError("duplicate archived report")
            self.report_paths.add(relative)
            raw = self.path(relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != report["sha256"]:
                raise ValueError("archived report changed: " + relative)
            archived = source_pairs(json.loads(raw))
            for row in report["changes"]:
                name = self.relative(row["path"])
                before, after = row["before_sha256"], row["after_sha256"]
                key = (relative, name, before, after)
                if (key in self.changes or before == after or not row["reason"].strip()
                        or (name, before) not in archived or (name, after) not in current
                        or (self.verify_sources and self.digest(name) != after)):
                    raise ValueError("source revision evidence differs: " + name)
                self.changes.add(key)

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("evidence path outside repository")
        return path

    def relative(self, path):
        return self.path(path).relative_to(self.root).as_posix()

    def digest(self, name):
        return hashlib.sha256(self.path(name).read_bytes()).hexdigest()

    def matches(self, report, name, before):
        after = self.digest(name)
        return before == after or (self.relative(report), self.relative(name), before, after) in self.changes

    def _replace_reviewed_digest_map(self, archived, generated, report):
        """Allow only reviewed changes in a report's source digest map."""
        old_map = archived.get("source_sha256")
        new_map = generated.get("source_sha256")
        if old_map is None and new_map is None:
            return True
        if not isinstance(old_map, dict) or not isinstance(new_map, dict) or old_map.keys() != new_map.keys():
            return False
        for name, before in old_map.items():
            after = new_map[name]
            if before == after:
                continue
            if not self.matches(report, name, before) or self.digest(name) != after:
                return False
            old_map[name] = after
        return True

    def product_matches(self, archived, generated, report):
        if archived == generated:
            return True
        old, new = json.loads(archived), json.loads(generated)
        if not self._replace_reviewed_digest_map(old, new, report):
            return False
        old_sources, new_sources = old.get("sources", []), new.get("sources", [])
        if len(old_sources) != len(new_sources):
            return False
        for before, after in zip(old_sources, new_sources):
            if before == after:
                continue
            if (before.keys() != after.keys() or before.get("path") != after.get("path")
                    or not self.matches(report, before["path"], before["sha256"])
                    or self.digest(after["path"]) != after["sha256"]):
                return False
            before["sha256"] = after["sha256"]
        return old == new
