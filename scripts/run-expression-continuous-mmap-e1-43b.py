#!/usr/bin/env python3
"""Execute or recover the COM4 App-only continuous mapping experiment."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, Journal, execute, load_plan, make_plan, persist_plan, sha, write_new,
    recover,
)
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT, CAPTURE_SCRIPT_SHA256, EsptoolDevice, ESPYTHON, load_persisted_plan,
)

def load_preparation():
    spec = importlib.util.spec_from_file_location("continuous_deployment_preparation", ROOT / "scripts/prepare-expression-e1-43b-deployment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppDevice(EsptoolDevice):
    """Reuse bounded read/log/reset transport; accept only this plan's App bytes."""
    def __init__(self, folder: Path, *, plan=None):
        if (not ESPYTHON.is_file() or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256
                or folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve()):
            raise ValueError("reviewed toolchain or run directory differs")
        self.port, self.run_dir, self.python, self.plan = "COM4", folder, ESPYTHON, plan
        from protocol.storage_upper_experiment_esptool import Runner
        self.runner, self._identity = Runner(), None
        self._reserved_logs = set()
        self._sequence = self._last_evidence_sequence()

    def identity(self) -> str:
        current = self._fresh_identity().digest
        if self.plan is not None and current != self.plan.metadata["identity_sha256"]:
            raise ValueError("fresh COM4 identity differs from persisted plan")
        return current

    def _require_plan_identity(self):
        self.identity()
        return self._identity

    def read_full(self) -> bytes:
        self.identity()
        raw = self._capture("full-readback", 0, FLASH_BYTES)
        self.identity()
        return raw

    def read_app(self, size: int) -> bytes:
        if self.plan is None or size != len(self.plan.rollback):
            raise ValueError("read must cover the exact App erase union")
        self.identity()
        return self._capture("app-readback", APP_START, size)

    def write_app(self, raw: bytes) -> None:
        if self.plan is None:
            raise ValueError("persisted App plan required")
        persisted = load_plan(self.run_dir)
        state = Journal(self.run_dir, persisted).load()
        if (persisted.plan_id != self.plan.plan_id or state is None
                or state["phase"] not in {"restore_required", "restoring"}):
            raise ValueError("durable App recovery intent required")
        allowed = [persisted.rollback]
        if state["phase"] == "restore_required":
            allowed.append(load_plan(self.run_dir, execution=True).candidate)
        if not isinstance(raw, bytes) or raw not in allowed:
            raise ValueError("payload is not the exact persisted App bytes")
        path = self._new_path("payloads", "app.bin")
        write_new(path, raw)
        log_path = self._reserve_log("write-app")
        self.identity()
        self._invoke("write-app", ("write_flash", "--verify", "--flash_mode", "keep", "--flash_size", "keep",
                                   hex(APP_START), str(path)), log_path=log_path)

    def read_flash(self, offset, size):
        raise ValueError("use the exact App or complete snapshot read")

    # Reject inherited A7 write/snapshot entry points: B has no test-page operation.
    def write_flash(self, offset, data):
        raise ValueError("use the App-only operation")

    def snapshot(self, plan):
        raise ValueError("use the full same-run snapshot")

    def _serial(self, label: str, seconds: int) -> str:
        if seconds not in {45, 450} or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
            raise ValueError("capture duration or script differs")
        path = self._new_path("captures", label + ".log")
        write_new(path, b"")
        log_path = self._reserve_log(label + "-capture")
        self.identity()
        code, _ = self._run_logged(label + "-capture", [str(ESPYTHON), str(CAPTURE_SCRIPT),
            "--port", "COM4", "--duration", str(seconds), "--output", str(path), "--usb-reset"],
            timeout=seconds + 45, log_path=log_path)
        if code != 0 or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("serial capture failed or exceeds limit")
        return path.read_text(encoding="utf-8", errors="strict")

    def capture(self) -> str:
        print("Starting diagnostic: wait for block 7, then use the 180-second hold window.", flush=True)
        return self._serial("continuous-probe", 450)

    def boot_original(self) -> bool:
        from protocol.storage_upper_experiment_esptool import _NORMAL_BOOT_MARKERS, _PANIC_MARKERS
        log = self._serial("normal-boot", 45)
        return all(marker in log for marker in _NORMAL_BOOT_MARKERS) and not any(marker in log for marker in _PANIC_MARKERS)


