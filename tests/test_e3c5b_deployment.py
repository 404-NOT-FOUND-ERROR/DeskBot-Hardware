"""Pinned E3c-5b preparation and CLI transactions with an in-memory device."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c5b_deployment as reviewed

ROOT = reviewed.ROOT
spec = importlib.util.spec_from_file_location("e3c5b_deployment_test", ROOT / "scripts/deploy-e3c5b-neutral.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class MemoryDevice:
    """Device boundary only; production plans, journal and CLI remain active."""
    def __init__(self, memory, folder, identity, plan=None):
        self.memory, self.folder, self.expected_identity, self.plan = memory, folder, identity, plan
        memory.events.append(("open", plan is not None))

    def identity(self):
        self.memory.events.append(("identity",))
        return "f" * 64 if self.memory.fail == "identity" else self.expected_identity

    def read_reference(self, reference):
        self.memory.events.append(("reference",))
        return bytes(self.memory.raw)

    def verify_original(self):
        tx.compare(self.plan, bytes(self.memory.raw), self.plan.rollback)
        return True

    def write_app(self, raw):
        self.memory.events.append(("write", tx.START, len(raw)))
        self.memory.writes.append(raw)
        if self.memory.fail == "write" and len(self.memory.writes) == 1:
            self.memory.raw[tx.START:tx.START + 128] = raw[:128]
            raise OSError("injected partial write")
        self.memory.raw[tx.START:tx.START + len(raw)] = raw

    def verify_image(self, raw, *, allow_runtime_nvs):
        current = bytes(self.memory.raw)
        if self.memory.fail == "readback" and len(self.memory.writes) == 1:
            current = current[:tx.START] + bytes([current[tx.START] ^ 1]) + current[tx.START + 1:]
        return tx.compare(self.plan, current, raw, allow_runtime_nvs=allow_runtime_nvs)

    def boot(self, candidate):
        self.memory.events.append(("boot", candidate))
        if candidate and self.memory.fail == "boot":
            raise ValueError("injected candidate boot failure")
        self.memory.boots += 1
        return dict(normal_boot=True,
                    hello=dict(boot_id="boot-" + str(self.memory.boots), device_id="memory-device"))


class NeutralDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = reviewed.load_reference()
        image = reviewed.reviewed_candidate()
        # Evidence validation has dedicated tests below. Transaction simulations
        # reuse its fixed candidate while exercising the production transaction.
        cls.evidence = dict(image=image,
            candidate=dict(variant="neutral_expression", bytes=reviewed.APP_SIZE,
                           sha256=reviewed.APP_SHA, app_elf_sha256=reviewed.APP_ELF_SHA),
            build_report_sha256=reviewed.BUILD_REPORT_SHA,
            offline_report_sha256=reviewed.OFFLINE_REPORT_SHA,
            path_audit_sha256=reviewed.PATCH_SHA)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3c5b-gate-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "tmp").mkdir()
        self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]),
                                      events=[], writes=[], boots=0, fail=None)

    def factory(self, folder, identity, plan=None):
        return MemoryDevice(self.memory, folder, identity, plan)

    def offline(self):
        folder = self.folder / "offline"
        with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice") as device:
            result = deploy.base.prepare_offline(folder)
        device.assert_not_called()
        self.assertFalse(result["hardware_accessed"])
        return folder

    def live(self, source):
        target = self.folder / "live"
        with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            deploy.base.prepare_live(target, source)
        self.assertEqual(self.memory.writes, [])
        return target

    def execute(self, phase, folder):
        with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            return deploy.base.run(phase, folder)

    def test_candidate_and_actual_build_native_evidence_are_pinned(self):
        with patch.object(reviewed.previous, "check_build_evidence",
                          side_effect=AssertionError("historical host report accessed")):
            evidence = reviewed.check_build_evidence()
        self.assertEqual(evidence, self.evidence)
        self.assertEqual(tx.sha(evidence["image"]), reviewed.APP_SHA)

    def test_changed_tested_source_is_rejected(self):
        report = json.loads((ROOT / reviewed.OFFLINE_REPORT).read_bytes())
        selected = next(iter(report["source_sha256"]))
        original = reviewed._read

        def read(root, name):
            raw = original(root, name)
            return raw + b"\n" if Path(name).as_posix() == selected else raw

        with patch.object(reviewed, "_read", side_effect=read), \
             self.assertRaisesRegex(ValueError, "tested source changed"):
            reviewed.check_build_evidence()

    def test_reference_reconstructs_accepted_e3c4b_and_extra_tail(self):
        previous = tx.load(ROOT / reviewed.LIVE_REFERENCE)
        end = tx.START + len(previous.rollback)
        expected = previous.snapshot[:tx.START] + previous.candidate + previous.snapshot[end:]
        self.assertEqual(self.reference["snapshot"], expected)
        self.assertEqual(tx.sha(expected), reviewed.REFERENCE_SNAPSHOT_SHA)
        self.assertEqual(tx.sha(self.reference["app_union"]), reviewed.ORIGINAL_UNION_SHA)
        self.assertEqual(self.reference["app_union"][:reviewed.ORIGINAL_SIZE], previous.image)
        self.assertEqual(expected[end:tx.START + reviewed.ERASE_BYTES], b"\xff" * 20480)

    def test_changed_acceptance_record_is_rejected(self):
        original = reviewed._read

        def read(root, name):
            raw = original(root, name)
            return raw + b"\n" if Path(name) == reviewed.ACCEPTANCE else raw

        with patch.object(reviewed, "_read", side_effect=read), \
             self.assertRaisesRegex(ValueError, "pinned E3c-5b evidence changed"):
            reviewed.load_reference()

    def test_reference_requires_installed_e3c4b(self):
        with patch.object(tx.Journal, "load", return_value=dict(phase="restored")), \
             self.assertRaisesRegex(ValueError, "accepted E3c-4b installation"):
            reviewed.load_reference()

    def test_current_reference_allows_nvs_and_rejects_static_changes(self):
        changed = bytearray(self.reference["snapshot"])
        changed[0x9000] ^= 1
        reviewed.check_current(bytes(changed), self.reference)
        for offset in (0, 0x8000, 0xD000, tx.START, 0x459000, 0xBC8000, 0x1000000):
            changed = bytearray(self.reference["snapshot"])
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                reviewed.check_current(bytes(changed), self.reference)

    def test_changed_candidate_is_rejected(self):
        changed = bytearray(self.evidence["image"])
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "App changed"):
            reviewed.reviewed_candidate(bytes(changed))

    def test_offline_plan_keeps_exact_union_and_e3c4b_rollback(self):
        folder = self.offline()
        plan = reviewed.check_plan(tx.load(folder), reference=self.reference)
        self.assertEqual(plan.metadata["offset"], 0x10000)
        self.assertEqual(plan.metadata["erase_bytes"], 4_513_792)
        self.assertEqual(tx.START + len(plan.rollback), 0x45E000)
        self.assertEqual(plan.rollback, self.reference["app_union"])
        self.assertEqual(tx.sha(plan.candidate), reviewed.WRITE_PAYLOAD_SHA)
        self.assertEqual(plan.metadata["original_bytes"], reviewed.ORIGINAL_SIZE)

    def test_repeated_prepare_preserves_existing_files(self):
        folder = self.offline()
        before = (folder / "plan.json").read_bytes()
        with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             self.assertRaises(FileExistsError):
            deploy.base.prepare_offline(folder)
        self.assertEqual((folder / "plan.json").read_bytes(), before)

    def test_offline_plan_cannot_install(self):
        folder = self.offline()
        with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base.entry, "run") as run, \
             self.assertRaisesRegex(ValueError, "live preflight required"):
            deploy.base.run("install", folder)
        run.assert_not_called()

    def test_live_preparation_saves_current_nvs_without_writing(self):
        source = self.offline()
        self.memory.raw[0x9000] ^= 1
        target = self.live(source)
        plan = tx.load(target)
        self.assertEqual(plan.snapshot, bytes(self.memory.raw))
        self.assertNotEqual(plan.id, tx.load(source).id)
        self.assertEqual(plan.rollback, self.reference["app_union"])
        receipt = json.loads((target / "preflight.json").read_bytes())
        self.assertEqual(receipt["rollback_target"], "E3c-4b")
        self.assertFalse(receipt["device_written"])
        self.assertFalse(receipt["fresh_device_check_required_before_install"])

    def test_changed_extra_union_tail_stops_live_preparation(self):
        source = self.offline()
        self.memory.raw[0x459000] ^= 1
        with self.assertRaisesRegex(ValueError, "current Flash differs"):
            self.live(source)
        self.assertEqual(self.memory.writes, [])
        self.assertIn(("boot", False), self.memory.events)
        self.assertFalse((self.folder / "live/plan.json").exists())

    def test_changed_preflight_target_stops_install(self):
        target = self.live(self.offline())
        receipt_path = target / "preflight.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["target"] = "base_COM8"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "preflight receipt differs"):
            self.execute("install", target)
        self.assertEqual(self.memory.writes, [])

    def test_install_preserves_outside_app_and_leaves_candidate_running(self):
        target = self.live(self.offline())
        plan = tx.load(target)
        result = self.execute("install", target)
        self.assertEqual(result["result"], "candidate_installed")
        self.assertEqual(result["hardware_acceptance"], "pending")
        self.assertEqual(self.memory.writes, [plan.candidate])
        self.assertEqual(tx.Journal(target, plan).load()["phase"], "installed")
        tx.compare(plan, bytes(self.memory.raw), plan.candidate)

    def test_failed_write_readback_or_boot_restores_exact_original(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure):
                parent = self.folder / failure
                parent.mkdir()
                source, target = parent / "offline", parent / "live"
                self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]),
                                              events=[], writes=[], boots=0, fail=None)
                with patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
                     patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
                    deploy.base.prepare_offline(source)
                    deploy.base.prepare_live(target, source)
                    self.memory.fail = failure
                    with self.assertRaises((OSError, ValueError)):
                        deploy.base.run("install", target)
                plan = tx.load_recovery(target)
                self.assertEqual(bytes(self.memory.raw), plan.snapshot)
                self.assertEqual(self.memory.writes[-1], plan.rollback)
                self.assertEqual(tx.Journal(target, plan).load()["phase"], "restored")

    def test_recovery_works_without_candidate_build_or_prior_run_access(self):
        target = self.live(self.offline())
        self.execute("install", target)
        plan = tx.load_recovery(target)
        (target / "candidate-app.bin").unlink()
        with patch.object(reviewed, "check_build_evidence", side_effect=AssertionError("build accessed")), \
             patch.object(reviewed, "load_reference", side_effect=AssertionError("prior run accessed")), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            result = deploy.base.run("recover", target)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(bytes(self.memory.raw), plan.snapshot)
        self.assertEqual(self.memory.writes[-1], plan.rollback)

    def test_recovery_rejects_forged_extra_tail_even_with_recomputed_metadata(self):
        folder = self.offline()
        old = tx.load(folder)
        changed = bytearray(old.snapshot)
        changed[0x459000] ^= 1
        forged = tx.prepare(bytes(changed), old.image, old.metadata["identity_sha256"], reviewed.ORIGINAL_SIZE, edition="E3c")
        with self.assertRaisesRegex(ValueError, "rollback differs"):
            reviewed.check_plan(tx.RecoveryPlan(forged.metadata, forged.snapshot), candidate=False)

    def test_wrong_device_performs_no_install_write(self):
        target = self.live(self.offline())
        self.memory.fail = "identity"
        with self.assertRaisesRegex(ValueError, "deployment device differs"):
            self.execute("install", target)
        self.assertEqual(self.memory.writes, [])

    def test_cli_prepare_check_and_argument_boundaries(self):
        label = "e3c-live-20261108-e3c5b-cli"
        with patch.object(deploy, "ROOT", self.folder), \
             patch.object(reviewed, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice") as device, redirect_stdout(io.StringIO()) as output:
            deploy.main(["prepare", "--run", label])
            deploy.main(["check", "--run", label])
        device.assert_not_called()
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["phase"] for row in rows], ["prepared_offline", "checked_offline"])
        self.assertEqual(rows[0]["plan_id"], rows[1]["plan_id"])
        for args in (["prepare-live", "--run", label], ["check", "--run", label, "--from-run", label]):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), \
                 patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                deploy.main(args)

    def test_run_paths_and_entry_bindings_are_specific_to_e3c5b(self):
        self.assertEqual(deploy.folder_for("e3c-live-20261108-e3c5b-offline1").parent, ROOT / "tmp")
        for label in ("../outside", "e3c-live-20261108-e3c5b-../outside", "e3c-live-20261108-e3c4b-offline1"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                deploy.folder_for(label)
        self.assertIs(deploy.base.reviewed, reviewed)
        self.assertIs(deploy.base.entry.reviewed, reviewed)
        self.assertEqual(deploy.base.entry.ROLLBACK_TARGET, "E3c-4b")


if __name__ == "__main__":
    unittest.main()
