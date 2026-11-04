"""Synthetic A7 fixtures. These objects never open a port or run esptool."""
from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from protocol.storage_upper_experiment import DeviceSnapshot, ExperimentPlan, STATIC_PROTECTED_NAMES, sha
from protocol.storage_upper_pattern import PAGE_BYTES, PAGE_START, build_pattern
from protocol.storage_upper_snapshot import LOW_RANGES
from test_storage_upper_pattern_runtime import synthetic_records, log_for

LOW_PAGE = bytes(range(256)) * 256
BOUNDARY_LOWER = bytes(range(16))


@contextmanager
def temporary_run(prefix="e143a7-test-"):
    root = (ROOT / "tmp").resolve()
    root.mkdir(exist_ok=True)
    path = root / (prefix + uuid.uuid4().hex[:12])
    path.mkdir()
    try:
        yield path
    finally:
        if path.exists():
            if path.is_symlink() or path.resolve().parent != root:
                raise AssertionError("fixture cleanup escaped repository tmp")
            shutil.rmtree(path)


def make_plan() -> ExperimentPlan:
    protected = {name: sha(("synthetic:" + name).encode()) for name in STATIC_PROTECTED_NAMES}
    protected.update({name: sha(LOW_PAGE[start:end]) for name, (start, end) in LOW_RANGES.items()
                      if name != "nvs"})
    return ExperimentPlan(sha(b"synthetic-a7-COM4"), b"R" * 8192, b"\xff" * PAGE_BYTES,
                          b"synthetic-candidate", build_pattern(), protected)


def make_snapshot(plan: ExperimentPlan) -> DeviceSnapshot:
    start, end = LOW_RANGES["nvs"]
    return DeviceSnapshot(plan.expected_identity_sha256, "esp32s3", 32 * 1024 * 1024,
                          16 * 1024 * 1024, False, False, plan.rollback_app, plan.rollback_page,
                          {**plan.expected_static_protected_sha256, "nvs": sha(LOW_PAGE[start:end])},
                          LOW_PAGE, BOUNDARY_LOWER)


def runtime_log(low_page: bytes = LOW_PAGE, boundary_lower: bytes = BOUNDARY_LOWER) -> str:
    records, _, _ = synthetic_records()
    summary = records["summary"]
    summary["low_alias_read_sha256"] = sha(low_page)
    summary["low_protected_read_sha256"] = sha(low_page[:0x9000])
    summary["boundary_read_hex"] = summary["boundary_mmap_hex"] = (boundary_lower + build_pattern()[:16]).hex()
    return log_for(records)


class FakeDevice:
    instances = []
    invalid_snapshot = False
    missing_reference = False
    identity_ok = True

    def __init__(self, **kwargs):
        self.plan = kwargs["plan"]
        self.write_enabled = kwargs["write_enabled"]
        self.app = self.plan.rollback_app
        self.page = self.plan.rollback_page
        self.boot_count = 0
        self.snapshot_count = 0
        self.writes = []
        self.instances.append(self)

    def snapshot(self, plan):
        self.snapshot_count += 1
        snapshot = make_snapshot(plan)
        if self.invalid_snapshot:
            snapshot.protected_sha256["assets"] = sha(b"drifted-assets")
        if self.missing_reference:
            snapshot = replace(snapshot, runtime_low_page=None)
        snapshot = replace(snapshot, app_erase_bytes=self.app, upper_page=self.page)
        self.last_snapshot = snapshot
        return snapshot

    def identity_matches(self, expected):
        return self.identity_ok and expected == self.plan.expected_identity_sha256

    def boot_original(self):
        self.boot_count += 1
        return True

    def write_flash(self, offset, payload):
        if not self.write_enabled:
            raise AssertionError("read-only fixture received a write")
        self.writes.append((offset, payload))
        if offset == PAGE_START:
            self.page = payload
        elif offset == 0x10000:
            self.app = payload + b"\xff" * (len(self.plan.rollback_app) - len(payload))
        else:
            raise AssertionError("write outside fixture ranges")

    def read_flash(self, offset, size):
        if offset == PAGE_START:
            return self.page[:size]
        if offset == 0x10000:
            return self.app[:size]
        raise AssertionError("read outside fixture ranges")

    def capture_probe(self, timeout_seconds):
        return runtime_log()
