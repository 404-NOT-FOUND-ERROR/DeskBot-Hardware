"""E3f-3: real HTTP, resource bytes, native catalog and expression dispatch."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as shy
from protocol import studio_resource_installation as resources
from protocol import studio_resource_package as packages
from tests import studio_role_flow_fixture as f

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_READY = all(path.is_file() for path in (
    ROOT / shy.APP_BUILD, ROOT / shy.BUILD_REPORT,
    ROOT / shy.RESOURCE_RUN / 'snapshot-32m.bin', packages.BASE_IMAGE, packages.CANDIDATE))
EVIDENCE = {}


@unittest.skipUnless(PRIVATE_READY, 'retained private Flash/build inputs required')
class RoleFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate, cls.frozen_sources = f.verified_builds()
        cls.addClassCleanup(f.close_builds)
        cls.snapshot = shy.resources_ready().snapshot
        cls.catalog = packages.load_catalog()
        cls.draft = deepcopy(cls.catalog['initial'])
        cls.draft['revision'] += 1
        cls.draft['roles'][0]['bindings']['embarrassed'] = 'shy_candidate.eaf'
        # The downloaded output, rather than an old candidate file, is the
        # only resource input used by every installation below.
        with tempfile.TemporaryDirectory(prefix='e3f3-package-', dir=ROOT / 'tmp') as temp:
            folder = Path(temp)
            original = f.Flash(cls.snapshot)
            pages, _ = f.installed_pages(original, folder / 'original')
            bridge = f.Bridge(original, folder, pages)
            http = f.HTTP(bridge, folder / 'packages')
            try:
                status, reply = http.post('/api/package', cls.draft)
                if status != 200:
                    raise ValueError('flow_http_package_failed: ' + str(reply))
                cls.image = http.download(reply['downloads']['expression-assets-8m.bin'])
                cls.package_report = reply['report']
            finally:
                http.close()
        if packages.sha(cls.image) != resources.CANDIDATE_SHA:
            raise ValueError('flow_downloaded_E145_changed')
        EVIDENCE.update(download=dict(path=reply['downloads']['expression-assets-8m.bin'],
            bytes=len(cls.image), sha256=packages.sha(cls.image)),
            frozen_source_sha256=cls.frozen_sources)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='e3f3-flow-', dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.flash = f.Flash(self.snapshot)
        self.plan = resources.prepare(self.snapshot, self.image, baseline.IDENTITY_SHA,
                                      mode='offline', baseline='E3d-2b')
        resource_folder = self.folder / 'resource-transaction'
        resource_folder.mkdir()
        resources.persist(resource_folder, self.plan)
        self.resource_journal = resources.Journal(resource_folder, self.plan)
        self.resource_device = f.ResourceDevice(self.flash, self.plan)
        self.counter = 0

    def install_resources(self):
        result = resources.install(self.plan, self.resource_device, self.resource_journal)
        self.assertEqual(result['result'], 'candidate_resources_installed')

    def install_app(self):
        self.app_plan = shy.make_app_plan(bytes(self.flash.raw), self.candidate, baseline.IDENTITY_SHA)
        folder = self.folder / 'app-transaction'
        folder.mkdir()
        app.persist(folder, self.app_plan)
        self.app_journal = app.Journal(folder, self.app_plan)
        self.app_device = f.AppDevice(self.flash, self.app_plan)
        result = app.install(self.app_plan, self.app_device, self.app_journal)
        self.assertEqual(result['result'], 'candidate_installed')

    def connect(self, *, corrupt=False, omit_last=False):
        self.counter += 1
        folder = self.folder / ('connection-' + str(self.counter))
        folder.mkdir()
        pages, manifest = f.installed_pages(self.flash, folder / 'mounted')
        if corrupt:
            pages[0]['catalog_id'] = 'f' * 64
        bridge = f.Bridge(self.flash, folder, pages)
        http = f.HTTP(bridge, folder / 'http-packages')
        self.addCleanup(http.close)
        bridge.open()
        state = bridge.read_catalog(omit_last=omit_last)
        return bridge, http, state, manifest

    def context(self, bridge):
        state = bridge.control()
        return {k: state[k] for k in ('connection_id', 'target_id', 'boot_id', 'session_id')}

    def resolve(self, bridge, http, *, context=None, semantic='embarrassed', request_id='flow-expression'):
        status, result = http.post('/api/role-expression/resolve', dict(draft=self.draft,
            semantic=semantic, source='device', context=context or self.context(bridge), request_id=request_id))
        self.assertEqual(status, 200, result)
        return result

    def submit(self, http, command):
        status, result = http.post('/api/studio/command', command)
        self.assertEqual(status, 200, result)
        return result

    def test_complete_install_directory_request_and_ordered_rollback(self):
        old, old_http, _, _ = self.connect()
        self.assertEqual(self.resolve(old, old_http)['status'], 'needs_publish')
        self.install_resources()
        # App switching invalidates an existing role analysis and directory.
        self.assertIsNone(old.control()['catalog']['manifest'])
        self.install_app()
        bridge, http, state, manifest = self.connect()
        self.assertNotEqual(bridge.boot, old.boot)
        self.assertEqual(state['catalog']['status'], 'complete')
        self.assertTrue(state['can_submit'])
        self.assertEqual(state['rejected_frames'], 0)
        self.assertEqual(state['catalog']['manifest'], manifest)
        self.assertEqual(len(bridge.pages), 18)
        status, overview = http.post('/api/role-expression/overview', dict(draft=self.draft,
            source='device', context=self.context(bridge), request_id='flow-overview'))
        self.assertEqual(status, 200, overview)
        self.assertEqual((overview['total'], overview['resource_matches'], overview['counts']['ready']), (29, 29, 2))
        result = self.resolve(bridge, http)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['command']['operation'], 'embarrassed')
        self.submit(http, result['command'])
        bridge.dispatch()
        receipts = bridge.control()['receipts']
        self.assertEqual([r['status'] for r in receipts], ['accepted', 'dispatched'])
        self.assertEqual((bridge.stats[0], bridge.stats[3]), (1, 1))
        self.assertEqual(bridge.control()['rejected_frames'], 0)
        # Preserve current settings written after installation.
        self.flash.raw[0x9000] ^= 1
        app_result = app.recover(self.app_plan, self.app_device, self.app_journal)
        self.assertEqual(app_result['result'], 'original_restored')
        self.assertIsNone(bridge.control()['catalog']['manifest'])
        resource_result = resources.recover(self.plan, self.resource_device, self.resource_journal)
        self.assertEqual(resource_result['result'], 'original_resources_restored')
        expected = bytearray(self.snapshot)
        expected[0x9000] ^= 1
        self.assertEqual(self.flash.raw, expected)
        EVIDENCE['flow'] = dict(overview_counts=overview['counts'], catalog_entries=len(manifest['entries']),
            catalog_id=manifest['catalog_id'], package_sha256=manifest['package_sha256'],
            directory_pages=bridge.pages, receipts=receipts,
            native_display_calls=bridge.stats[0], native_shy_calls=bridge.stats[3],
            original_app_sha256=baseline.APP_SHA, candidate_app_sha256=shy.APP_SHA,
            writes=[dict(kind=kind, offset=start, bytes=size) for kind, start, size in self.flash.writes],
            nvs_preserved=True, rollback_matches_original_except_current_nvs=True,
            player_boundary='display stub: semantic dispatch verified; physical rendering pending')

    def test_download_replaces_only_shy_preserving_layouts_and_actions(self):
        _, before = f.installed_pages(self.flash, self.folder / 'before')
        self.install_resources()
        _, after = f.installed_pages(self.flash, self.folder / 'after')
        rows_before = {(r['kind'], r['id']): r for r in before['entries']}
        rows_after = {(r['kind'], r['id']): r for r in after['entries']}
        changed = [key for key in rows_before if rows_before[key] != rows_after[key]]
        self.assertEqual(changed, [('expression', 'embarrassed')])
        row = rows_after[changed[0]]
        asset = self.catalog['assets']['shy_candidate.eaf']
        self.assertEqual((row['resource_id'], row['resource_sha256'], row['bytes'], row['fps'], row['loop']),
            ('shy_candidate.eaf', asset['deployed_sha256'], asset['bytes'], 25, True))
        EVIDENCE['changed_entries'] = [dict(kind=k, id=v) for k, v in changed]

    def test_resources_alone_still_require_shy_interface(self):
        self.install_resources()
        bridge, http, state, _ = self.connect()
        self.assertEqual(state['catalog']['status'], 'complete')
        result = self.resolve(bridge, http)
        self.assertEqual(result['reason'], 'expression_interface_pending')
        self.assertIsNone(result['command'])
        self.assertEqual(bridge.stats[0], 0)

    def test_resource_partial_write_restores_original_bytes(self):
        self.flash.fail_resource = True
        with self.assertRaisesRegex(OSError, 'injected_resource_write'):
            self.install_resources()
        self.assertEqual(self.flash.raw, self.snapshot)
        self.assertEqual(self.resource_journal.load()['phase'], 'restored')
        bridge, http, _, _ = self.connect()
        self.assertEqual(self.resolve(bridge, http)['status'], 'needs_publish')

    def test_resource_reboot_cancels_old_neutral_command_before_dispatch(self):
        bridge, http, _, _ = self.connect()
        self.submit(http, self.resolve(bridge, http, semantic='neutral')['command'])
        self.assertIsNotNone(bridge.link.outgoing)
        self.install_resources()
        # dispatch itself observes the boot, without an earlier status read.
        self.assertIsNone(bridge.dispatch())
        self.assertEqual(bridge.stats[0], 0)
        self.assertIsNone(bridge.control()['catalog']['manifest'])
        with self.assertRaisesRegex(ValueError, 'reconnect_required'):
            bridge._native([('U', bridge.now+1)])

    def test_app_partial_write_retains_new_resources_and_original_app(self):
        self.install_resources()
        expected = bytes(self.flash.raw)
        self.flash.fail_app = True
        with self.assertRaisesRegex(OSError, 'injected_app_write'):
            self.install_app()
        self.assertEqual(self.flash.raw, expected)
        self.assertEqual(self.app_journal.load()['phase'], 'restored')
        bridge, http, _, _ = self.connect()
        self.assertEqual(self.resolve(bridge, http)['reason'], 'expression_interface_pending')

    def test_wrong_rollback_order_rejects_before_resource_write(self):
        self.install_resources()
        self.install_app()
        writes = len(self.flash.writes)
        with self.assertRaisesRegex(ValueError, 'outside_resource_bytes_changed'):
            resources.recover(self.plan, self.resource_device, self.resource_journal)
        self.assertEqual(len(self.flash.writes), writes)
        app.recover(self.app_plan, self.app_device, self.app_journal)
        resources.recover(self.plan, self.resource_device, self.resource_journal)
        self.assertEqual(self.flash.raw, self.snapshot)

    def test_duplicate_http_command_dispatches_once(self):
        self.install_resources()
        self.install_app()
        bridge, http, _, _ = self.connect()
        command = self.resolve(bridge, http)['command']
        self.submit(http, command)
        self.submit(http, command)
        bridge.dispatch()
        self.submit(http, command)
        bridge.dispatch()
        self.assertEqual((bridge.stats[0], bridge.stats[3]), (1, 1))
        requests = [r for r in bridge.wires if r.get('command', {}).get('semantic_id') == 'embarrassed']
        self.assertEqual(len(requests), 1)
        EVIDENCE['duplicate_native_dispatches'] = bridge.stats[3]

    def interrupted(self, change):
        self.install_resources()
        self.install_app()
        bridge, http, _, _ = self.connect()
        self.submit(http, self.resolve(bridge, http)['command'])
        bridge.dispatch(change)
        receipts = bridge.control()['receipts']
        self.assertEqual([r['status'] for r in receipts], ['accepted', 'interrupted'])
        self.assertEqual(bridge.stats[0], 0)
        EVIDENCE[change + '_interruption'] = receipts[-1]

    def test_menu_transition_between_accept_and_owner_interrupts(self):
        self.interrupted('menu')

    def test_voice_transition_between_accept_and_owner_interrupts(self):
        self.interrupted('voice')

    def test_reconnect_discards_old_context_catalog_and_queued_command(self):
        self.install_resources()
        self.install_app()
        bridge, http, _, _ = self.connect()
        context = self.context(bridge)
        command = self.resolve(bridge, http)['command']
        self.submit(http, command)
        bridge.disconnect()
        self.assertIsNone(bridge.control()['catalog']['manifest'])
        bridge.open()
        self.assertNotEqual(self.context(bridge)['session_id'], context['session_id'])
        self.assertNotEqual(self.context(bridge)['connection_id'], context['connection_id'])
        self.assertIsNone(bridge.control()['catalog']['manifest'])
        self.assertIsNone(self.resolve(bridge, http, context=context)['command'])
        self.submit(http, command)
        self.assertIsNone(bridge.link.outgoing)
        self.assertFalse(any(r.get('command') for r in bridge.wires))
        bridge.read_catalog()
        self.assertEqual(self.resolve(bridge, http, request_id='new-analysis')['status'], 'ready')

    def test_missing_last_directory_page_cannot_resolve(self):
        self.install_resources()
        self.install_app()
        bridge, http, state, _ = self.connect(omit_last=True)
        self.assertEqual(state['catalog']['status'], 'failed')
        self.assertIsNone(self.resolve(bridge, http)['command'])
        self.assertEqual(bridge.stats[0], 0)

    def test_disconnect_after_accepted_prevents_native_owner_dispatch(self):
        self.install_resources()
        self.install_app()
        bridge, http, _, _ = self.connect()
        self.submit(http, self.resolve(bridge, http)['command'])
        bridge.now += 400
        bridge._native([('D', bridge.now, bridge.link.tick(bridge.now))])
        accepted = bridge.control()['receipts'][-1]
        self.assertEqual(accepted['status'], 'accepted')
        bridge.disconnect()
        bridge._native([('U', bridge.now+10), ('Z', bridge.now+20)])
        self.assertEqual((bridge.stats[0], bridge.stats[3]), (0, 0))
        bridge.open()
        rejected = bridge.control()['rejected_frames']
        bridge.link.accept(accepted, bridge.now)
        self.assertEqual(bridge.control()['rejected_frames'], rejected+1)
        self.assertIsNone(bridge.control()['catalog']['manifest'])
        EVIDENCE['accepted_disconnect_native_dispatches'] = bridge.stats[0]

    def test_tampered_native_directory_cannot_resolve(self):
        self.install_resources()
        self.install_app()
        bridge, http, state, _ = self.connect(corrupt=True)
        self.assertEqual(state['catalog']['status'], 'failed')
        self.assertGreater(state['rejected_frames'], 0)
        self.assertIsNone(self.resolve(bridge, http)['command'])
        self.assertEqual(bridge.stats[0], 0)


if __name__ == '__main__':
    unittest.main()
