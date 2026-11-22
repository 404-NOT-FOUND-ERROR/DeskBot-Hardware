"""Exercise the real patch profile and App transaction with an emulated port."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as app
from protocol import studio_role_query_fix_e3f9d1 as fixed
from protocol.storage_upper_experiment_esptool import Runner, _NORMAL_BOOT_MARKERS, parse_identity
from tests.test_studio_role_release_e3f9d import FakeRunner
from tests.test_studio_resource_adapter import FLASH_LOG, SECURITY_LOG

ROOT=fixed.ROOT


def cli():
    spec=importlib.util.spec_from_file_location('role_fix_test_cli',ROOT/'scripts/deploy-studio-role-query-fix-e3f9d1.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class PatchRunner(FakeRunner):
    def boot_log(self):
        self.boots+=1
        descriptor=cli().parent.entry.base.entry.parent.image_identity
        data=bytes(self.data[app.START:app.START+fixed.APP_SIZE])
        if app.sha(data)!=fixed.APP_SHA:data=bytes(self.data[app.START:app.START+fixed.ORIGINAL_SIZE])
        firmware=descriptor(data)
        frames=[]
        for index in range(4):
            hello=copy.deepcopy(self.hello)
            hello.update(sequence=index,uptime_ms=1105+index*10000,boot_id='boot-%032x'%self.boots)
            hello['device_id']='device-'+hashlib.sha256(b'deskbot-link-v1\0'+bytes.fromhex(self.mac.replace(':',''))).digest()[:16].hex()
            hello['firmware'].update(firmware)
            frames.append('I (%d) DeskBotLink: %s'%(hello['uptime_ms'],json.dumps(hello)))
        extra='\nGuru Meditation\n' if self.boots in self.fail_boots else ''
        return '\n'.join((*_NORMAL_BOOT_MARKERS,'USB input ready: /dev/secondary',*frames))+extra


class PatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference=fixed.load_reference()
        cls.image=fixed.reviewed_candidate()
        cls.identity=parse_identity(FLASH_LOG,SECURITY_LOG).digest

    def setUp(self):
        self.profile=patch.object(fixed,'IDENTITY_SHA',self.identity);self.profile.start();self.addCleanup(self.profile.stop)
        self.guard=patch.object(Runner,'run',side_effect=AssertionError('real process in offline test'));self.guard.start();self.addCleanup(self.guard.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='rolefix-test-',dir=ROOT/'tmp');self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name)
        self.plan=fixed.prepare_plan(self.reference['snapshot'],self.identity,self.image)
        app.persist(self.work,self.plan)
        self.module=cli();self.device=self.module.parent.RoleAppDevice(self.work,self.identity,self.plan)
        self.runner=PatchRunner(self.plan.snapshot);self.device.runner=self.runner

    def test_baseline_matches_completed_two_role_installation(self):
        self.assertEqual(app.sha(self.reference['snapshot']),fixed.REFERENCE_SHA)
        self.assertEqual(app.sha(self.plan.snapshot[0x1000000:0x1800000]),fixed.baseline.RESOURCE_SHA)

    def test_runtime_nvs_is_fresh_while_static_changes_are_rejected(self):
        raw=bytearray(self.plan.snapshot);raw[0x9000]^=1
        fixed.check_current(bytes(raw),self.reference)
        for offset in (0,0x8000,0x10000,0x1000000,0x1800000):
            with self.subTest(offset=offset):
                raw=bytearray(self.plan.snapshot);raw[offset]^=1
                with self.assertRaises(ValueError):fixed.check_current(bytes(raw),self.reference)

    def test_candidate_and_identity_are_pinned(self):
        raw=bytearray(self.image);raw[-1]^=1
        with self.assertRaises(ValueError):fixed.reviewed_candidate(bytes(raw))
        with self.assertRaises(ValueError):fixed.prepare_plan(self.plan.snapshot,'0'*64,self.image)

    def test_real_adapter_install_preserves_all_outside_app_bytes(self):
        result=app.install(self.plan,self.device,app.Journal(self.work,self.plan))
        self.assertEqual(result['result'],'candidate_installed')
        self.assertEqual(len(self.runner.writes),1)
        self.assertEqual(self.runner.writes[0],(app.START,self.plan.candidate))
        self.assertEqual(bytes(self.runner.data[:app.START]),self.plan.snapshot[:app.START])
        end=app.START+len(self.plan.candidate)
        self.assertEqual(bytes(self.runner.data[end:]),self.plan.snapshot[end:])

    def test_partial_write_and_boot_failure_restore_original_two_role_app(self):
        for failure in ('write','boot'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory(prefix='rolefix-fault-',dir=ROOT/'tmp') as name:
                work=Path(name);app.persist(work,self.plan)
                device=self.module.parent.RoleAppDevice(work,self.identity,self.plan)
                runner=PatchRunner(self.plan.snapshot);device.runner=runner
                (runner.fail_writes if failure=='write' else runner.fail_boots).add(1)
                with self.assertRaises((ValueError,OSError)):app.install(self.plan,device,app.Journal(work,self.plan))
                self.assertEqual(bytes(runner.data),self.plan.snapshot)
                self.assertEqual(app.Journal(work,self.plan).load()['phase'],'restored')

    def test_recovery_survives_missing_candidate_and_build_files(self):
        journal=app.Journal(self.work,self.plan);journal.save('restore_required')
        (self.work/'candidate-app.bin').unlink()
        with patch.object(fixed,'load_reference',side_effect=AssertionError('historical candidate dependency')):
            recovery=fixed.check_plan(app.load_recovery(self.work),candidate=False)
            device=self.module.parent.RoleAppDevice(self.work,self.identity,recovery);device.runner=self.runner
            self.runner.data[app.START:app.START+32]=b'\x00'*32
            app.recover(recovery,device,journal)
        self.assertEqual(bytes(self.runner.data),self.plan.snapshot)

    def test_write_geometry_and_payload_fail_before_runner(self):
        for args in (('erase_flash',),('write_flash','0x8000','bad.bin'),('write_flash','0x10000','bad.bin')):
            with self.subTest(args=args),self.assertRaises(ValueError):self.device._invoke('invalid',args)
        self.assertFalse(self.runner.calls)

    def test_changed_resources_block_rollback_before_write(self):
        journal=app.Journal(self.work,self.plan);journal.save('restore_required')
        self.runner.data[0x1000000]^=1
        with self.assertRaises(OSError):app.recover(self.plan,self.device,journal)
        self.assertFalse(self.runner.writes)


if __name__=='__main__':unittest.main()
