"""File-backed Flash simulator. No serial, esptool, network or firmware imports."""
from __future__ import annotations

import json
import os
from pathlib import Path

from protocol.storage_upper_migration_transaction import (
    FLASH_BYTES, MODE, TABLE_BYTES, TABLE_START, UPPER_BYTES, UPPER_START,
    Journal, canonical, load_plan, read_file, sha, write_new,
)


class SimulatedPowerLoss(BaseException):
    """Bypass normal error recovery to exercise a new-process recovery entry."""


def synthetic_snapshot(old_table: bytes, old_assets: bytes) -> bytes:
    if len(old_table) != TABLE_BYTES or len(old_assets) != 0x5DC000:
        raise ValueError("retained legacy table and resource bytes required")
    data = bytearray(b"\xff" * FLASH_BYTES)
    data[TABLE_START:TABLE_START + TABLE_BYTES] = old_table
    data[0x5EC000:0xBC8000] = old_assets
    marker = b"SIMULATION ONLY - NOT FIRMWARE!!!"
    data[0x10000:0x10000 + 32] = marker[:32].ljust(32, b"!")
    data[0xBC8000:0xC90000] = b"M" * 0xC8000
    # Non-FF contents ensure that recovery cannot substitute an erase.
    data[UPPER_START:UPPER_START + UPPER_BYTES] = bytes(range(256)) * (UPPER_BYTES // 256)
    data[0x1800000:FLASH_BYTES] = b"R" * 0x800000
    return bytes(data)


class FileFlash:
    simulation_only = True

    def __init__(self, folder: Path, *, interrupt_at: str | None = None):
        self.folder = folder
        self.path = folder / "flash-simulation.bin"
        self.plan = load_plan(folder)
        self.journal = Journal(folder, self.plan)
        descriptor = json.loads(read_file(folder, "simulation-device.json", 4096))
        if descriptor != {"schema": "deskbot.flash_simulator.v1", "mode": MODE,
                          "identity_sha256": self.plan.metadata["identity_sha256"],
                          "plan_id": self.plan.plan_id}:
            raise ValueError("invalid simulation device descriptor")
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size != FLASH_BYTES:
            raise ValueError("complete simulation Flash file required")
        self.interrupt_at, self.interrupted = interrupt_at, False
        self.writes: list[dict] = []
        self.boots: list[str] = []

    @classmethod
    def create(cls, folder: Path, raw: bytes, *, interrupt_at: str | None = None):
        plan = load_plan(folder)
        if len(raw) != FLASH_BYTES or sha(raw) != plan.metadata["snapshot_sha256"]:
            raise ValueError("simulation snapshot differs from plan")
        write_new(folder / "flash-simulation.bin", raw)
        write_new(folder / "simulation-device.json", canonical({
            "schema": "deskbot.flash_simulator.v1", "mode": MODE,
            "identity_sha256": plan.metadata["identity_sha256"], "plan_id": plan.plan_id,
        }) + b"\n")
        return cls(folder, interrupt_at=interrupt_at)

    def identity(self) -> str:
        return self.plan.metadata["identity_sha256"]

    def read_flash(self, offset: int, size: int) -> bytes:
        if type(offset) is not int or type(size) is not int or offset < 0 or size <= 0 or offset + size > FLASH_BYTES:
            raise ValueError("invalid simulation read range")
        with self.path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(size)
        if len(raw) != size:
            raise ValueError("short simulation read")
        return raw

    def read_full(self) -> bytes:
        return self.read_flash(0, FLASH_BYTES)

    def write_flash(self, offset: int, raw: bytes) -> None:
        state = self.journal.load()
        phase = state["phase"] if state else ""
        permitted = {
            "upper_write_intent": (UPPER_START, self.plan.metadata["candidate_upper_sha256"]),
            "table_write_intent": (TABLE_START, self.plan.metadata["candidate_table_sha256"]),
            "restore_table_intent": (TABLE_START, self.plan.metadata["original_table_sha256"]),
            "restore_upper_intent": (UPPER_START, self.plan.metadata["original_upper_sha256"]),
        }
        size = {TABLE_START: TABLE_BYTES, UPPER_START: UPPER_BYTES}.get(offset)
        if phase not in permitted or permitted[phase] != (offset, sha(raw)) or len(raw) != size:
            raise ValueError("simulator write is outside the persisted intent")
        record = {"phase": phase, "offset": offset, "bytes": len(raw), "sha256": sha(raw),
                  "bytes_written": 0, "interrupted": False}
        self.writes.append(record)
        with self.path.open("r+b") as handle:
            handle.seek(offset)
            handle.write(b"\xff" * len(raw))
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(offset)
            if self.interrupt_at == phase and not self.interrupted:
                # The real table's entries fit well inside half a sector.
                # Stop within the entry area so its MD5/footer is still missing.
                prefix = 128 if offset == TABLE_START else len(raw) // 2
                handle.write(raw[:prefix])
                handle.flush()
                os.fsync(handle.fileno())
                record.update(bytes_written=prefix, interrupted=True)
                self.interrupted = True
                raise SimulatedPowerLoss("simulated interrupted sector write: " + phase)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            record["bytes_written"] = len(raw)

    def boot(self, target: str) -> bool:
        self.boots.append(target)
        if target not in {"candidate", "original"}:
            raise ValueError("unknown simulation boot target")
        expected = self.plan.metadata[("candidate" if target == "candidate" else "original") + "_table_sha256"]
        return sha(self.read_flash(TABLE_START, TABLE_BYTES)) == expected
