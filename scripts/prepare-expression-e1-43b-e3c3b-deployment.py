#!/usr/bin/env python3
"""Prepare a read-only E3c-3b snapshot and exact App recovery plan."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3b_baseline as baseline
from protocol.storage_continuous_mmap_experiment import APP_START, FLASH_BYTES, load_plan, make_plan, persist_plan, sha, write_new
from protocol.storage_upper_experiment_esptool import FlashReadTransferError, _NORMAL_BOOT_MARKERS, _PANIC_MARKERS

SPEC = importlib.util.spec_from_file_location("e143b_previous_readonly_transport", ROOT / "scripts/prepare-expression-e1-43b-e3c3a-deployment.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("reviewed read-only COM4 transport is unavailable")
previous = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(previous)
RUN_LABEL = re.compile(r"e143b-e3c3b-[0-9]{8}-[a-z0-9][a-z0-9-]{0,17}")


def _json(path: Path, value: dict) -> None:
    write_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def validate_boot_log(text: str) -> dict:
    """The boot log prints an ELF prefix; the full digest is checked in Flash."""
    prefixes = re.findall(r"ELF file SHA256:\s*([0-9a-fA-F]+)(?:\.\.\.)?", text)
    if (not all(marker in text for marker in _NORMAL_BOOT_MARKERS)
            or any(marker in text for marker in _PANIC_MARKERS)
            or len(prefixes) != 1 or len(prefixes[0]) < 8
            or not baseline.APP_ELF_SHA256_EXPECTED.startswith(prefixes[0].lower())):
        raise ValueError("normal E3c-3b boot markers or ELF identity differ")
    return {"normal_boot_observed": True, "elf_prefix": prefixes[0].lower()}


class ReadOnlyAppDevice(previous.ReadOnlyAppDevice):
    def _invoke(self, label, args, *, timeout=180, log_path=None):
        try:
            return super()._invoke(label, args, timeout=timeout, log_path=log_path)
        except FlashReadTransferError as error:
            # This preparation audit binds one transfer to each snapshot block.
            # Keep the partial evidence and leave boot cleanup to prepare_live().
            raise OSError("snapshot read packet truncated; evidence retained, preparation stopped") from error

    def boot_original(self) -> bool:
        log = self._serial("normal-boot", 45)
        validate_boot_log(log)
        return True


def _safe_run_folder(folder: Path, run_label: str) -> Path:
    if not isinstance(run_label, str) or RUN_LABEL.fullmatch(run_label) is None:
        raise ValueError("run label must be e143b-e3c3b-YYYYMMDD-name")
    tmp = ROOT / "tmp"
    target = baseline.safe_path(folder, tmp, exists=False)
    if target != tmp.resolve(strict=True) / run_label:
        raise ValueError("run must be the named direct child of repository tmp")
    if target.exists():
        raise FileExistsError("retain existing run and select a new label")
    return target


def _persist(folder: Path, label: str, snapshot: bytes, facts: dict, build: dict) -> dict:
    original = facts["original_app"]
    plan = make_plan(snapshot, build["candidate"], original_app_bytes=original["bytes"],
                     identity_sha256=facts["identity_sha256"], run_label=label)
    # Every byte in the sector union, including the old App tail, is restored.
    persist_plan(folder, plan)
    saved = load_plan(folder, execution=True)
    if saved != plan or plan.rollback != snapshot[APP_START:APP_START + len(plan.rollback)]:
        raise ValueError("saved App recovery plan differs from the snapshot")
    receipt = {
        "schema": "deskbot.continuous_mmap.live_preflight.e3c3b.v1",
        "edition": "E1.43b-2", "baseline": "E3c-3b",
        "result": "snapshot_validated_rollback_prepared", "run_label": label,
        "target": "body_COM4", "plan_id": plan.plan_id,
        "identity_sha256": facts["identity_sha256"],
        "baseline_reference": facts,
        "build": {"report": build["report"], "audit": build["audit"],
                  "candidate_path": build["candidate_path"]},
        "snapshot": {"file": "snapshot-32m.bin", "bytes": len(snapshot), "sha256": sha(snapshot)},
        "rollback": {"file": "rollback-app.bin", "offset": APP_START,
                     "bytes": len(plan.rollback), "sha256": sha(plan.rollback)},
        "candidate_write": {"offset": APP_START, "payload_bytes": len(build["candidate"]),
                            "erase_bytes": plan.metadata["erase_bytes"],
                            "end_exclusive": APP_START + plan.metadata["erase_bytes"]},
        "normal_boot_observed": True, "device_accessed": True, "device_written": False,
        "flash_written": False, "nvs_written": False, "assets_written": False,
        "bootloader_written": False, "partition_table_written": False, "c61_accessed": False,
    }
    _json(folder / "preflight.json", receipt)
    return receipt


def prepare_live(folder: Path, run_label: str, *, device_factory=None) -> dict:
    folder = _safe_run_folder(folder, run_label)
    build = baseline.load_e3c3b_probe_inputs()
    migration = baseline.migrated.load_migrated_baseline()
    expected_identity = migration["identity_sha256"]
    folder.mkdir()
    device = device_factory(folder, expected_identity) if device_factory else ReadOnlyAppDevice(folder, expected_identity)
    observation = {"schema": "deskbot.continuous_mmap.device_observation.e3c3b.v1",
                   "run_label": run_label, "target": "body_COM4", "identity_sha256": None,
                   "snapshot_bytes": 0, "snapshot_sha256": None, "baseline_validated": False,
                   "normal_boot_observed": False, "device_written": False}
    facts, snapshot, failure = None, None, None
    try:
        observed = device.identity()
        observation["identity_sha256"] = observed
        if observed != expected_identity:
            raise ValueError("COM4 identity differs from the migrated body")
        print("COM4 identity verified; reading 32 one-MiB snapshot blocks.", flush=True)
        snapshot = device.read_full()
        if device.identity() != expected_identity:
            raise ValueError("COM4 identity changed during snapshot read")
        if not isinstance(snapshot, bytes) or len(snapshot) != FLASH_BYTES:
            raise ValueError("complete 32 MiB snapshot required")
        write_new(folder / "snapshot-32m.bin", snapshot)
        observation.update(snapshot_bytes=len(snapshot), snapshot_sha256=sha(snapshot))
        facts = baseline._validate_snapshot(snapshot, observed, migration, build["accepted"])
        observation["baseline_validated"] = True
    except BaseException as error:
        failure = error
        observation["preparation_error"] = type(error).__name__ + ": " + str(error)
    finally:
        # A failed read can leave the original App in ROM download mode.
        # Boot cleanup is reported independently and cannot turn failure into pass.
        try:
            if device.boot_original() is not True:
                raise ValueError("normal E3c-3b boot was not observed")
            observation["normal_boot_observed"] = True
        except BaseException as error:
            observation["boot_error"] = type(error).__name__ + ": " + str(error)
            if failure is None:
                failure = error
        _json(folder / "device-observation.json", observation)
    if failure is not None:
        raise failure
    return _persist(folder, run_label, snapshot, facts, build)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    args = parser.parse_args()
    result = prepare_live(ROOT / "tmp" / args.run_label, args.run_label)
    print(json.dumps({key: result[key] for key in ("result", "run_label", "plan_id", "device_written")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
