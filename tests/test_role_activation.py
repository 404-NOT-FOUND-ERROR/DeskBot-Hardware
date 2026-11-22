"""Persistent role state and actual reviewed expression mappings."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from protocol import role_activation as roles

ROOT = Path(__file__).resolve().parents[1]


class RoleActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.media = roles.load_roles()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="roles-unit-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.store = roles.RoleActivation(self.catalog, self.path, clock=lambda: 1800000000)
        self.a, self.b = [row["id"] for row in self.catalog["roles"]]
        self.catalog_id = self.catalog["catalog_id"]

    def activate(self, role=None, revision=0):
        return self.store.activate(role or self.b, revision, self.catalog_id)

    def test_reviewed_e145_shy_is_not_the_authoring_default(self):
        shy = self.catalog["roles"][0]["expressions"]["embarrassed"]
        self.assertEqual((shy["resource_id"], shy["fps"], shy["loop"], shy["layout_id"],
                          shy["mirror"], shy["implicit_action_id"]),
                         ("shy_candidate.eaf", 25, True, "eye_anim", True, "cat_nuzzle"))

    def test_six_semantics_really_resolve_different_resources_and_layouts(self):
        for semantic, state in zip(roles.SEMANTICS, ("idle", "happy", "sad", "shy", "confused", "drowsy")):
            old = self.store.resolve(semantic, 0, self.catalog_id)
            self.assertEqual(old["expression"]["mode"], "single")
            self.assertTrue(old["expression"]["mirror"])
            self.assertNotIn("command", old)
        self.activate()
        for semantic, state in zip(roles.SEMANTICS, ("idle", "happy", "sad", "shy", "confused", "drowsy")):
            new = self.store.resolve(semantic, 1, self.catalog_id)
            self.assertEqual((new["expression"]["state"], new["expression"]["mirror"]), (state, False))
            self.assertEqual(set(new["expression"]["clips"]), {"entry", "loop", "exit"})
            self.assertNotIn("command", new)

    def test_initial_default_reads_without_writing_and_restores_after_restart(self):
        self.assertEqual(self.store.snapshot()["storage_status"], "initial")
        self.assertFalse(self.path.exists())
        saved = self.activate()
        self.assertEqual(saved["activation"]["revision"], 1)
        self.assertEqual(saved, roles.RoleActivation(self.catalog, self.path).snapshot())
        self.assertEqual(list(self.path.parent.glob(".role-*.tmp")), [])

    def test_repeated_selection_is_idempotent_but_old_revision_still_conflicts(self):
        first = self.activate()
        raw = self.path.read_bytes()
        self.assertEqual(self.activate(revision=1), first)
        self.assertEqual(self.path.read_bytes(), raw)
        with self.assertRaises(roles.RoleConflict):
            self.activate(revision=0)

    def test_unknown_ids_types_catalog_and_revision_are_rejected(self):
        for bad in (None, [], {}, True, "missing", "__proto__"):
            with self.subTest(role=bad), self.assertRaises(ValueError):
                self.store.activate(bad, 0, self.catalog_id)
        for bad in (True, -1, 0.5, "0", roles.MAX_REVISION+1):
            with self.subTest(revision=bad), self.assertRaises(ValueError):
                self.store.resolve("neutral", bad, self.catalog_id)
        with self.assertRaises(roles.RoleConflict):
            self.store.activate(self.b, 0, "f"*64)
        for bad in (None, [], "unknown", "eat"):
            with self.assertRaises(ValueError):
                self.store.resolve(bad, 0, self.catalog_id)
        self.assertFalse(self.path.exists())

    def test_no_shell_identity_is_invented_and_source_is_explicit(self):
        with self.assertRaises(ValueError):
            self.store.activate(self.b, 0, self.catalog_id, source="shell")
        saved = self.activate()["activation"]
        self.assertEqual(saved["source"], "manual")
        self.assertFalse({"shell_id", "magnet_profile_id"} & set(saved))

    def test_old_expression_revision_is_rejected_after_selection_changes(self):
        self.activate()
        with self.assertRaises(roles.RoleConflict):
            self.store.resolve("happy", 0, self.catalog_id)

    def test_atomic_write_failure_keeps_original_memory_and_disk(self):
        self.activate()
        before, raw = self.store.snapshot(), self.path.read_bytes()
        with patch.object(roles.os, "replace", side_effect=OSError("simulated full disk")):
            with self.assertRaises(roles.RoleStorageError):
                self.activate(self.a, 1)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.path.parent.glob(".role-*.tmp")), [])

    def test_lock_covers_write_and_readers_see_only_committed_state(self):
        entered, release = threading.Event(), threading.Event()
        def writer(path, value):
            entered.set()
            if not release.wait(2):
                raise OSError("test writer was not released")
            roles.atomic_save(path, value)
        self.store.writer = writer
        with ThreadPoolExecutor(2) as executor:
            writing = executor.submit(self.activate)
            self.assertTrue(entered.wait(2))
            reading = executor.submit(self.store.snapshot)
            self.assertFalse(reading.done())
            release.set()
            self.assertEqual(writing.result(), reading.result())

    def test_competing_saves_have_one_winner(self):
        def save(role):
            try:
                return self.activate(role)["activation"]["role_id"]
            except roles.RoleConflict:
                return "conflict"
        with ThreadPoolExecutor(2) as executor:
            result = list(executor.map(save, (self.a, self.b)))
        self.assertEqual(result.count("conflict"), 1)
        self.assertEqual(self.store.snapshot()["activation"]["revision"], 1)

    def test_invalid_saved_file_is_preserved_and_explicitly_recovered(self):
        raw = b'{"broken":"original bytes"}'
        self.path.write_bytes(raw)
        self.store = roles.RoleActivation(self.catalog, self.path, clock=lambda: 1800000000)
        self.assertIsNone(self.store.snapshot()["activation"])
        with self.assertRaises(roles.RoleStorageError):
            self.activate()
        self.assertEqual(self.path.read_bytes(), raw)
        recovered = self.store.recover(self.b, 0, self.catalog_id)
        backups = list(self.path.parent.glob("state.previous-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertEqual(recovered, roles.RoleActivation(self.catalog, self.path).snapshot())
        with self.assertRaises(roles.RoleConflict):
            self.store.recover(self.a, 0, self.catalog_id)

    def test_catalog_change_duplicate_keys_and_wrong_types_preserve_saved_bytes(self):
        self.activate()
        saved = roles.read_json(self.path)
        for bad in ({**saved,"catalog_id":"e"*64}, {**saved,"revision":True},
                    {**saved,"source":"shell"}, {**saved,"revision":0}):
            self.path.write_bytes(roles.canonical(bad))
            raw = self.path.read_bytes()
            restored = roles.RoleActivation(self.catalog, self.path)
            self.assertEqual(restored.snapshot()["storage_status"], "unavailable")
            self.assertEqual(self.path.read_bytes(), raw)
        self.path.write_bytes(b'{"schema":"one","schema":"two"}')
        self.assertIsNone(roles.RoleActivation(self.catalog, self.path).snapshot()["activation"])

    def test_one_os_owner_and_release(self):
        first = roles.StateOwner(self.path)
        try:
            with self.assertRaises(roles.RoleStorageError):
                roles.StateOwner(self.path)
        finally:
            first.close()
        second = roles.StateOwner(self.path)
        second.close()

    def test_close_waits_for_commit_then_rejects_late_writes(self):
        entered, release = threading.Event(), threading.Event()
        def writer(path, value):
            entered.set()
            if not release.wait(2):
                raise OSError("test writer was not released")
            roles.atomic_save(path, value)
        self.store.writer=writer
        with ThreadPoolExecutor(2) as executor:
            saving=executor.submit(self.activate)
            self.assertTrue(entered.wait(2))
            closing=executor.submit(self.store.close)
            self.assertFalse(closing.done())
            release.set()
            saved=saving.result()
            closing.result()
        with self.assertRaises(roles.RoleStorageError):
            self.store.activate(self.a,1,self.catalog_id)
        self.assertEqual(roles.RoleActivation(self.catalog,self.path).snapshot(),saved)

    def test_catalog_and_results_are_independent_snapshots(self):
        snapshot = self.store.snapshot()
        snapshot["catalog"]["roles"][0]["name"] = "changed"
        self.assertEqual(self.store.snapshot()["catalog"]["roles"][0]["name"], "原有角色")
        result = self.store.resolve("happy", 0, self.catalog_id)
        result["expression"]["mirror"] = False
        self.assertTrue(self.store.resolve("happy", 0, self.catalog_id)["expression"]["mirror"])
        changed = deepcopy(self.catalog)
        changed["catalog_id"] = "e"*64
        self.activate()
        self.assertIsNone(roles.RoleActivation(changed, self.path).snapshot()["activation"])


if __name__ == "__main__":
    unittest.main()
