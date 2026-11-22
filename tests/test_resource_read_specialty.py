"""Real App transactions and probe parsing with simulated device failures."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import resource_read_specialty as task
from protocol import studio_app_deployment as tx
from protocol.storage_continuous_mmap import reference_for, extract_records
from test_storage_continuous_mmap import fixture, encode

ROOT = Path(__file__).resolve().parents[1]


def capture(snapshot):
    rows, _ = fixture()
    ref = reference_for(snapshot[0x1000000:0x1800000], identity_sha256=task.catalog.IDENTITY_SHA,
                        run_label="synthetic", source_sha256=tx.sha(snapshot))
    for row in rows:
        p = row["payload"]
        if row["kind"] == "block":
            p.update(read_sha256=ref["block_sha256"][p["index"]], mmap_sha256=ref["block_sha256"][p["index"]])
        elif row["kind"] == "sample":
            p.update(head_hex=ref["head_hex"], tail_hex=ref["tail_hex"])
        elif row["kind"] == "summary":
            p.update(read_sha256=ref["sha256"], mmap_sha256=ref["sha256"])
    return "\n".join(encode(row["kind"], row["payload"]) for row in rows)


class MemoryDevice:
    def __init__(self, memory, folder, identity, plan=None):
        self.m, self.folder, self.expected_identity, self.plan = memory, folder, identity, plan
        self.probe_log = memory.log
        memory.opened += 1

    def identity(self):
        return "0"*64 if self.m.fail=="identity" else self.expected_identity

    def read_reference(self, inputs):
        if self.m.fail=="read":
            raise OSError("injected read failure")
        return bytes(self.m.raw)

    def verify_original(self):
        tx.compare(self.plan, bytes(self.m.raw), self.plan.rollback)
        return True

    def write_app(self, raw):
        state = tx.Journal(self.folder, self.plan).load()
        assert state["phase"] in {"restore_required", "restoring"}
        self.m.writes.append((tx.START, len(raw)))
        if len(self.m.writes)==1 and self.m.fail=="write":
            self.m.raw[tx.START:tx.START+128] = raw[:128]
            raise OSError("injected partial write")
        if state["phase"]=="restoring" and self.m.fail=="restore":
            raise OSError("injected restore failure")
        self.m.raw[tx.START:tx.START+len(raw)] = raw

    def verify_image(self, raw, *, allow_runtime_nvs):
        if len(self.m.writes)==1 and self.m.fail=="readback":
            raise OSError("injected readback failure")
        return tx.compare(self.plan, bytes(self.m.raw), raw, allow_runtime_nvs=allow_runtime_nvs)

    def boot(self, candidate):
        self.m.boots.append(candidate)
        if candidate and self.m.fail=="boot":
            raise ValueError("injected boot failure")
        return dict(normal_boot=True, firmware_matches_candidate=True)


class ResourceReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = task.load_inputs()
        cls.log = capture(cls.inputs["snapshot"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="resource-read-test-", dir=ROOT/"tmp")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)/"resource-read-20261108-test"
        self.m = SimpleNamespace(raw=bytearray(self.inputs["snapshot"]), log=self.log,
                                 writes=[], boots=[], opened=0, fail=None)

    def factory(self, folder, identity, plan=None):
        return MemoryDevice(self.m, folder, identity, plan)

    def prepare(self):
        return task.prepare(self.folder, self.factory, inputs=self.inputs)

    def execute(self, plan_id):
        return task.execute(self.folder, plan_id, self.factory, inputs=self.inputs)

    def test_fixed_installed_and_probe_evidence_are_loaded(self):
        self.assertEqual(tx.sha(self.inputs["original"]),task.catalog.APP_SHA)
        self.assertEqual(tx.sha(self.inputs["image"]),task.probe.PROBE_APP_SHA256)
        self.assertEqual(len(self.inputs["snapshot"]),tx.FLASH)

    def test_prepare_preserves_fresh_nvs_and_full_original_union(self):
        self.m.raw[0x9000] ^= 1
        self.prepare()
        p=tx.load(self.folder)
        self.assertEqual(p.snapshot,bytes(self.m.raw))
        self.assertEqual(p.rollback,bytes(self.m.raw[0x10000:0x460000]))
        self.assertEqual(self.m.writes,[])

    def test_static_or_current_app_change_stops_preparation(self):
        for offset in (0x8000,0x10000,0x460000,0x1000000):
            with self.subTest(offset=offset):
                self.m.raw[offset]^=1
                with self.assertRaisesRegex(ValueError,"Flash differs"):
                    self.prepare()
                self.assertEqual(self.m.writes,[])
                self.folder.rmdir()
                self.m.raw[offset]^=1

    def test_identity_and_read_failure_do_not_write(self):
        for fail in ("identity","read"):
            self.m.fail=fail
            with self.assertRaises((ValueError,OSError)):
                self.prepare()
            self.assertEqual(self.m.writes,[])
            self.folder.rmdir()

    def test_plan_mismatch_does_not_open_execution_device(self):
        self.prepare()
        opened=self.m.opened
        with self.assertRaisesRegex(ValueError,"plan ID"):
            self.execute("0"*64)
        self.assertEqual(self.m.opened,opened)

    def test_automatic_probe_and_exact_original_restore(self):
        p=self.prepare()
        result=self.execute(p["plan_id"])
        self.assertEqual(result["result"],"automatic_mapping_passed")
        self.assertTrue(result["restored"])
        self.assertTrue(result["runtime"]["probe_passed"])
        self.assertEqual(result["interaction"],"pending")
        self.assertEqual(self.m.raw,self.inputs["snapshot"])
        self.assertEqual(self.m.writes,[(tx.START,task.SPAN)]*2)

    def test_candidate_write_readback_and_boot_failures_restore(self):
        for fail in ("write","readback","boot"):
            with self.subTest(fail=fail):
                folder=self.folder.with_name(self.folder.name+"-"+fail)
                self.folder=folder
                p=self.prepare()
                self.m.fail=fail
                with self.assertRaises((ValueError,OSError)):
                    self.execute(p["plan_id"])
                self.assertEqual(self.m.raw,self.inputs["snapshot"])
                self.assertEqual(tx.Journal(folder,tx.load_recovery(folder)).load()["phase"],"restored")
                self.m.writes=[]
                self.m.fail=None

    def test_bad_or_missing_probe_records_restore(self):
        p=self.prepare()
        self.m.log="not a diagnostic"
        with self.assertRaises(ValueError):
            self.execute(p["plan_id"])
        self.assertEqual(self.m.raw,self.inputs["snapshot"])

    def test_false_probe_result_restores(self):
        p=self.prepare()
        rows=extract_records(self.log)
        rows[-1]["payload"]["probe_passed"]=False
        self.m.log="\n".join(encode(row["kind"],row["payload"]) for row in rows)
        with self.assertRaisesRegex(ValueError,"did not pass"):
            self.execute(p["plan_id"])
        self.assertEqual(self.m.raw,self.inputs["snapshot"])

    def test_recovery_can_resume_without_candidate_or_builds(self):
        p=self.prepare()
        self.m.fail="restore"
        with self.assertRaises(OSError):
            self.execute(p["plan_id"])
        self.assertEqual(json.loads((self.folder/"execute-result.json").read_bytes())["result"],"needs_recovery")
        (self.folder/"candidate-app.bin").unlink()
        self.m.fail=None
        with patch.object(task,"load_inputs",side_effect=AssertionError("candidate accessed")):
            result=task.recover(self.folder,self.factory)
        self.assertEqual(result["result"],"original_restored")
        self.assertEqual(self.m.raw,self.inputs["snapshot"])

    def test_changed_backup_stops_recovery_before_device(self):
        self.prepare()
        path=self.folder/"rollback-app-union.bin"
        raw=path.read_bytes()
        path.write_bytes(bytes([raw[0]^1])+raw[1:])
        opened=self.m.opened
        with self.assertRaises(ValueError):
            task.recover(self.folder,self.factory)
        self.assertEqual(self.m.opened,opened)

    def test_recomputed_plan_with_changed_erase_union_tail_is_rejected(self):
        modified=bytearray(self.inputs["snapshot"])
        modified[0x45F031]^=1
        plan=tx.prepare(bytes(modified),self.inputs["image"],task.catalog.IDENTITY_SHA,
                        task.catalog.APP_SIZE,edition="E3c")
        self.folder.mkdir()
        tx.persist(self.folder,plan)
        opened=self.m.opened
        with self.assertRaisesRegex(ValueError,"rollback"):
            task.recover(self.folder,self.factory)
        self.assertEqual(self.m.opened,opened)

    def test_changed_receipt_stops_execution_before_device(self):
        p=self.prepare()
        path=self.folder/"preflight.json"
        row=json.loads(path.read_bytes()); row["source"]["probe_sha256"]="0"*64
        path.write_text(json.dumps(row),encoding="utf-8")
        opened=self.m.opened
        with self.assertRaisesRegex(ValueError,"preflight"):
            self.execute(p["plan_id"])
        self.assertEqual(self.m.opened,opened)

    def test_repeat_execution_is_rejected_before_device(self):
        p=self.prepare()
        self.execute(p["plan_id"])
        opened=self.m.opened
        with self.assertRaises(FileExistsError):
            self.execute(p["plan_id"])
        self.assertEqual(self.m.opened,opened)


if __name__=="__main__":
    unittest.main()
