#!/usr/bin/env python3
"""Snapshot public sources and register exact historical-to-current revisions."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publication_evidence import REVIEW, SourceReview, source_pairs

CURRENT = "artifacts/publication-20261122/source-snapshot.json"
spec = importlib.util.spec_from_file_location("publication_e3e_history", ROOT / "scripts/build-e3e-source-review.py")
historical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(historical)
REPORTS = [*historical.REPORTS, historical.CURRENT,
    "artifacts/deskbot-studio-e3d1-20261105/report.json"]

# Every later change must belong to an explicitly listed source and rationale.
# Adding a new unlisted delta fails even when its digest can be calculated.
GROUPS = {
    "Compare archived and regenerated products at two exact endpoints, preserving historical result fields.": (
        "scripts/build-deskbot-studio-e2c.py", "scripts/build-deskbot-studio-e3a.py", "scripts/build-deskbot-studio-e3b.py",
        "tests/test_deskbot_studio_e2c.py", "tests/test_deskbot_studio_e3a.py", "tests/test_deskbot_studio_e3b.py",
        "tests/test_deskbot_studio_e3d.py", "tests/test_deskbot_studio_e3e.py", "tests/test_studio_usb_transport.py",
        "tests/test-expression-e1-28-semantic-review.py", "tests/vendor/studio_catalog/SOURCES.json",
    ),
    "Record the current portable CI, publication audit and immutable historical source endpoints.": (
        ".github/workflows/host-checks.yml", "scripts/build-e3d2-source-review.py",
        "scripts/build-e3e-source-review.py", "scripts/check-publication.py",
        "scripts/publication_evidence.py", "scripts/run-offline-checks.py",
        "scripts/check-workbenches.py", "tests/test_publication_evidence.py",
    ),
    "Extend the locked firmware inputs for catalog, role playback, network exchange and layered rendering.": (
        "firmware/body/SOURCE_MANIFEST.sha256", "firmware/body/main/Kconfig.projbuild",
        "firmware/body/main/assets.cc", "firmware/body/main/boards/esp_vocat/ui_bridge.cc",
        "firmware/body/main/display/emote_display.cc",
    ),
    "Retain one USB owner while adding bounded resource catalogs, role commands and current display capabilities.": (
        "firmware/body/main/boards/esp_vocat/studio_catalog.h",
        "firmware/body/main/boards/esp_vocat/studio_catalog_adapter.cc",
        "firmware/body/main/boards/esp_vocat/studio_catalog_builder.cc",
        "firmware/body/main/boards/esp_vocat/studio_display_readiness.h",
        "firmware/body/main/boards/esp_vocat/studio_expression_adapter.cc",
        "firmware/body/main/boards/esp_vocat/studio_expression_control.h",
        "firmware/body/main/boards/esp_vocat/studio_usb_diagnostic.cc",
        "firmware/body/main/boards/esp_vocat/studio_usb_session.h",
        "firmware/body/main/boards/esp_vocat/studio_runtime_state.cc",
        "firmware/body/main/boards/esp_vocat/studio_runtime_state.h",
    ),
    "Describe bounded observation, catalog and expression capabilities with legacy-compatible optional fields.": (
        "protocol/deskbot-body-observation-v2.schema.json",
        "protocol/deskbot-studio-catalog-page-v1.schema.json",
        "protocol/deskbot-studio-control-link-v1.schema.json",
        "protocol/deskbot-studio-display-v1.schema.json",
        "protocol/studio_identity_observation.py", "protocol/studio_installed_catalog.py",
        "protocol/studio_display_query.py", "protocol/studio_runtime_query.py",
    ),
    "Serialize queries, playback and catalog reads, preserving response age and single-session ownership.": (
        "protocol/studio_control_bridge.py", "protocol/studio_control_link.py",
        "protocol/studio_role_expression.py", "tools/observe-studio-body-e3c.py",
    ),
    "Preserve deployment restoration and current NVS observations while extending validated resource packaging.": (
        "protocol/storage_continuous_mmap_experiment.py", "protocol/studio_app_deployment.py",
        "protocol/studio_resource_package.py", "tests/test_storage_continuous_mmap_experiment.py",
        "tests/test_storage_upper_migration_preflight.py",
    ),
    "Run E3c-3 boot-log and digest tests with a synthetic App when private deployment evidence is absent.": (
        "tests/test_studio_e3c3_deployment.py",
    ),
    "Add the separately recorded resource sources and confidence-based magnetic demo without changing identity automatically.": (
        "protocol/expression-master-library-v1.json", "protocol/magnetic_recognition.py",
    ),
    "Extend workbench controls and historical reproduction to current expression, catalog and shared-connection behavior.": (
        "scripts/build-deskbot-studio-e3c.py", "scripts/build-deskbot-studio-e3d.py",
        "scripts/build-deskbot-studio-e3d2.py", "scripts/build-deskbot-studio-e3e.py",
        "scripts/deskbot-studio-e3c.js", "scripts/deskbot-studio-e3d2.js",
        "scripts/deskbot-studio-e3e.js", "scripts/serve-deskbot-studio-e3e.py",
        "scripts/validate-deskbot-studio-e3c3.py",
    ),
    "Exercise the production bounded catalog, runtime and expression endpoints with isolated fixtures.": (
        "tests/cpp/studio_catalog_test.cc", "tests/cpp/studio_expression_control_test.cc",
        "tests/cpp/studio_runtime_snapshot_test.cc", "tests/serve_studio_e3e_fixture.py",
        "tests/test_deskbot_studio_e3c.py", "tests/test_deskbot_studio_e3d2.py",
        "tests/test_studio_runtime_snapshot.py",
        "tests/test_printf_portability_guard.py", "tests/test_studio_catalog_native.py",
        "tests/test_studio_catalog_query.py", "tests/test_studio_control_link.py",
        "tests/test_studio_control_native.py", "tests/test_studio_display_query.py",
        "tests/test_studio_expression_control.py", "tests/test_studio_runtime_query.py",
    ),
    "Check archived workbenches by their rendered resource and visible panels across browser runners.": (
        "tests/deskbot-studio-e3d2-browser.cjs", "tests/deskbot-studio-e3e-browser.cjs",
        "tests/studio-role-overview-browser.cjs",
    ),
}
REASONS = {path: reason for reason, paths in GROUPS.items() for path in paths}


def products():
    names = set(filter(None, subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")))
    # Snapshot every public Git input, including generated HTML and reports.
    # The two generated records are omitted to avoid recursive digests.
    sources = names - {CURRENT, REVIEW}
    originals = {}
    for relative in REPORTS:
        raw = (ROOT / relative).read_bytes()
        before = source_pairs(json.loads(raw))
        originals[relative] = (raw, before)
    digests = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(sources)}
    reports = []
    for relative, (raw, pairs) in originals.items():
        changes = []
        for name, before in sorted(pairs):
            after = digests.get(name)
            if after is None:
                raise ValueError("historical source missing from public inputs: " + name)
            if before == after:
                continue
            if name not in REASONS:
                raise ValueError("source change needs explicit review: " + name)
            changes.append(dict(path=name, before_sha256=before, after_sha256=after, reason=REASONS[name]))
        reports.append(dict(path=relative, sha256=hashlib.sha256(raw).hexdigest(), changes=changes))
    snapshot = dict(schema="deskbot.publication_source_snapshot/v1", edition="PUB-2026-11-22",
        hardware_accessed=False, device_written=False,
        sources=[dict(path=name, sha256=digest) for name, digest in digests.items()],
        historical_files=[dict(path=path, sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest())
            for path in (historical.REVIEW, historical.legacy.REVIEW)])
    review = dict(schema="deskbot.source_review/v1", edition="PUB-2026-11-22",
        historical_reports_rewritten=False, current_report=CURRENT, reports=reports)
    encode = lambda value: (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return {CURRENT: encode(snapshot), REVIEW: encode(review)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for relative, raw in products().items():
        path = ROOT / relative
        if args.check:
            if path.read_bytes() != raw:
                raise ValueError("publication source record differs: " + relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    SourceReview()
    print("Publication source records " + ("checked" if args.check else "recorded"))


if __name__ == "__main__":
    main()
