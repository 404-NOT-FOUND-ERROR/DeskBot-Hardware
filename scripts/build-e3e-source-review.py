#!/usr/bin/env python3
"""Record exact current revisions beside the retained historical source reports."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publication_evidence import E3E_REVIEW as REVIEW, SourceReview, source_pairs

spec = importlib.util.spec_from_file_location("historical_e3d2_review", ROOT / "scripts/build-e3d2-source-review.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
CURRENT = "artifacts/deskbot-studio-e3e-20261108/report.json"
REPORTS = [*legacy.REPORTS, *("artifacts/deskbot-studio-e3d2-20261105/" + name for name in
    ("report.json", "offline-validation.json", "native-validation.json", "full-offline-validation.json")),
    "artifacts/deskbot-studio-e3e-20261108/history/before-usb-timing/report.json",
    "artifacts/deskbot-studio-e3e-20261108/history/before-role-overview-e3f2/report.json"]
REASONS = {
    "scripts/build-deskbot-studio-e3e.py": "The current workbench records USB timing and optional shy-control sources and tests; historical reports keep their original bytes.",
    "scripts/build-e3e-source-review.py": "The review registers timing, E3f-1 shy and E3f-2 analysis-only revisions; preceding reports and reviews are retained separately.",
    ".github/workflows/host-checks.yml": "CI adds the E3f-2 JavaScript syntax check and production-page browser checks with a memory bridge.",
    "firmware/body/SOURCE_MANIFEST.sha256": "Four E3f-1 firmware source hashes are refreshed for the default-off shy capability and dispatch increment.",
    "firmware/body/main/Kconfig.projbuild": "E3f-1 adds a default-off shy request flag depending on the existing expression entry point.",
    "firmware/body/main/boards/esp_vocat/studio_expression_control.h": "The enabled variant accepts embarrassed and copies the accepted semantic into a bounded local buffer at the original commit point.",
    "firmware/body/main/boards/esp_vocat/studio_expression_adapter.cc": "The enabled dispatcher copies the semantic under the existing lock and invokes the original player after unlocking.",
    "firmware/body/main/boards/esp_vocat/studio_display_readiness.h": "Only the shy-enabled reply appends the bounded expression_semantics capability list.",
    "protocol/deskbot-studio-display-v1.schema.json": "The optional capability list permits at most two unique known semantics; absence preserves legacy neutral support.",
    "protocol/studio_display_query.py": "The host validates optional semantic capabilities and retains neutral-only compatibility with old firmware.",
    "scripts/deskbot-studio-e3c.js": "The workbench adds an explicitly clicked shy request, same-session installed binding and inherited base-action explanation.",
    "scripts/deskbot-studio-e3d2.js": "The live directory displays the expression capabilities advertised by the current device reply.",
    "scripts/deskbot-studio-e3e.js": "Role analysis labels and enables a matching neutral or shy request from current capability and installed-resource results.",
    "tests/cpp/studio_expression_control_test.cc": "The enabled native harness reports actual embarrassed player calls separately from total calls.",
    "tests/serve_studio_e3e_fixture.py": "The memory bridge preserves either neutral or embarrassed for production HTTP and browser verification.",
    "tests/test_studio_expression_control.py": "The existing native harness can compile both capability variants and observes the actual player semantic.",
    "protocol/studio_runtime_query.py": "USB reply budgets are 8 seconds; state age starts at a correlated complete reply, while leases remain send-anchored.",
    "protocol/studio_control_link.py": "Slow-link scheduling preserves timing budgets, gates shy requests on current capability and serializes queued playback with directory reads.",
    "protocol/studio_control_bridge.py": "IPC keeps the 10-second local command budget and accepts embarrassed only when advertised by a fresh device reply.",
    "protocol/deskbot-studio-control-link-v1.schema.json": "The reply-age contract is retained and the command enum additionally permits embarrassed.",
    "tools/observe-studio-body-e3c.py": "The single USB worker captures command enqueue and snapshot times and can trace bounded writes and replies without a serial flush.",
    "protocol/studio_role_expression.py": "Role planning retains the 2-second freshness limit and matching shy capability; E3f-2 adds a default-preserving analysis option that returns before command construction.",
    "tests/test_studio_control_link.py": "Control regression checks the 8-second transport boundary, receive-based age and no submission during an outstanding query.",
    "tests/test_studio_catalog_query.py": "Catalog regression checks 8-second page timeouts and uninterrupted directory pagination before display resumes.",
    "tests/test_studio_runtime_query.py": "Native roundtrips check receive-based cache age while preserving conservative session leases.",
    "tests/test_studio_display_query.py": "Native display tests distinguish reply validity from the serial transfer budget.",
    "tests/test_deskbot_studio_e3c.py": "The bridge command wait is tested at the 10-second boundary without resending commands.",
    "scripts/publication_evidence.py": "E3e checks exact current source digests while retaining historical reports.",
    "scripts/build-e3d2-source-review.py": "The historical review reproduces its recorded endpoints independently of later source revisions.",
    "scripts/build-deskbot-studio-e3d2.py": "Historical page checks allow only explicitly reviewed source digest changes.",
    "scripts/check-publication.py": "Publication checks retain E3d-2 evidence and verify the current E3e source review.",
    "scripts/run-offline-checks.py": "The portable suite includes role planning and the E3e and E3f-2 production HTTP services.",
    "scripts/check-workbenches.py": "The page checks include the E3e and E3f-2 workbenches.",
    "tests/test_deskbot_studio_e3d2.py": "Historical reproduction checks exact page bytes and reviewed source fields.",
    "tests/test_publication_evidence.py": "Regression checks cover subsequent editions and unreviewed source changes.",
    "tests/test_studio_control_native.py": "Role requests exercise the production C++ endpoint, with optional staged-source selection.",
    "tests/test_storage_upper_migration_preflight.py": "Historical preflight rejects either of the explicitly changed pinned inputs.",
    "protocol/storage_continuous_mmap_experiment.py": "The diagnostic transaction records the same-run NVS baseline and preserves it through recovery.",
    "tests/test_storage_continuous_mmap_experiment.py": "Regression cases distinguish current NVS updates from changed App or protected bytes.",
}


def products():
    current = dict(source_pairs(json.loads((ROOT / CURRENT).read_bytes())))
    reports = []
    for relative in REPORTS:
        raw = (ROOT / relative).read_bytes()
        before = dict(source_pairs(json.loads(raw)))
        changes = []
        for name, digest in sorted(before.items()):
            path = ROOT / name
            endpoint = current.get(name)
            if endpoint is None or endpoint == digest:
                continue
            reason = REASONS.get(name) or legacy.REPORTS.get(relative, {}).get(name)
            if not reason:
                raise ValueError("source change needs explicit review: " + name)
            changes.append(dict(path=name, before_sha256=digest, after_sha256=endpoint, reason=reason))
        reports.append(dict(path=relative, sha256=hashlib.sha256(raw).hexdigest(), changes=changes))
    return (json.dumps(dict(schema="deskbot.source_review/v1", edition="E3e",
        historical_reports_rewritten=False, current_report=CURRENT, reports=reports), indent=2) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path, raw = ROOT / REVIEW, products()
    if args.check:
        if path.read_bytes() != raw:
            raise ValueError("E3e source review differs")
    else:
        path.write_bytes(raw)
    SourceReview(review=REVIEW, verify_sources=False)
    print("E3e source review " + ("checked" if args.check else "recorded"))
