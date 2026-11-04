"""Persist and verify same-run CPU probe references; no device I/O."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from protocol.storage_upper_experiment import DeviceSnapshot, ExperimentPlan, sha, validate_snapshot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "deskbot.upper_pattern.write_snapshot.e1_43a7/v1"
FILES = {"low_page": ("low-page.bin", 65536), "boundary_lower": ("boundary-lower.bin", 16)}
LOW_RANGES = {"bootloader_reserved": (0, 0x8000), "partition_table": (0x8000, 0x9000),
              "nvs": (0x9000, 0xD000), "otadata": (0xD000, 0xF000), "phy_init": (0xF000, 0x10000)}


def _hash(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("snapshot binding requires lowercase SHA-256 values")
    return value


def _run(run_dir: Path) -> Path:
    root = (ROOT / "tmp").resolve()
    if (not isinstance(run_dir, Path) or not run_dir.is_absolute() or run_dir.is_symlink()
            or run_dir.resolve().parent != root
            or re.fullmatch(r"e143a7-[a-z0-9][a-z0-9-]{0,39}", run_dir.name) is None
            or not run_dir.is_dir()):
        raise ValueError("snapshot requires an existing E1.43a-7 run under repository tmp")
    return run_dir.resolve()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate snapshot manifest key")
        result[key] = value
    return result


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class ProbeReference:
    identity_sha256: str
    plan_id: str
    run_label: str
    manifest_sha256: str
    low_page: bytes
    boundary_lower: bytes

    @property
    def low_alias_sha256(self) -> str:
        return sha(self.low_page)

    @property
    def low_protected_sha256(self) -> str:
        return sha(self.low_page[:0x9000])

    def boundary_hex(self, pattern: bytes) -> str:
        return (self.boundary_lower + pattern[:16]).hex()


def persist_snapshot(run_dir: Path, plan: ExperimentPlan, snapshot: DeviceSnapshot) -> ProbeReference:
    run_dir = _run(run_dir)
    plan.validate()
    validate_snapshot(plan, snapshot)
    payloads = {"low_page": snapshot.runtime_low_page, "boundary_lower": snapshot.runtime_boundary_lower}
    for key, (_, length) in FILES.items():
        if not isinstance(payloads[key], bytes) or len(payloads[key]) != length:
            raise ValueError("write snapshot is missing the exact " + key + " bytes")
    for name, (start, end) in LOW_RANGES.items():
        if sha(payloads["low_page"][start:end]) != snapshot.protected_sha256[name]:
            raise ValueError("write snapshot low page disagrees with its protected range: " + name)
    directory = run_dir / "snapshot"
    # Exclusive creation also retains partial bundles instead of silently reusing them.
    directory.mkdir(exist_ok=False)
    facts = {}
    for key, (filename, length) in FILES.items():
        _write_new(directory / filename, payloads[key])
        facts[key] = {"file": filename, "bytes": length, "sha256": sha(payloads[key])}
    manifest = {"schema": SCHEMA, "identity_sha256": snapshot.identity_sha256,
                "plan_id": plan.plan_id, "run_label": run_dir.name, "files": facts}
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    _write_new(directory / "manifest.json", raw)
    return load_snapshot(run_dir, identity_sha256=plan.expected_identity_sha256, plan_id=plan.plan_id,
                         expected_manifest_sha256=sha(raw))


def load_snapshot(run_dir: Path, *, identity_sha256: str, plan_id: str,
                  expected_manifest_sha256: str) -> ProbeReference:
    run_dir = _run(run_dir)
    _hash(identity_sha256)
    _hash(plan_id)
    _hash(expected_manifest_sha256)
    directory = run_dir / "snapshot"
    path = directory / "manifest.json"
    if (directory.is_symlink() or directory.resolve().parent != run_dir
            or path.is_symlink() or not path.is_file() or path.stat().st_size > 4096):
        raise ValueError("same-run snapshot manifest is missing or unsafe")
    raw = path.read_bytes()
    if sha(raw) != expected_manifest_sha256:
        raise ValueError("snapshot manifest changed after the write-time binding")
    manifest = json.loads(raw.decode("ascii"), object_pairs_hook=_unique)
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "identity_sha256", "plan_id", "run_label", "files"}
            or manifest["schema"] != SCHEMA
            or manifest["identity_sha256"] != identity_sha256
            or manifest["plan_id"] != plan_id or manifest["run_label"] != run_dir.name):
        raise ValueError("snapshot identity, plan, run-label or schema mismatch")
    if not isinstance(manifest["files"], dict) or set(manifest["files"]) != set(FILES):
        raise ValueError("snapshot must contain the exact reference file set")
    payloads = {}
    for key, (filename, length) in FILES.items():
        facts = manifest["files"][key]
        if (not isinstance(facts, dict) or set(facts) != {"file", "bytes", "sha256"}
                or facts["file"] != filename or type(facts["bytes"]) is not int or facts["bytes"] != length):
            raise ValueError("snapshot reference geometry mismatch: " + key)
        _hash(facts["sha256"])
        file = directory / filename
        if file.is_symlink() or not file.is_file() or file.stat().st_size != length:
            raise ValueError("snapshot reference is missing or has a wrong length: " + key)
        payload = file.read_bytes()
        if len(payload) != length or sha(payload) != facts["sha256"]:
            raise ValueError("snapshot reference digest mismatch: " + key)
        payloads[key] = payload
    return ProbeReference(identity_sha256, plan_id, run_dir.name, sha(raw),
                          payloads["low_page"], payloads["boundary_lower"])
