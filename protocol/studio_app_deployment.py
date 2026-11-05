"""App-only installation with a same-run backup and resumable rollback."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

START, PARTITION, FLASH = 0x10000, 0x5DC000, 0x2000000
RECOVERY_PHASES = frozenset({"restore_required", "candidate_verified", "installed",
                             "restoring", "needs_recovery", "restored"})


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class RecoveryPlan:
    metadata: dict
    snapshot: bytes

    @property
    def id(self):
        return sha(canonical(self.metadata))

    @property
    def rollback(self):
        return self.snapshot[START:START + self.metadata["erase_bytes"]]

    def validate(self):
        m = self.metadata
        names = {"schema", "edition", "offset", "erase_bytes", "original_bytes", "candidate_bytes",
                 "identity_sha256", "snapshot_sha256", "candidate_sha256", "rollback_sha256",
                 "write_payload_sha256"}
        if (not isinstance(m, dict) or set(m) != names
                or m.get("schema") != "deskbot.studio_app_plan/v1" or m.get("edition") not in ("E3b", "E3c")
                or m.get("offset") != START or len(self.snapshot) != FLASH
                or any(type(m.get(key)) is not int or not 0 < m[key] <= PARTITION
                       for key in ("original_bytes", "candidate_bytes", "erase_bytes"))
                or m["erase_bytes"] % 4096
                or m["erase_bytes"] != (max(m["original_bytes"], m["candidate_bytes"]) + 4095) // 4096 * 4096):
            raise ValueError("App plan geometry differs")
        for key in names:
            if key.endswith("_sha256") and (not isinstance(m[key], str)
                                             or re.fullmatch(r"[a-f0-9]{64}", m[key]) is None):
                raise ValueError("invalid plan digest")
        for name, raw in (("snapshot", self.snapshot), ("rollback", self.rollback)):
            if m.get(name + "_sha256") != sha(raw):
                raise ValueError(name + " digest differs")
        if self.rollback[0] != 0xE9:
            raise ValueError("original App image invalid")


@dataclass(frozen=True)
class Plan(RecoveryPlan):
    image: bytes

    @property
    def candidate(self):
        return self.image + self.rollback[len(self.image):]

    def validate(self):
        super().validate()
        if len(self.image) != self.metadata["candidate_bytes"] or not self.image or self.image[0] != 0xE9:
            raise ValueError("candidate image invalid")
        for name, raw in (("candidate", self.image), ("write_payload", self.candidate)):
            if self.metadata[name + "_sha256"] != sha(raw):
                raise ValueError(name + " digest differs")


def prepare(snapshot, image, identity, original_bytes, *, edition="E3b"):
    if len(snapshot) != FLASH or not image or not 0 < original_bytes <= PARTITION:
        raise ValueError("complete snapshot and App images required")
    span = (max(original_bytes, len(image)) + 4095) // 4096 * 4096
    if span > PARTITION:
        raise ValueError("candidate exceeds App partition")
    rollback = snapshot[START:START + span]
    metadata = {"schema": "deskbot.studio_app_plan/v1", "edition": edition, "offset": START,
                "erase_bytes": span, "original_bytes": original_bytes, "candidate_bytes": len(image),
                "identity_sha256": identity, "snapshot_sha256": sha(snapshot),
                "candidate_sha256": sha(image), "rollback_sha256": sha(rollback),
                "write_payload_sha256": sha(image + rollback[len(image):])}
    plan = Plan(metadata, snapshot, image)
    plan.validate()
    return plan


def persist(folder, plan):
    plan.validate()
    for name, raw in (("snapshot-32m.bin", plan.snapshot), ("candidate-app.bin", plan.image),
                      ("rollback-app-union.bin", plan.rollback)):
        write_new(folder / name, raw)
    write_new(folder / "plan.json", canonical({"plan_id": plan.id, "metadata": plan.metadata}))


def _read(folder, name):
    path = folder / name
    if folder.is_symlink() or path.is_symlink():
        raise ValueError("unsafe plan path")
    return path.read_bytes()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate saved key")
        result[key] = value
    return result


def load_recovery(folder):
    """Read only the original backup and journal plan; no candidate/build input."""
    saved = json.loads(_read(folder, "plan.json"), object_pairs_hook=_unique)
    if not isinstance(saved, dict) or set(saved) != {"metadata", "plan_id"}:
        raise ValueError("invalid saved plan")
    plan = RecoveryPlan(saved["metadata"], _read(folder, "snapshot-32m.bin"))
    plan.validate()
    if saved["plan_id"] != plan.id or _read(folder, "rollback-app-union.bin") != plan.rollback:
        raise ValueError("saved recovery bytes differ")
    return plan


def load(folder):
    original = load_recovery(folder)
    plan = Plan(original.metadata, original.snapshot, _read(folder, "candidate-app.bin"))
    plan.validate()
    return plan


class Journal:
    def __init__(self, folder, plan):
        self.path, self.plan = folder / "deployment-state.json", plan

    def load(self):
        if not self.path.exists():
            return None
        value = json.loads(_read(self.path.parent, self.path.name), object_pairs_hook=_unique)
        if (not isinstance(value, dict) or value.get("plan_id") != self.plan.id
                or not isinstance(value.get("phase"), str)):
            raise ValueError("journal belongs to another plan")
        return value

    def save(self, phase, **details):
        # Interrupted temporary files are retained; the previous committed
        # journal remains authoritative and another recovery can proceed.
        temp = self.path.with_suffix(".new-" + uuid.uuid4().hex)
        write_new(temp, canonical({"plan_id": self.plan.id, "phase": phase, **details}))
        os.replace(temp, self.path)


def compare(plan, raw, payload, *, allow_runtime_nvs=False):
    if len(raw) != FLASH:
        raise ValueError("full readback required")
    end = START + len(plan.rollback)
    expected = plan.snapshot[:START] + payload + plan.snapshot[end:]
    if allow_runtime_nvs:
        equal = raw[:0x9000] == expected[:0x9000] and raw[0xD000:] == expected[0xD000:]
    else:
        equal = raw == expected
    if not equal:
        raise ValueError("Flash readback differs from exact expected bytes")
    return {"sha256": sha(raw), "static_outside_app_equal": True,
            "nvs_equal": raw[0x9000:0xD000] == plan.snapshot[0x9000:0xD000]}


def recover(plan, device, journal):
    plan.validate()
    state = journal.load()
    if state is None or state["phase"] not in RECOVERY_PHASES:
        raise ValueError("no interrupted deployment to recover")
    try:
        if device.identity() != plan.metadata["identity_sha256"]:
            raise ValueError("recovery device differs")
        journal.save("restoring")
        device.write_app(plan.rollback)
        facts = device.verify_image(plan.rollback, allow_runtime_nvs=True)
        boot = device.boot(False)
        journal.save("restored", readback=facts, boot=boot)
        return {"result": "original_restored", "readback": facts, "boot": boot}
    except BaseException as error:
        journal.save("needs_recovery", error_type=type(error).__name__)
        raise


def install(plan, device, journal):
    if not isinstance(plan, Plan):
        raise ValueError("installation requires a complete candidate plan")
    plan.validate()
    if journal.load() is not None:
        raise ValueError("existing deployment requires inspect/recover")
    if device.identity() != plan.metadata["identity_sha256"]:
        raise ValueError("deployment device differs")
    if not device.verify_original():
        raise ValueError("App changed after backup")
    journal.save("prepared")
    journal.save("restore_required")
    try:
        device.write_app(plan.candidate)
        facts = device.verify_image(plan.candidate, allow_runtime_nvs=False)
        journal.save("candidate_verified", readback=facts)
        boot = device.boot(True)
        journal.save("installed", readback=facts, boot=boot, hardware_acceptance="pending")
        return {"result": "candidate_installed", "readback": facts, "boot": boot,
                "hardware_acceptance": "pending"}
    except BaseException:
        recover(plan, device, journal)
        raise
