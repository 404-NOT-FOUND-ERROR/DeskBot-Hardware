"""Pin the touch-only diagnostic App and its E3f-9d.3 rollback."""
import json
from pathlib import Path

from protocol import display_clock_e3f9d3 as baseline
from protocol import studio_app_deployment as app
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-9d.4-touch", "E3f-9d.3"
THRESHOLD_PERCENT = 5
SOURCE = Path("tmp/touch-e3f9d4/frozen/xiaozhi.bin")
BUILD_REPORT = Path("tmp/touch-e3f9d4/firmware-build-report.json")
BUILD_REPORT_SHA = "bce1cd25425291e2a695a331d71e6d38db1b4e731ce2a5cc0cd0e6e5c3a51334"
APP_SIZE = 4541232
APP_SHA = "3570428fffc03957a97101197a76274de483d89513dbd1b049a754cf590261a1"
APP_ELF_SHA = "5da5f396f135eb9de73ca0bc39ce1fb2239b2a0d966b54a562e598ae373bfbaa"
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
STATIC_SHA = "ec5268006f1d6a39d55bd5b4daf6fb59781014f54bbe38f6b901fff72ae9b2af"
REFERENCE = Path("tmp/e3c-live-20261113-e3f9d3-live1")
DISPLAY_ERRORS = baseline.DISPLAY_ERRORS


def load_reference(root=ROOT):
    saved = baseline.check_plan(app.load(root / REFERENCE), root=root)
    receipt = json.loads((root / REFERENCE / "install-result.json").read_bytes())
    if receipt.get("result") != "candidate_installed":
        raise ValueError("retained display installation required")
    raw = saved.snapshot[:app.START] + saved.candidate + saved.snapshot[app.START + len(saved.candidate):]
    check_current(raw)
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
                report=dict(edition=ROLLBACK_TARGET, resources="two-role-v1"))


def check_current(raw, reference=None):
    if (type(raw) is not bytes or len(raw) != app.FLASH
            or app.sha(raw[:0x9000] + raw[0xD000:]) != STATIC_SHA
            or app.sha(raw[app.START:app.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("installed_E3f9d3_snapshot_required")


def reviewed_candidate(raw=None, root=ROOT):
    raw = (root / SOURCE).read_bytes() if raw is None else raw
    if len(raw) != APP_SIZE or app.sha(raw) != APP_SHA:
        raise ValueError("touch_diagnostic_App_changed")
    facts = image_facts(raw)
    start = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != app.FLASH or raw[start + 144:start + 176].hex() != APP_ELF_SHA:
        raise ValueError("touch_diagnostic_profile_changed")
    return raw


def check_plan(plan, *, candidate=True, root=ROOT):
    plan.validate()
    m = plan.metadata
    if (m["edition"] != "E3c" or m["identity_sha256"] != IDENTITY_SHA
            or m["original_bytes"] != ORIGINAL_SIZE or m["candidate_bytes"] != APP_SIZE
            or m["candidate_sha256"] != APP_SHA):
        raise ValueError("touch_diagnostic_plan_changed")
    check_current(plan.snapshot)
    if candidate:
        reviewed_candidate(plan.image, root=root)
    return plan


def prepare_plan(snapshot, identity, image, root=ROOT):
    check_current(snapshot)
    return check_plan(app.prepare(snapshot, reviewed_candidate(image, root), identity,
                                  ORIGINAL_SIZE, edition="E3c"), root=root)


def check_build_evidence(root=ROOT):
    raw = (root / BUILD_REPORT).read_bytes()
    if app.sha(raw) != BUILD_REPORT_SHA:
        raise ValueError("touch_diagnostic_build_report_changed")
    report = json.loads(raw)
    if (report.get("edition") != EDITION or report.get("result") != "build_verified"
            or report.get("source_delta") != ["main/boards/esp_vocat/touch_sensor.cc"]
            or report.get("effective_config_equal") is not True
            or not all(report["protected_equal"].values())
            or report["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA)):
        raise ValueError("touch_diagnostic_build_required")
    return dict(image=reviewed_candidate(root=root),
                candidate=dict(variant="touch-probe", bytes=APP_SIZE,
                               sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA),
                build_report_sha256=app.sha(raw), offline_report_sha256=None, path_audit_sha256=None)


def check_display_boot(log):
    baseline.check_display_boot(log)
    if ("TouchProbe: ready period_ms=500 threshold_percent=5 gesture=original" not in log
            or not all(f"TouchProbe: pad={pad} raw=" in log for pad in (7, 6))
            or any(marker in log for marker in ("TouchProbe: setup failed", "TouchProbe: task creation failed"))):
        raise ValueError("touch_diagnostic_boot_incomplete")
