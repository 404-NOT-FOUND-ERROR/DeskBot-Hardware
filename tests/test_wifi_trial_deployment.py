"""Exercise the local pinned Wi-Fi trial with the actual App transport."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import airboat_role_deployment as paired
from protocol import studio_app_deployment as app
from protocol.wifi_trial_deployment import ReviewedTrial, installed_pair
from protocol.storage_upper_experiment_esptool import Runner, _NORMAL_BOOT_MARKERS, parse_identity
from tests.test_studio_role_release_e3f9d import FakeRunner
from tests.test_studio_resource_adapter import FLASH_LOG, SECURITY_LOG

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / 'tmp/e3c-live-20261120-wifi-touch-off1'


class WifiDeploymentTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(Runner, 'run', side_effect=AssertionError('real process in offline test'))
        guard.start(); self.addCleanup(guard.stop)
        self.pins = json.loads((SAVED / 'wifi-pins.json').read_bytes())
        self.pins['identity_sha256'] = parse_identity(FLASH_LOG, SECURITY_LOG).digest
        self.reviewed = ReviewedTrial(ROOT, self.pins)
        original = app.load(SAVED)
        self.plan = self.reviewed.prepare_plan(original.snapshot, self.reviewed.IDENTITY_SHA, original.image)
        folder = tempfile.TemporaryDirectory(prefix='wifi-deploy-test-', dir=ROOT / 'tmp')
        self.addCleanup(folder.cleanup); self.folder = Path(folder.name)
        app.persist(self.folder, self.plan)
        spec = importlib.util.spec_from_file_location('wifi_test_deployment', ROOT / 'scripts/deploy-wifi-trial.py')
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.parent, self.device_type = self.module.configure(self.reviewed)
        plan, pins = self.plan, self.pins
        describe = self.parent.parent.entry.base.entry.parent.image_identity
        class NetworkRunner(FakeRunner):
            def boot_log(self):
                self.boots += 1
                candidate = app.sha(bytes(self.data[app.START:app.START + pins['candidate_bytes']])) == pins['candidate_sha256']
                image = bytes(self.data[app.START:app.START + (pins['candidate_bytes'] if candidate else pins['original_bytes'])])
                frames = []
                for index in range(4):
                    hello = copy.deepcopy(self.hello)
                    hello.update(sequence=index, uptime_ms=1105 + index * 10000, boot_id='boot-%032x' % self.boots)
                    hello['device_id'] = 'device-' + hashlib.sha256(b'deskbot-link-v1\0' + bytes.fromhex(self.mac.replace(':', ''))).digest()[:16].hex()
                    hello['firmware'].update(describe(image))
                    frames.append('I (%d) DeskBotLink: %s' % (hello['uptime_ms'], json.dumps(hello)))
                extra = ['DeskBotTouchWifi: ready'] if candidate else []
                if self.boots in self.fail_boots: extra.append('Guru Meditation')
                return '\n'.join((*_NORMAL_BOOT_MARKERS, 'USB input ready: /dev/secondary', *frames, *extra))
        self.runner = NetworkRunner(plan.snapshot)
        self.device = self.device_type(self.folder, self.reviewed.IDENTITY_SHA, self.plan)
        self.device.runner = self.runner

    def test_install_changes_only_app_and_verifies_worker(self):
        result = app.install(self.plan, self.device, app.Journal(self.folder, self.plan))
        self.assertEqual(result['result'], 'candidate_installed')
        self.assertEqual(self.runner.writes, [(app.START, self.plan.candidate)])
        end = app.START + len(self.plan.candidate)
        self.assertEqual(bytes(self.runner.data[:app.START]), self.plan.snapshot[:app.START])
        self.assertEqual(bytes(self.runner.data[end:]), self.plan.snapshot[end:])

    def test_failed_boot_restores_same_run_app(self):
        self.runner.fail_boots.add(1)
        with self.assertRaises(ValueError): app.install(self.plan, self.device, app.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.plan.snapshot)
        self.assertEqual(app.Journal(self.folder, self.plan).load()['phase'], 'restored')

    def test_combined_worker_requires_its_own_boot_marker(self):
        for edition in ('E3f-9d.6-body-wifi', 'E3f-9d.7-network-settings'):
            self.pins['edition'] = edition
            reviewed = ReviewedTrial(ROOT, self.pins)
            log = self.runner.boot_log()
            with self.subTest(edition=edition), patch('protocol.wifi_trial_deployment.display.check_display_boot') as check_display:
                with self.assertRaisesRegex(ValueError, 'wifi_trial_worker_not_started'):
                    reviewed.check_display_boot(log + '\nDeskBotExprWifi: ready')
                reviewed.check_display_boot(log + '\nDeskBotBodyWifi: ready')
                self.assertEqual(check_display.call_count, 2)

    def test_combined_build_requires_both_features_in_one_worker(self):
        report = json.loads((ROOT / self.pins['profile'] / 'build/report.json').read_bytes())
        report.update(edition='E3f-9d.6-body-wifi', network_workers=1, worker_stack_bytes=6144,
                      features=['wifi_expression', 'wifi_head_touch'])
        pins = {**self.pins, 'edition': report['edition'], 'profile': '.'}
        build = self.folder / 'build'; build.mkdir()
        for count, valid in ((1, True), (2, False)):
            report['network_workers'] = count
            raw = json.dumps(report).encode()
            (build / 'report.json').write_bytes(raw)
            pins['build_report_sha256'] = app.sha(raw)
            reviewed = ReviewedTrial(self.folder, pins)
            with patch.object(reviewed, 'reviewed_candidate', return_value=b'candidate'):
                if valid:
                    self.assertEqual(reviewed.check_build_evidence()['image'], b'candidate')
                else:
                    with self.assertRaisesRegex(ValueError, 'combined_wifi_single_worker_required'):
                        reviewed.check_build_evidence()

    def test_recovery_needs_no_candidate_or_build(self):
        (self.folder / 'candidate-app.bin').unlink()
        recovery = self.reviewed.check_plan(app.load_recovery(self.folder), candidate=False)
        device = self.device_type(self.folder, self.reviewed.IDENTITY_SHA, recovery); device.runner = self.runner
        journal = app.Journal(self.folder, recovery); journal.save('restore_required')
        self.runner.data[app.START:app.START + 32] = b'\0' * 32
        with patch.object(self.reviewed, 'check_build_evidence', side_effect=AssertionError('build needed')):
            app.recover(recovery, device, journal)
        self.assertEqual(bytes(self.runner.data), self.plan.snapshot)

    def test_network_settings_build_requires_persistence_and_session_sources(self):
        report = json.loads((ROOT / self.pins['profile'] / 'build/report.json').read_bytes())
        report.update(edition='E3f-9d.7-network-settings', network_workers=1, worker_stack_bytes=6144,
            features=['wifi_expression', 'wifi_head_touch'], network_settings=True, nvs_namespace='deskbot_net',
            source_sha256={'main/boards/esp_vocat/' + name: 'a' * 64 for name in
                ('body_network_config.h', 'body_network_config.inc', 'studio_usb_session.h', 'studio_usb_diagnostic.cc')})
        pins = {**self.pins, 'edition': report['edition'], 'profile': '.'}
        build = self.folder / 'build'; build.mkdir()
        for change in ({}, {'network_settings': False}, {'nvs_namespace': 'wrong'}, {'source_sha256': {}}):
            changed = {**report, **change}
            raw = json.dumps(changed).encode()
            (build / 'report.json').write_bytes(raw)
            pins['build_report_sha256'] = app.sha(raw)
            reviewed = ReviewedTrial(self.folder, pins)
            with self.subTest(change=change), patch.object(reviewed, 'reviewed_candidate', return_value=b'candidate'):
                if not change:
                    self.assertEqual(reviewed.check_build_evidence()['image'], b'candidate')
                else:
                    with self.assertRaisesRegex(ValueError, 'persisted_network_settings_build_required'):
                        reviewed.check_build_evidence()

    def test_static_change_is_rejected_and_nvs_can_refresh(self):
        raw = bytearray(self.plan.snapshot); raw[0x9000] ^= 1
        self.reviewed.check_current(bytes(raw))
        raw[0x1000000] ^= 1
        with self.assertRaises(ValueError): self.reviewed.check_current(bytes(raw))

    def test_paired_reference_requires_installed_assets_and_unpadded_app(self):
        snapshot, image = self.plan.snapshot, self.plan.image
        resources = b'\xa5' * paired.SIZE
        paired_pins = dict(self.pins, identity_sha256=paired.IDENTITY, original_app_elf_sha256='c' * 64)
        plan = paired.prepare(snapshot, image, resources, paired_pins, {
            'image_sha256': app.sha(resources),
            'download_sha256': {'deskbot-roles.json': 'b' * 64},
        })
        folder = self.folder / 'paired'; folder.mkdir()
        app_target = plan['targets'][0]
        payload = image + snapshot[app.START + len(image):app.START + app_target['bytes']]
        for name, raw in {
            'plan.json': app.canonical(plan),
            'snapshot-32m.bin': snapshot,
            'candidate-app.bin': payload,
            'candidate-assets.bin': resources,
            'pins.json': app.canonical(paired_pins),
        }.items():
            (folder / name).write_bytes(raw)
        state = dict(phase='installed', readback_equal=True, outside_targets_equal=True,
                     nvs_equal=True, boot_verified=True, plan_sha256=app.sha(app.canonical(plan)))
        (folder / 'state.json').write_bytes(app.canonical(state))
        baseline, original, identity, graph = installed_pair(folder)
        self.assertEqual(original, image)
        self.assertEqual(identity, paired.IDENTITY)
        self.assertEqual(graph, 'b' * 64)
        self.assertEqual(baseline[paired.START:paired.START + paired.SIZE], resources)
        (folder / 'candidate-assets.bin').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'installation_payload_changed'):
            installed_pair(folder)

    def test_preflight_failure_can_restart_baseline_without_plan(self):
        device = self.device_type(self.folder, self.reviewed.IDENTITY_SHA)
        device.runner = self.runner; device.identity()
        with patch.object(self.reviewed, 'load_reference', return_value={'snapshot': self.plan.snapshot}):
            self.assertTrue(device.boot(False)['firmware_matches_candidate'])

if __name__ == '__main__': unittest.main()
