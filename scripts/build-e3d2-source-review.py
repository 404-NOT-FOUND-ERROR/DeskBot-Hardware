#!/usr/bin/env python3
"""Record the bounded E3d-2 source changes beside unchanged historical reports."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publication_evidence import LEGACY_REVIEW as REVIEW, source_pairs

HOST = "Catalog paging reuses the display/control session and its single USB owner."
FIRMWARE = "Catalog snapshots add resource-owner lifecycle hooks and a bounded USB page provider."
REPORTS = {
    "artifacts/deskbot-studio-e3c6-20261105/report.json": {
        "protocol/studio_control_link.py": HOST,
        "protocol/studio_control_bridge.py": HOST,
        "scripts/build-deskbot-studio-e3c.py": "The check command compares reviewed source revisions while retaining the archived report.",
    },
    "artifacts/deskbot-studio-e3c6-20261105/offline-validation.json": {
        "protocol/studio_control_link.py": HOST,
        "protocol/studio_control_bridge.py": HOST,
        "protocol/deskbot-studio-control-link-v1.schema.json": "Optional catalog progress is added to the host state schema.",
        "scripts/build-deskbot-studio-e3c.py": "The check command compares reviewed source revisions while retaining the archived report.",
        "tests/test_deskbot_studio_e3c.py": "The reproduction test checks the bounded source review and unchanged product fields.",
        "firmware/body/SOURCE_MANIFEST.sha256": "The manifest records E3d-2 catalog sources.",
        "firmware/body/main/boards/esp_vocat/studio_usb_session.h": FIRMWARE,
    },
    "artifacts/deskbot-studio-e3d1-20261105/offline-validation.json": {
        "scripts/run-offline-checks.py": "The portable suite adds E3d-2 and source-evidence regression tests.",
    },
    "artifacts/expression-storage-strategy-e1-35-20261029/report.json": {
        "firmware/body/main/assets.cc": FIRMWARE,
    },
    "artifacts/expression-addressing-e1-38-20261030/report.json": {
        "firmware/body/main/assets.cc": FIRMWARE,
    },
    "artifacts/deskbot-studio-e3c3-20261105/offline-validation.json": {
        "protocol/studio_runtime_query.py": "The runtime query parser keeps the validated state response contract.",
        "firmware/body/main/Kconfig.projbuild": "The diagnostic configuration keeps the query feature disabled by default.",
        "firmware/body/main/boards/esp_vocat/studio_runtime_state.h": "The runtime state store keeps bounded snapshot ownership.",
        "firmware/body/main/boards/esp_vocat/studio_runtime_state.cc": "The runtime state store keeps bounded snapshot ownership.",
        "tests/cpp/studio_runtime_snapshot_test.cc": "The native snapshot regression covers the bounded state store.",
        "firmware/body/main/boards/esp_vocat/studio_usb_session.h": "The USB session keeps one owner and bounded diagnostic pages.",
        "firmware/body/main/boards/esp_vocat/studio_usb_diagnostic.cc": "The diagnostic endpoint keeps framing and recovery behavior bounded.",
        "protocol/studio_app_deployment.py": "Deployment preparation keeps the explicit App-only write boundary.",
        "tests/test_studio_runtime_query.py": "The host query tests keep the state response and expiry rules covered.",
        "scripts/validate-deskbot-studio-e3c3.py": "The archive check permits only explicitly reviewed source digest updates.",
    },
}
CURRENT = "artifacts/deskbot-studio-e3d2-20261105/report.json"


def products():
    current = dict(source_pairs(json.loads((ROOT / CURRENT).read_bytes())))
    reports = []
    for relative, names in REPORTS.items():
        raw = (ROOT / relative).read_bytes()
        before = dict(source_pairs(json.loads(raw)))
        changes = []
        for name, reason in names.items():
            # Reproduce this historical transition from its recorded endpoints.
            # Later editions keep their own exact old-to-current review.
            after = current[name]
            if before[name] == after:
                raise ValueError("expected reviewed source delta differs: " + name)
            changes.append(dict(path=name, before_sha256=before[name], after_sha256=after, reason=reason))
        reports.append(dict(path=relative, sha256=hashlib.sha256(raw).hexdigest(), changes=changes))
    return (json.dumps(dict(schema="deskbot.source_review/v1", edition="E3d-2",
        historical_reports_rewritten=False, current_report=CURRENT, reports=reports), indent=2) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / REVIEW
    raw = products()
    if args.check:
        if path.read_bytes() != raw:
            raise ValueError("source review differs")
    else:
        path.write_bytes(raw)
    print("E3d-2 source review " + ("checked" if args.check else "recorded"))
