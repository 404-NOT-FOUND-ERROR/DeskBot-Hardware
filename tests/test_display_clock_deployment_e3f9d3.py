"""Exercise the actual App transport with memory-backed esptool responses."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as app
from protocol import display_clock_e3f9d3 as fixed
from protocol.storage_upper_experiment_esptool import Runner,_NORMAL_BOOT_MARKERS,parse_identity
from tests.test_studio_role_release_e3f9d import FakeRunner
from tests.test_studio_resource_adapter import FLASH_LOG,SECURITY_LOG

ROOT=fixed.ROOT


def cli():
    spec=importlib.util.spec_from_file_location('display_clock_test_cli',ROOT/'scripts/deploy-display-clock-e3f9d3.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class DisplayRunner(FakeRunner):
    def boot_log(self):
        self.boots+=1
        descriptor=cli().parent.entry.base.entry.parent.image_identity
        data=bytes(self.data[app.START:app.START+fixed.APP_SIZE])
        if app.sha(data)!=fixed.APP_SHA:
            data=bytes(self.data[app.START:app.START+fixed.ORIGINAL_SIZE])
        firmware=descriptor(data);frames=[]
        for index in range(4):
            hello=copy.deepcopy(self.hello)
            hello.update(sequence=index,uptime_ms=1105+index*10000,boot_id='boot-%032x'%self.boots)
            hello['device_id']='device-'+hashlib.sha256(b'deskbot-link-v1\0'+bytes.fromhex(self.mac.replace(':',''))).digest()[:16].hex()
            hello['firmware'].update(firmware)
            frames.append('I (%d) DeskBotLink: %s'%(hello['uptime_ms'],json.dumps(hello)))
        extra='\nGuru Meditation\n' if self.boots in self.fail_boots else ''
        if app.sha(bytes(self.data[app.START:app.START+fixed.APP_SIZE]))==fixed.APP_SHA:
            extra+='\n'+getattr(self,'display_error','')+'\n'
        return '\n'.join((*_NORMAL_BOOT_MARKERS,'USB input ready: /dev/secondary',*frames))+extra


class DisplayDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference=fixed.load_reference();cls.image=fixed.reviewed_candidate()
        cls.identity=parse_identity(FLASH_LOG,SECURITY_LOG).digest

    def setUp(self):
        profile=patch.object(fixed,'IDENTITY_SHA',self.identity);profile.start();self.addCleanup(profile.stop)
        guard=patch.object(Runner,'run',side_effect=AssertionError('real process in offline test'));guard.start();self.addCleanup(guard.stop)
        temp=tempfile.TemporaryDirectory(prefix='display-deploy-',dir=ROOT/'tmp');self.addCleanup(temp.cleanup)
        self.work=Path(temp.name)
        self.plan=fixed.prepare_plan(self.reference['snapshot'],self.identity,self.image)
        app.persist(self.work,self.plan)
        self.module=cli();self.device=self.module.DisplayDevice(self.work,self.identity,self.plan)
        self.runner=DisplayRunner(self.plan.snapshot);self.device.runner=self.runner

    def test_installed_pair_and_fresh_nvs(self):
        self.assertEqual(app.sha(self.reference['snapshot']),fixed.REFERENCE_SHA)
        raw=bytearray(self.plan.snapshot);raw[0x9000]^=1;fixed.check_current(bytes(raw),self.reference)
        for offset in (0,0x8000,0x10000,0x1000000,0x1800000):
            with self.subTest(offset=offset):
                raw=bytearray(self.plan.snapshot);raw[offset]^=1
                with self.assertRaises(ValueError):fixed.check_current(bytes(raw),self.reference)

    def test_candidate_and_device_identity(self):
        raw=bytearray(self.image);raw[-1]^=1
        with self.assertRaises(ValueError):fixed.reviewed_candidate(bytes(raw))
        with self.assertRaises(ValueError):fixed.prepare_plan(self.plan.snapshot,'0'*64,self.image)

    def test_exact_app_install_preserves_nvs_and_two_role_resources(self):
        result=app.install(self.plan,self.device,app.Journal(self.work,self.plan))
        self.assertEqual(result['result'],'candidate_installed')
        self.assertEqual(self.runner.writes,[(app.START,self.plan.candidate)])
        self.assertEqual(bytes(self.runner.data[:app.START]),self.plan.snapshot[:app.START])
        end=app.START+len(self.plan.candidate)
        self.assertEqual(bytes(self.runner.data[end:]),self.plan.snapshot[end:])

    def test_partial_write_and_failed_boot_restore_same_run_app(self):
        for failure in ('write','boot'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory(prefix='display-fault-',dir=ROOT/'tmp') as name:
                work=Path(name);app.persist(work,self.plan)
                device=self.module.DisplayDevice(work,self.identity,self.plan)
                runner=DisplayRunner(self.plan.snapshot);device.runner=runner
                (runner.fail_writes if failure=='write' else runner.fail_boots).add(1)
                with self.assertRaises((ValueError,OSError)):app.install(self.plan,device,app.Journal(work,self.plan))
                self.assertEqual(bytes(runner.data),self.plan.snapshot)
                self.assertEqual(app.Journal(work,self.plan).load()['phase'],'restored')

    def test_recovery_needs_only_this_run_backup(self):
        journal=app.Journal(self.work,self.plan);journal.save('restore_required')
        (self.work/'candidate-app.bin').unlink()
        with patch.object(fixed,'load_reference',side_effect=AssertionError('historical dependency')):
            recovery=fixed.check_plan(app.load_recovery(self.work),candidate=False)
            device=self.module.DisplayDevice(self.work,self.identity,recovery);device.runner=self.runner
            self.runner.data[app.START:app.START+32]=b'\x00'*32
            app.recover(recovery,device,journal)
        self.assertEqual(bytes(self.runner.data),self.plan.snapshot)

    def test_wrong_write_scope_and_resource_change_stop_before_write(self):
        for args in (('erase_flash',),('write_flash','0x8000','bad.bin'),('write_flash','0x10000','bad.bin')):
            with self.subTest(args=args),self.assertRaises(ValueError):self.device._invoke('invalid',args)
        self.assertFalse(self.runner.calls)
        journal=app.Journal(self.work,self.plan);journal.save('restore_required')
        self.runner.data[0x1000000]^=1
        with self.assertRaises(OSError):app.recover(self.plan,self.device,journal)
        self.assertFalse(self.runner.writes)


    def test_each_display_error_restores_original_automatically(self):
        for marker in fixed.DISPLAY_ERRORS:
            with self.subTest(marker=marker),tempfile.TemporaryDirectory(prefix='clock-display-fault-',dir=ROOT/'tmp') as name:
                work=Path(name);app.persist(work,self.plan)
                device=self.module.DisplayDevice(work,self.identity,self.plan)
                runner=DisplayRunner(self.plan.snapshot);runner.display_error=marker;device.runner=runner
                with self.assertRaisesRegex(ValueError,'candidate_display_transport_failed'):
                    app.install(self.plan,device,app.Journal(work,self.plan))
                self.assertEqual(bytes(runner.data),self.plan.snapshot)
                self.assertEqual(app.Journal(work,self.plan).load()['phase'],'restored')
                self.assertEqual(runner.writes,[(app.START,self.plan.candidate),(app.START,self.plan.rollback)])

    def test_display_error_gate_uses_actual_captured_log(self):
        observed=(ROOT/'tmp/e3c-live-20261113-e3f9d2-live1/captures/051-eat-boot.log').read_text(encoding='utf-8')
        with patch.object(self.module.parent.RoleAppDevice,'_serial',return_value=observed):
            with self.assertRaisesRegex(ValueError,'DMA TX underflow'):
                self.device.boot(True)
        self.assertFalse(self.device._candidate_display_check)


if __name__=='__main__':unittest.main()
