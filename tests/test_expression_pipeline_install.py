"""Failure-path checks use fake USB/transaction adapters; they never access a device."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import ExitStack

from protocol.expression_pipeline import ExpressionProjects,atomic
from protocol import expression_pipeline_install as installer


class Operation:
    def acquire(self): pass
    def release(self): pass


class Bridge:
    def __init__(self):
        import threading
        self.operation=Operation();self.lock=threading.RLock();self.network_maintenance=False
    def snapshot(self):return {'connection':{'status':'connected','port':'COM4'}}
    def ports(self):return {'ports':[{'port':'COM4','key':'head'}]}
    def _disconnect(self):pass
    def _connect(self,*args):pass


class Device:
    def __init__(self,run):self._identity=type('Identity',(),{'digest':'device'})()
    def read_verified(self,expected):return expected
    def boot(self,elf):return True


class ExpressionInstallTests(unittest.TestCase):
    def make_store(self,root):
        root=root.resolve()
        baseline=root/'base';baseline.mkdir();(baseline/'report.json').write_text(json.dumps({'graph_sha256':'old'}))
        run=root/'baseline-run';run.mkdir();(run/'plan.json').write_text(json.dumps({'app_elf_sha256':'elf'}))
        store=ExpressionProjects(root/'projects',baseline,run)
        p=store.create('test')
        build=root/'build';build.mkdir()
        p.update(revision=2,bindings={'happy':{'asset':'asset','mirror':False,'fps':25}},
            build={'revision':2,'folder':'build-2','installed':False,'status':'ready'},installation=None)
        store.save(p)
        return store,p,baseline,run

    def test_pre_device_failure_clears_pending_but_touched_failure_requires_recovery(self):
        for accessed,expected in ((False,'failed'),(True,'recovery_required')):
            with self.subTest(accessed=accessed),tempfile.TemporaryDirectory() as temp:
                store,p,_,_=self.make_store(Path(temp));store.job=dict(project=p['id'],revision=2,status='running',
                    stages=[],run=str(Path(temp)/'run'),previous_package=str(store.baseline),previous_run=str(store.baseline_run),
                    device_accessed=accessed)
                atomic(store.root/'install-pending.json',store.job)
                with mock.patch('protocol.expression_pipeline_install.install',side_effect=OSError('injected')):
                    store._install(p,Bridge())
                self.assertEqual(store.job['status'],expected)
                self.assertEqual((store.root/'install-pending.json').exists(),accessed)

    def test_adoption_io_failure_rolls_pair_back_and_restores_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();store,p,baseline,baseline_run=self.make_store(root)
            profile=root/'expression.json';before={'graph_sha256':'old','device':'same'};profile.write_text(json.dumps(before))
            store.job=dict(project=p['id'],revision=2,status='running',stages=[],run=str(root/'run'),
                previous_package=str(baseline),previous_run=str(baseline_run),device_accessed=True)
            project=dict(p,baseline=str(baseline),baseline_run=str(baseline_run))
            candidate,image=b'app',b'resources';expected=b'snapshot'
            report={'graph_sha256':'new','image_sha256':'image'}
            transactions=[]
            def execute(*args,**kwargs):
                transactions.append(kwargs.get('recover',False))
                return {'phase':'restored' if kwargs.get('recover') else 'installed','boot_verified':True}
            results=iter([mock.Mock(returncode=0),mock.Mock(returncode=0)])
            with mock.patch.object(installer,'reviewed',return_value=(root/'build',expected,candidate,'device',report,candidate,image)), \
                 mock.patch.object(installer,'host_configuration',return_value=(['powershell.exe'],profile)), \
                 mock.patch.object(installer,'connected_head',return_value=('COM4','head')), \
                 mock.patch.object(installer,'device_adapter',return_value=type('Adapter',(),{'Device':Device})), \
                 mock.patch.object(installer.tx,'prepare',return_value={'targets':[{'bytes':len(candidate)}]}), \
                 mock.patch.object(installer.tx,'execute',side_effect=execute), \
                 mock.patch.object(installer.subprocess,'run',side_effect=lambda *a,**k:next(results)), \
                 mock.patch.object(installer.shutil,'copy2',side_effect=OSError('injected host disk error')):
                with self.assertRaisesRegex(OSError,'injected host disk error'):
                    installer.install(store,project,Bridge())
            self.assertEqual(transactions,[False,True])
            self.assertEqual(json.loads(profile.read_text()),before)
            self.assertEqual(store.baseline,baseline)
            self.assertTrue(store.job['recovery_required'] is False)

    def test_preexisting_head_must_match_audited_adapter_port(self):
        bridge=Bridge()
        self.assertEqual(installer.connected_head(bridge),('COM4','head'))
        with mock.patch.object(bridge,'snapshot',return_value={'connection':{'status':'connected','port':'COM8'}}), \
             mock.patch.object(bridge,'ports',return_value={'ports':[{'port':'COM8','key':'other'}]}):
            with self.assertRaisesRegex(ValueError,'COM4'):
                installer.connected_head(bridge)

    def exercise_install(self,root,*,failure=None,read_error=False,stop_error=False):
        store,p,baseline,baseline_run=self.make_store(root)
        profile=root/'expression.json';profile.write_text(json.dumps({'graph_sha256':'old','credential':'retained'}))
        store.job=dict(project=p['id'],revision=2,status='running',stages=[],run=str(root/'run'),
            previous_package=str(baseline),previous_run=str(baseline_run))
        bridge=Bridge();bridge._connect=mock.Mock();bridge._disconnect=mock.Mock()
        calls=[]
        def execute(run,device,**kwargs):
            recovering=kwargs.get('recover',False);calls.append(recovering)
            if failure in ('write','recover'):
                (run/'state.json').write_text(json.dumps({'phase':'needs_recovery'}))
                if not recovering or failure=='recover':raise OSError('injected write/recover')
            if failure=='preflight' and not recovering:raise OSError('injected preflight')
            return {'phase':'restored' if recovering else 'installed','boot_verified':True}
        def command(args,**kwargs):
            if '-Stop' in args and stop_error:raise OSError('injected stop')
            return mock.Mock(returncode=0)
        with ExitStack() as stack:
            patches=[mock.patch.object(installer,'reviewed',return_value=(root/'build',b'snapshot',b'app','device',
                         {'graph_sha256':'new'},b'app',b'resources')),
                mock.patch.object(installer,'host_configuration',return_value=(['powershell.exe'],profile)),
                mock.patch.object(installer,'device_adapter',return_value=type('Adapter',(),{'Device':Device})),
                mock.patch.object(installer.tx,'prepare',return_value={'targets':[{'bytes':3}]}),
                mock.patch.object(installer.tx,'execute',side_effect=execute),
                mock.patch.object(installer.subprocess,'run',side_effect=command)]
            for patch in patches:stack.enter_context(patch)
            if read_error:stack.enter_context(mock.patch.object(Device,'read_verified',side_effect=OSError('injected read')))
            error=None
            try:installer.install(store,p,bridge)
            except Exception as caught:error=caught
        return store,profile,bridge,calls,error

    def test_success_adopts_graph_and_restores_usb(self):
        with tempfile.TemporaryDirectory() as temp:
            store,profile,bridge,calls,error=self.exercise_install(Path(temp).resolve())
            self.assertIsNone(error);self.assertEqual(calls,[False])
            self.assertEqual(json.loads(profile.read_text()),{'graph_sha256':'new','credential':'retained'})
            self.assertTrue(store.load(store.job['project'])['build']['installed'])
            bridge._connect.assert_called_once_with('COM4','head')
            self.assertFalse(bridge.network_maintenance)

    def test_write_failure_recovers_pair_before_service_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            store,profile,bridge,calls,error=self.exercise_install(Path(temp).resolve(),failure='write')
            self.assertIsNotNone(error);self.assertEqual(calls,[False,True])
            self.assertFalse(store.job['recovery_required'])
            self.assertEqual(json.loads(profile.read_text())['graph_sha256'],'old')
            bridge._connect.assert_called_once()

    def test_failed_recovery_keeps_service_stopped_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            store,profile,bridge,calls,error=self.exercise_install(Path(temp).resolve(),failure='recover')
            self.assertIsNotNone(error);self.assertEqual(calls,[False,True])
            self.assertTrue(store.job['recovery_required']);bridge._connect.assert_not_called()
            self.assertFalse(bridge.network_maintenance)

    def test_read_and_preflight_failures_boot_original_without_write(self):
        for options in ({'read_error':True},{'failure':'preflight'},{'stop_error':True}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as temp:
                store,profile,bridge,calls,error=self.exercise_install(Path(temp).resolve(),**options)
                self.assertIsNotNone(error);self.assertNotIn(True,calls)
                self.assertFalse(store.job['recovery_required']);bridge._connect.assert_called_once()

    def test_reopen_without_write_intent_verifies_identity_before_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();store,p,baseline,previous=self.make_store(root)
            (root/'tmp').mkdir();run=root/'tmp/layered-face-live-expression-test'
            store.job=dict(project=p['id'],revision=2,status='recovery_required',stages=[],run=str(run),
                previous_package=str(baseline),previous_run=str(previous))
            atomic(store.root/'install-pending.json',store.job)
            profile=root/'expression.json';profile.write_text(json.dumps({'graph_sha256':'new'}))
            order=[]
            class RecoverDevice:
                def __init__(self,folder):self._identity=None
                def read_verified(self,raw):
                    self._identity=type('Identity',(),{'digest':'device'})();order.append('verify');return raw
                def boot(self,elf):
                    if not self._identity:raise AssertionError('boot before identity')
                    order.append('boot');return True
            with mock.patch.object(installer,'ROOT',root), \
                 mock.patch.object(installer,'host_configuration',return_value=(['powershell.exe'],profile)), \
                 mock.patch.object(installer,'installed_pair',return_value=(b'snapshot',b'app','device','old')), \
                 mock.patch.object(installer,'device_adapter',return_value=type('Adapter',(),{'Device':RecoverDevice})), \
                 mock.patch.object(installer.subprocess,'run',return_value=mock.Mock(returncode=0)):
                installer.recover(store,Bridge())
            self.assertEqual(order,['verify','boot'])
            self.assertEqual(json.loads(profile.read_text())['graph_sha256'],'old')
            self.assertFalse(store.load(p['id'])['build']['installed'])

    def test_reopen_read_failure_still_boots_verified_identity_and_keeps_recovery_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();store,p,baseline,previous=self.make_store(root)
            (root/'tmp').mkdir();run=root/'tmp/layered-face-live-expression-test'
            store.job=dict(project=p['id'],revision=2,status='recovery_required',stages=[],run=str(run),
                previous_package=str(baseline),previous_run=str(previous))
            atomic(store.root/'install-pending.json',store.job)
            profile=root/'expression.json';profile.write_text(json.dumps({'graph_sha256':'new'}));order=[]
            class RecoverDevice:
                def __init__(self,folder):self._identity=None
                def read_verified(self,raw):
                    self._identity=type('Identity',(),{'digest':'device'})();order.append('verify');raise OSError('injected read')
                def boot(self,elf):order.append('boot');return True
            bridge=Bridge();bridge._connect=mock.Mock()
            with mock.patch.object(installer,'ROOT',root), \
                 mock.patch.object(installer,'host_configuration',return_value=(['powershell.exe'],profile)), \
                 mock.patch.object(installer,'installed_pair',return_value=(b'snapshot',b'app','device','old')), \
                 mock.patch.object(installer,'device_adapter',return_value=type('Adapter',(),{'Device':RecoverDevice})), \
                 mock.patch.object(installer.subprocess,'run',return_value=mock.Mock(returncode=0)):
                store._recover(bridge)
            self.assertEqual(order,['verify','boot'])
            self.assertEqual(store.job['status'],'recovery_required')
            self.assertTrue((store.root/'install-pending.json').is_file())
            bridge._connect.assert_not_called()


if __name__=='__main__':unittest.main()
