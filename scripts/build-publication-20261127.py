#!/usr/bin/env python3
"""Register the November 27 public tree against immutable earlier publications."""
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

CURRENT = "artifacts/publication-20261127/source-snapshot.json"
spec = importlib.util.spec_from_file_location("publication_previous", ROOT / "scripts/build-publication-20261122.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
REPORTS = [*previous.REPORTS, previous.CURRENT]
ARCHIVES = (previous.historical.REVIEW, previous.historical.legacy.REVIEW,
    previous.CURRENT, "artifacts/publication-20261122/source-review.json")

# Every changed historical input requires an explicit path and rationale.
# Earlier snapshots and their reviews remain byte-for-byte intact.
GROUPS = {
    'Document current r8, alarm and production pipeline status with explicit physical acceptance boundaries.': (
        'CHANGELOG.en.md',
        'CHANGELOG.md',
        'README.en.md',
        'README.md',
        'docs/architecture.md',
        'docs/baseline-status.md',
        'docs/body-daily-service.md',
        'docs/development-pipeline-20261102.md',
        'docs/firmware-next-steps-20261120.md',
        'docs/layered-face-integration.md',
        'docs/project-mainline.md',
        'docs/roadmap.md',
        'docs/xiaozhi-cloud-life-bridge.md',
    ),
    'Publish portable CI checks and retain exact historical source evidence.': (
        '.gitattributes',
        '.github/workflows/host-checks.yml',
        '.gitignore',
        'scripts/check-publication.py',
        'scripts/publication_evidence.py',
        'scripts/archived_products.py',
    ),
    'Integrate credential renewal, layered rendering and voice alarm controls while retaining the existing device baseline.': (
        'firmware/body/main/boards/esp_vocat/body_network_config.h',
        'firmware/body/main/boards/esp_vocat/body_network_config.inc',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_api.h',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_manager.cc',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_pomodoro.cc',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_pomodoro.h',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_sleep_24h.cc',
        'firmware/body/main/boards/esp_vocat/customer_ui/alarm_sleep_24h.h',
        'firmware/body/main/boards/esp_vocat/dev_tools.cc',
        'firmware/body/main/boards/esp_vocat/head_touch_wifi.h',
        'firmware/body/main/boards/esp_vocat/head_touch_wifi.inc',
        'firmware/body/main/boards/esp_vocat/studio_layered_face.h',
        'firmware/body/main/boards/esp_vocat/studio_layered_face.inc',
        'firmware/body/main/boards/esp_vocat/studio_layered_gfx.h',
        'firmware/body/main/boards/esp_vocat/studio_layered_runtime.inc',
        'firmware/body/main/boards/esp_vocat/studio_usb_diagnostic.cc',
        'firmware/body/main/boards/esp_vocat/studio_usb_session.h',
        'firmware/body/main/boards/esp_vocat/wifi_body_runtime.inc',
        'firmware/body/main/boards/esp_vocat/wifi_expression.inc',
    ),
    'Implement validated expression packaging, credential and App-only deployment transactions.': (
        'protocol/airboat_role_deployment.py',
        'protocol/body_network_workbench.py',
        'protocol/eaf_preview.py',
        'protocol/layered_face.py',
        'protocol/layered_gfx_patch.py',
        'protocol/wifi_trial_deployment.py',
    ),
    'Expose the real expression pipeline and build the current compatible firmware resources.': (
        'scripts/build-layered-face.py',
        'scripts/build-wifi-expression.py',
        'scripts/configure-body-network.py',
        'scripts/deploy-wifi-trial.py',
        'scripts/layered-face-preview.html',
        'scripts/layered-face-preview.js',
        'scripts/serve-deskbot-shell-workbench.py',
        'scripts/start-deskbot-shell-workbench.ps1',
        'scripts/test-body-wifi.py',
    ),
    'Verify production behavior and portable public fixtures for this release.': (
        'tests/cpp/body_wifi_worker_test.cc',
        'tests/cpp/layered_face_test.cc',
        'tests/cpp/studio_role_player_test.cc',
        'tests/layered-face-browser.cjs',
        'tests/test_airboat_role_deployment.py',
        'tests/test_body_network_workbench.py',
        'tests/test_body_wifi.py',
        'tests/test_layered_face.py',
        'tests/test_layered_runtime.py',
        'tests/test_wifi_trial_deployment.py',
    ),
}
REASONS = {**previous.REASONS,
    **{path: reason for reason, paths in GROUPS.items() for path in paths}}

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
    snapshot = dict(schema="deskbot.publication_source_snapshot/v1", edition="PUB-2026-11-27",
        hardware_accessed=False, device_written=False,
        sources=[dict(path=name, sha256=digest) for name, digest in digests.items()],
        historical_files=[dict(path=path, sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest())
            for path in ARCHIVES])
    review = dict(schema="deskbot.source_review/v1", edition="PUB-2026-11-27",
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
