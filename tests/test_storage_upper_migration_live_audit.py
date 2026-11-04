"""Replay a migration into saved files; the audit never opens a serial port."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_upper_migration_transaction as tx
from protocol.storage_upper_migration_esptool import UpperMigrationEsptoolDevice
from protocol.storage_upper_migration_simulator import synthetic_snapshot


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = module_at("upper_live_audit_test", "scripts/audit-expression-e1-43c-live.py")
REPLAY = module_at("upper_live_replay_test", "tests/test_storage_upper_migration_esptool.py")


class LiveAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        upper = ROOT / "tmp/e135b-migration-20261029/candidate/assets.bin"
        if not upper.is_file():
            raise unittest.SkipTest("retained private E1.18 inputs required")
        old = ROOT / "tmp/e135b-migration-20261029/old"
        cls.snapshot = synthetic_snapshot((old / "partition-table.bin").read_bytes(),
                                          (old / "assets.bin").read_bytes())
        cls.upper = upper.read_bytes()
        cls.table = (ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin").read_bytes()

    def setUp(self):
        self.folder = ROOT / "tmp" / ("e143c-live-audit-" + uuid.uuid4().hex[:16])
        self.addCleanup(shutil.rmtree, self.folder, True)
        plan = tx.make_plan(self.snapshot, self.upper, self.table,
                            identity_sha256=REPLAY.parse_identity(REPLAY.FLASH_LOG, REPLAY.SECURITY_LOG).digest,
                            run_label=self.folder.name, mode=tx.LIVE_MODE, authorization=tx.DIRECT_AUTHORIZATION)
        tx.persist_plan(self.folder, plan)
        runner = REPLAY.ReplayRunner(self.snapshot, plan.candidate_table)
        device = UpperMigrationEsptoolDevice(self.folder, runner=runner, python=Path(sys.executable), write_enabled=True)
        result = tx.execute(self.folder, device, initial_snapshot=self.snapshot)
        (self.folder / "execute-result.json").write_text(json.dumps(result), encoding="utf-8")

    def last_zero_block(self):
        return sorted((self.folder / "captures").glob("*-migration-full-backup-00000000-*.bin"))[-1]

    def test_completed_replay_has_exact_resources_protected_ranges_and_boot(self):
        result = AUDIT.audit(self.folder.name)
        self.assertEqual(result["result"], "migration_readback_and_boot_passed_interaction_pending")
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertEqual(len(result["final_readback_blocks"]), 32)
        self.assertTrue(all(row["verified"] for row in result["protected_static_ranges"]))
        self.assertTrue(result["resources"]["package_unchanged"])

    def test_audit_rejects_a_missing_final_block(self):
        self.last_zero_block().unlink()
        with self.assertRaises(ValueError):
            AUDIT.audit(self.folder.name)

    def test_audit_rejects_changed_protected_byte(self):
        path = self.last_zero_block()
        raw = bytearray(path.read_bytes())
        raw[0x10000] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "static protected range changed"):
            AUDIT.audit(self.folder.name)


if __name__ == "__main__":
    unittest.main()