def folder_for(label: str) -> Path:
    if re.fullmatch(r"e143b-[a-z0-9][a-z0-9-]{0,39}", label) is None:
        raise ValueError("run label must begin e143b-")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run directory must be a direct child of repository tmp")
    return folder


def save_json(path: Path, value: dict) -> None:
    write_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def accepted_snapshot(raw: bytes, prep: dict) -> None:
    acceptance = json.loads((ROOT / prep["baseline_acceptance"]["path"]).read_text(encoding="utf-8"))
    live = json.loads((ROOT / acceptance["sealed_experiment_report"]).read_text(encoding="utf-8"))
    for name, row in live["restored_ranges"].items():
        if name == "nvs":
            continue
        if sha(raw[row["offset"]:row["offset"] + row["bytes"]]) != row["sha256"]:
            raise ValueError("live accepted baseline differs: " + name)
    old = prep["original_app"]
    if sha(raw[APP_START:APP_START + old["bytes"]]) != old["sha256"]:
        raise ValueError("live App is not accepted E1.42")


def run_new(folder: Path, report: Path) -> dict:
    if folder.exists():
        raise FileExistsError("retain existing run; use a new label")
    prep = load_preparation().prepare(report)
    candidate = (ROOT / prep["candidate_app"]["path"]).read_bytes()
    acceptance = json.loads((ROOT / prep["baseline_acceptance"]["path"]).read_text(encoding="utf-8"))
    live = json.loads((ROOT / acceptance["sealed_experiment_report"]).read_text(encoding="utf-8"))
    previous_label = live.get("run_label", "")
    if re.fullmatch(r"e143a7-[a-z0-9][a-z0-9-]{0,39}", previous_label) is None:
        raise ValueError("accepted report must name the private A7 run")
    previous = load_persisted_plan(ROOT / "tmp" / previous_label)
    if previous.plan_id != live.get("plan_id"):
        raise ValueError("accepted identity plan differs from sealed audit")
    folder.mkdir()
    device = AppDevice(folder)
    identity, confirmed, plan = None, False, None
    try:
        identity = device.identity()
        if identity != previous.expected_identity_sha256:
            raise ValueError("COM4 is not the previously accepted body")
        confirmed = True
        raw = device.read_full()
        accepted_snapshot(raw, prep)
        write_new(folder / "snapshot-32m.bin", raw)
        plan = make_plan(raw, candidate, original_app_bytes=prep["original_app"]["bytes"],
                         identity_sha256=identity, run_label=folder.name)
        persist_plan(folder, plan)
        save_json(folder / "preflight.json", {"schema": "deskbot.continuous_mmap.live_preflight.v1", "result": "passed",
            "plan_id": plan.plan_id, "build_report": report.relative_to(ROOT).as_posix(),
            "build_report_sha256": sha(report.read_bytes()), "snapshot_sha256": sha(raw), "flash_written": False})
        device.plan = plan
        print("Same-run backup saved; starting temporary App and diagnostic capture.", flush=True)
        result = execute(plan, device, Journal(folder, plan), initial_snapshot=raw)
        save_json(folder / "execute-result.json", result)
        return result
    except BaseException:
        # After first-write intent the controller owns recovery. Prior failures
        # can only have left the unchanged original App waiting in download mode.
        if confirmed:
            state = Journal(folder, plan).load() if plan is not None else None
            if state is None or state["phase"] == "prepared":
                device.boot_original()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("execute", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--build-report", type=Path)
    args = parser.parse_args()
    folder = folder_for(args.run_label)
    if args.phase == "execute":
        if args.build_report is None:
            parser.error("execute requires --build-report")
        result = run_new(folder, args.build_report.resolve())
    else:
        plan = load_plan(folder)
        device, journal = AppDevice(folder, plan=plan), Journal(folder, plan)
        result = recover(plan, device, journal)
        save_json(folder / "recovery-result.json", result)
    print(json.dumps({"phase": args.phase, "run_label": args.run_label,
                      "result": result.get("result", result.get("phase", "completed")),
                      "probe_passed": result.get("probe_passed"), "restored": result.get("restored")}, ensure_ascii=False))
    return 0 if args.phase != "execute" or result.get("probe_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
