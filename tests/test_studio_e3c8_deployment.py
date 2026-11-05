"""USB-drain rollout pins the installed candidate and retains independent recovery."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c8_deployment as reviewed


class DrainDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.original = b'\xe9' + b'o' * 8999
        self.image = b'\xe9' + b'n' * 9999
        raw = bytearray(b'\xff' * tx.FLASH)
        raw[tx.START:tx.START+len(self.original)] = self.original
        self.plan = tx.prepare(bytes(raw), self.image, 'a'*64, len(self.original), edition='E3c')

    def test_candidate_rejects_changed_bytes_before_parsing(self):
        with patch.object(reviewed, 'image_facts') as parser, self.assertRaises(ValueError):
            reviewed.reviewed_candidate(self.image)
        parser.assert_not_called()

    def test_candidate_capacity_is_checked_independently(self):
        with patch.multiple(reviewed, APP_SIZE=len(self.image), APP_SHA=tx.sha(self.image)):
            with patch.object(reviewed, 'image_facts', return_value={'configured_flash_bytes': tx.FLASH}):
                self.assertEqual(reviewed.reviewed_candidate(self.image), self.image)
            with patch.object(reviewed, 'image_facts', return_value={'configured_flash_bytes': tx.FLASH//2}):
                with self.assertRaises(ValueError):
                    reviewed.reviewed_candidate(self.image)

    def test_recovery_uses_original_without_candidate(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            tx.persist(folder, self.plan)
            (folder / 'candidate-app.bin').unlink()
            saved = tx.load_recovery(folder)
            with patch.multiple(reviewed, ORIGINAL_SIZE=len(self.original), ORIGINAL_SHA=tx.sha(self.original)):
                reviewed.check_plan(saved, candidate=False)
            self.assertEqual(saved.rollback, self.plan.rollback)

    def reference(self, root, *, valid=True, change_hash=False):
        folder = root / 'tmp' / reviewed.PREVIOUS_RUN
        folder.mkdir(parents=True)
        rows = {
            'install-result.json': {'result': 'candidate_installed', 'readback': {
                'app_byte_equal': valid, 'static_outside_app_equal': True, 'nvs_equal': True}},
            'restart-result.json': {'result': 'restart_verified', 'boot_id_changed': True, 'device_id_stable': True},
        }
        hashes = {}
        for name, value in rows.items():
            raw = tx.canonical(value)
            (folder/name).write_bytes(raw)
            hashes[name] = tx.sha(raw) if not change_hash else '0'*64
        return hashes

    def load_reference(self, root, hashes, *, phase='installed'):
        with (patch.object(tx, 'load', return_value=self.plan),
              patch.object(reviewed.previous, 'check_plan', side_effect=lambda plan: plan),
              patch.object(tx.Journal, 'load', return_value={'phase': phase}),
              patch.multiple(reviewed, EVIDENCE=hashes, PREVIOUS_PLAN=self.plan.id)):
            return reviewed.load_reference(root)

    def test_reference_reconstructs_installed_app_and_original_tail(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            reference = self.load_reference(root, self.reference(root))
            end = tx.START + len(self.plan.rollback)
            expected = self.plan.snapshot[:tx.START] + self.plan.candidate + self.plan.snapshot[end:]
            self.assertEqual(reference['snapshot'], expected)
            self.assertEqual(reference['identity_sha256'], self.plan.metadata['identity_sha256'])

    def test_reference_requires_installed_journal(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                self.load_reference(root, self.reference(root), phase='restored')

    def test_reference_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                self.load_reference(root, self.reference(root, change_hash=True))

    def test_reference_requires_successful_outside_app_checks(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                self.load_reference(root, self.reference(root, valid=False))


if __name__ == '__main__':
    unittest.main()
