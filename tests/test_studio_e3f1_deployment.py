"""Offline E1.45/E3f-1 plan composition and production App transaction tests."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as e3d2b
from protocol import studio_e3f1_deployment as deployment
from protocol import studio_resource_installation as resources

ROOT = deployment.ROOT


class MemoryDevice:
    simulation_only = True

    def __init__(self, memory, plan):
        self.memory, self.plan = memory, plan

    def identity(self):
        return self.plan.metadata["identity_sha256"]

    def verify_original(self):
        app.compare(self.plan, bytes(self.memory.raw), self.plan.rollback)
        return True

    def write_app(self, raw):
        self.memory.writes += 1
        if self.memory.fail == "write" and self.memory.writes == 1:
            self.memory.raw[app.START:app.START + 128] = raw[:128]
            raise OSError("injected partial App write")
        self.memory.raw[app.START:app.START + len(raw)] = raw

    def verify_image(self, raw, *, allow_runtime_nvs):
        current = bytes(self.memory.raw)
        if self.memory.fail == "readback" and self.memory.writes == 1:
            current = current[:app.START] + bytes([current[app.START] ^ 1]) + current[app.START + 1:]
        return app.compare(self.plan, current, raw, allow_runtime_nvs=allow_runtime_nvs)

    def boot(self, candidate):
        self.memory.boots.append(candidate)
        if candidate and self.memory.fail == "boot":
            raise ValueError("injected E3f-1 boot failure")
        return True


class MemoryResourceDevice:
    """Share the same Flash while exercising the independent resource transaction."""
    simulation_only = True

    def __init__(self, memory, plan):
        self.memory, self.plan = memory, plan
        self.writes = 0

    def identity(self):
        return self.plan.metadata["identity_sha256"]

    def read_full(self):
        return bytes(self.memory.raw)

    def write_assets(self, raw):
        self.writes += 1
        self.memory.raw[resources.START:resources.START + resources.SIZE] = raw

    def boot(self):
        return True


class E3f1DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build, cls.test_report, cls.candidate = deployment.inputs()
        cls.resource_plan = deployment.resources_ready()
        cls.after_resources = deployment.simulate_resource_install(cls.resource_plan)
        cls.plan = deployment.make_app_plan(cls.after_resources, cls.candidate,
                                            cls.resource_plan.metadata["identity_sha256"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3f1-transaction-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.memory = SimpleNamespace(raw=bytearray(self.after_resources), writes=0, boots=[], fail=None)
        self.device = MemoryDevice(self.memory, self.plan)

    def test_reviewed_build_resources_and_live_test_basis_are_pinned(self):
        self.assertEqual(self.build["baseline"], "E3d-2b")
        self.assertEqual(self.candidate, (ROOT / deployment.APP_BUILD).read_bytes())
        self.assertEqual(self.test_report["tests"], 232)
        self.assertEqual(resources.plan_baseline(self.resource_plan), "E3d-2b")
        self.assertEqual(self.resource_plan.metadata["mode"], "offline")

    def test_resource_scenario_changes_only_the_e1_45_partition(self):
        old, new = self.resource_plan.snapshot, self.after_resources
        start, end = resources.START, resources.START + resources.SIZE
        self.assertEqual(new[:start], old[:start])
        self.assertEqual(new[end:], old[end:])
        self.assertEqual(new[start:end], self.resource_plan.image)

    def test_sequential_plan_targets_e1_45_and_rolls_back_e3d2b(self):
        self.assertTrue(deployment.check_app_plan(self.plan, expected_snapshot=self.after_resources,
                                                  candidate=self.candidate))
        self.assertEqual(self.plan.metadata["identity_sha256"], self.resource_plan.metadata["identity_sha256"])
        self.assertEqual(self.plan.rollback[:e3d2b.APP_SIZE],
                         self.after_resources[app.START:app.START + e3d2b.APP_SIZE])
        self.assertEqual(self.plan.snapshot[resources.START:resources.START + resources.SIZE],
                         self.resource_plan.image)

    def test_app_erase_range_is_exactly_the_candidate_union(self):
        self.assertEqual(self.plan.metadata["offset"], 0x10000)
        self.assertEqual(self.plan.metadata["erase_bytes"], 4_521_984)
        self.assertEqual(app.START + self.plan.metadata["erase_bytes"], deployment.APP_ERASE_END)

    def test_bad_candidate_digest_is_rejected(self):
        changed = bytearray(self.candidate)
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "E3f1_offline_plan_inputs_mismatch"):
            deployment.make_app_plan(self.after_resources, bytes(changed), self.plan.metadata["identity_sha256"])

    def test_old_e118_snapshot_is_not_an_e3f1_app_base(self):
        with self.assertRaisesRegex(ValueError, "E1.45_must_be_installed"):
            deployment.make_app_plan(self.resource_plan.snapshot, self.candidate,
                                     self.plan.metadata["identity_sha256"])

    def test_wrong_e3d2b_original_app_is_rejected(self):
        changed = bytearray(self.after_resources)
        changed[app.START] ^= 1
        with self.assertRaisesRegex(ValueError, "rollback_source_mismatch"):
            deployment.make_app_plan(bytes(changed), self.candidate, self.plan.metadata["identity_sha256"])

    def test_candidate_build_report_must_match_pin(self):
        original = deployment._read

        def altered(root, path):
            return b"{}" if path == deployment.BUILD_REPORT else original(root, path)

        with patch.object(deployment, "_read", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "reviewed_E3f1_evidence_changed"):
                deployment.inputs()

    def test_successful_production_transaction_keeps_e145(self):
        journal = app.Journal(self.folder, self.plan)
        result = app.install(self.plan, self.device, journal)
        self.assertEqual(result["result"], "candidate_installed")
        self.assertEqual(result["hardware_acceptance"], "pending")
        self.assertEqual(self.memory.boots, [True])
        self.assertEqual(bytes(self.memory.raw[resources.START:resources.START + resources.SIZE]),
                         self.resource_plan.image)
        app.compare(self.plan, bytes(self.memory.raw), self.plan.candidate)

    def test_manual_app_rollback_keeps_e145_and_runtime_nvs(self):
        journal = app.Journal(self.folder, self.plan)
        app.install(self.plan, self.device, journal)
        self.memory.raw[0x9000] ^= 1
        app.recover(self.plan, self.device, journal)
        self.assertEqual(self.memory.boots, [True, False])
        self.assertEqual(bytes(self.memory.raw[resources.START:resources.START + resources.SIZE]),
                         self.resource_plan.image)
        self.assertEqual(self.memory.raw[0x9000], self.after_resources[0x9000] ^ 1)
        self.assertEqual(journal.load()["phase"], "restored")

    def test_failed_candidate_operations_restore_e3d2b_and_keep_e145(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure):
                self.memory = SimpleNamespace(raw=bytearray(self.after_resources), writes=0, boots=[], fail=failure)
                device = MemoryDevice(self.memory, self.plan)
                journal = app.Journal(self.folder / failure, self.plan)
                journal.path.parent.mkdir()
                with self.assertRaises((OSError, ValueError)):
                    app.install(self.plan, device, journal)
                self.assertEqual(self.memory.boots[-1], False)
                self.assertEqual(bytes(self.memory.raw[app.START:app.START + len(self.plan.rollback)]),
                                 self.plan.rollback)
                self.assertEqual(bytes(self.memory.raw[resources.START:resources.START + resources.SIZE]),
                                 self.resource_plan.image)
                self.assertEqual(journal.load()["phase"], "restored")

    def test_nvs_is_part_of_live_recovery_but_not_replaced_by_offline_template(self):
        app.persist(self.folder, self.plan)
        journal = app.Journal(self.folder, self.plan)
        app.install(self.plan, self.device, journal)
        (self.folder / "candidate-app.bin").unlink()
        with patch.object(deployment, "inputs", side_effect=AssertionError("build read during recovery")):
            recovery = app.load_recovery(self.folder)
            self.assertTrue(deployment.check_recovery_plan(recovery))
            app.recover(recovery, MemoryDevice(self.memory, recovery), app.Journal(self.folder, recovery))
        self.assertEqual(bytes(self.memory.raw), self.after_resources)

    def test_recovery_can_be_repeated_after_partial_restore(self):
        journal = app.Journal(self.folder, self.plan)
        app.install(self.plan, self.device, journal)
        self.memory.raw[app.START:app.START + 16] = self.plan.rollback[:16]
        journal.save("needs_recovery")
        app.recover(self.plan, self.device, journal)
        app.recover(self.plan, self.device, journal)
        self.assertEqual(bytes(self.memory.raw), self.after_resources)
        self.assertEqual(journal.load()["phase"], "restored")

    def test_wrong_device_or_partition_table_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "write_or_rollback_range_mismatch"):
            deployment.make_app_plan(self.after_resources, self.candidate, "f" * 64)
        changed = bytearray(self.after_resources)
        changed[0x8000] ^= 1
        with self.assertRaisesRegex(ValueError, "partition_table_mismatch"):
            deployment.make_app_plan(bytes(changed), self.candidate, self.plan.metadata["identity_sha256"])

    def test_original_input_paths_stay_in_the_worktree(self):
        with self.assertRaisesRegex(ValueError, "offline_input_path_invalid"):
            deployment._read(ROOT, Path("../outside.txt"))

    def test_inputs_reject_changed_candidate_and_each_evidence_report(self):
        original = deployment._read
        for selected in (deployment.APP_BUILD, deployment.TEST_REPORT):
            with self.subTest(selected=selected):
                def altered(root, path):
                    raw = original(root, path)
                    return raw + b"\n" if path == selected else raw
                with patch.object(deployment, "_read", side_effect=altered), self.assertRaises(ValueError):
                    deployment.inputs()

    def test_resource_preparation_reports_must_match_loaded_plan(self):
        original = deployment._read
        cases = [(deployment.RESOURCE_PREPARATION, "prepare", "plan_id"),
                 (deployment.RESOURCE_PREPARATION, "check", "plan_id"),
                 (deployment.RESOURCE_READINESS, "budget", "free_bytes"),
                 (deployment.RESOURCE_READINESS, None, "result"),
                 (deployment.RESOURCE_CANDIDATE_REPORT, "candidate", "package_sha256")]
        for path, section, field in cases:
            with self.subTest(path=path, section=section, field=field):
                selected_path = path
                def altered(root, read_path):
                    raw = original(root, read_path)
                    if read_path == selected_path:
                        report = json.loads(raw)
                        if section is None:
                            report[field] = "failed"
                        else:
                            report[section][field] = "0" * 64 if field == "plan_id" else -1 if field == "free_bytes" else "failed"
                        return json.dumps(report).encode()
                    return raw
                with patch.object(deployment, "_read", side_effect=altered):
                    with self.assertRaisesRegex(ValueError, "E1.45_offline_plan_mismatch"):
                        deployment.resources_ready()

    def test_offline_rehearsal_is_not_install_authority(self):
        result = deployment.rehearse()
        self.assertEqual(result["result"], "sequential_installation_and_recovery_ready_offline")
        self.assertEqual(result["mode"], "simulation_only")
        self.assertFalse(result["resources"]["installable"])
        self.assertFalse(result["app"]["installable"])
        self.assertTrue(result["recovery"]["offline_app_plan_requires_fresh_live_snapshot"])
        self.assertFalse(result["evidence"]["hardware_accessed"])
        self.assertFalse(result["evidence"]["device_written"])
        self.assertNotIn("independent_review", result["evidence"])

    def test_both_production_transactions_install_and_roll_back_in_order(self):
        self.memory.raw = bytearray(self.resource_plan.snapshot)
        resource_device = MemoryResourceDevice(self.memory, self.resource_plan)
        resource_folder = self.folder / "resources"
        resource_folder.mkdir()
        resource_journal = resources.Journal(resource_folder, self.resource_plan)
        resources.install(self.resource_plan, resource_device, resource_journal)
        self.assertEqual(bytes(self.memory.raw), self.after_resources)
        app_journal = app.Journal(self.folder, self.plan)
        app.install(self.plan, self.device, app_journal)
        # Resource recovery compares outside bytes, so the App is restored first.
        with self.assertRaisesRegex(ValueError, "outside_resource_bytes_changed"):
            resources.recover(self.resource_plan, resource_device, resource_journal)
        self.assertEqual(resource_device.writes, 1)
        app.recover(self.plan, self.device, app_journal)
        self.memory.raw[0x9000] ^= 1
        resources.recover(self.resource_plan, resource_device, resource_journal)
        expected = bytearray(self.resource_plan.snapshot)
        expected[0x9000] ^= 1
        self.assertEqual(bytes(self.memory.raw), bytes(expected))
        self.assertEqual(resource_device.writes, 2)
        self.assertEqual(resource_journal.load()["phase"], "restored")


if __name__ == "__main__":
    unittest.main()
