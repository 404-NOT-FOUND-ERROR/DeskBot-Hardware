"""Adopt an installed Wi-Fi role while retaining USB routing and local data."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('airboat_host', ROOT/'scripts/activate-airboat-host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='airboat-host-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root/'tmp/airboat-role-live-test'
        self.folder.mkdir(parents=True)
        self.local = self.root/'local'
        self.runtime = self.local/'DeskBot/companion/runtime.json'
        self.role = self.root/'role.json'
        self.write(self.role, {'previous': 'role'})
        self.db = self.root/'life.sqlite'
        self.db.write_bytes(b'life archive unchanged')
        self.config = {'database': str(self.db), 'touch_device_id': 'same-head',
            'touch_firmware_sha256': 'usb-accepted-firmware'}
        self.original = {}
        for channel in ('touch', 'expression'):
            actual = self.local/f'body/{channel}/receiver.json'
            prepared = self.root/f'tmp/airboat-role-20261121/{channel}/receiver.json'
            value = {'device_id': 'same-head', 'firmware_sha256': 'old-wifi-firmware',
                'token_file': 'token', 'cert_file': 'cert', 'key_file': 'key'}
            if channel == 'expression':
                value.update(graph_sha256='old-graph', role_id='old-role', role_revision=3)
            for filename, content in [('token', b'separate-'+channel.encode()), ('cert', b'certificate'), ('key', b'key')]:
                for parent in (actual.parent, prepared.parent):
                    parent.mkdir(parents=True, exist_ok=True)
                    (parent/filename).write_bytes(content)
            self.write(actual, value)
            self.write(prepared, value)
            self.config[channel+'_wifi_config'] = str(actual)
            self.original[channel] = value
        self.write(self.runtime, self.config)
        self.runtime_bytes = self.runtime.read_bytes()
        self.write(self.folder/'state.json', {'phase': 'installed', 'readback_equal': True, 'boot_verified': True})
        self.write(self.folder/'plan.json', {'app_elf_sha256': 'new-wifi-firmware', 'graph_sha256': 'new-graph'})
        self.write(self.root/'tmp/airboat-role-20261121/expression/build/report.json', {'app_elf_sha256': 'new-wifi-firmware'})
        self.write(self.root/'artifacts/airboat-role-20261121/package/report.json',
            {'download_sha256': {'deskbot-roles.json': 'new-graph'}})
        self.roles = Mock(catalog={'catalog_id': 'catalog'})
        self.roles.snapshot.return_value = {'storage_status': 'unavailable'}
        for scope in [patch.object(host, 'ROOT', self.root), patch.object(host, 'STATE_PATH', self.role),
                      patch.object(host, 'load_roles', return_value=({}, None)),
                      patch.object(host, 'RoleActivation', return_value=self.roles),
                      patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})]:
            scope.start()
            self.addCleanup(scope.stop)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value)+'\n', encoding='utf-8')

    def test_wifi_adoption_keeps_usb_binding_and_life_archive(self):
        receipt = host.run(self.folder)
        self.assertEqual(self.runtime.read_bytes(), self.runtime_bytes)
        self.assertEqual(self.db.read_bytes(), b'life archive unchanged')
        self.assertTrue(receipt['usb_receiver_binding_preserved'])
        for channel in ('touch', 'expression'):
            row = json.loads(Path(self.config[channel+'_wifi_config']).read_bytes())
            self.assertEqual(row['firmware_sha256'], 'new-wifi-firmware')
            self.assertNotEqual(row['firmware_sha256'], self.config['touch_firmware_sha256'])
        self.roles.recover.assert_called_once_with('airboat-face-v1', 0, 'catalog')

    def test_saved_configuration_restores_both_receivers(self):
        host.run(self.folder)
        host.run(self.folder, restore=True)
        self.assertEqual(json.loads(self.runtime.read_bytes()), self.config)
        for channel in ('touch', 'expression'):
            self.assertEqual(json.loads(Path(self.config[channel+'_wifi_config']).read_bytes()), self.original[channel])

    def test_changed_credentials_stop_before_receiver_updates(self):
        prepared = self.root/'tmp/airboat-role-20261121/expression/token'
        prepared.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'receiver_credentials_changed'):
            host.run(self.folder)
        for channel in ('touch', 'expression'):
            self.assertEqual(json.loads(Path(self.config[channel+'_wifi_config']).read_bytes()), self.original[channel])


if __name__ == '__main__':
    unittest.main()
