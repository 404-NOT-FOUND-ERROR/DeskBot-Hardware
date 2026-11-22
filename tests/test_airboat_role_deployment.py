"""Exercise paired recovery after a partial write, changed bytes and failed boot."""
import json
from pathlib import Path
import tempfile
import unittest

from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.studio_resource_package import TABLE_SHA


class PairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=b'\xff'*app.FLASH
        cls.candidate=b'A'*8192
        cls.image=b'B'*tx.SIZE
        # Use a validated real partition table while keeping all test writes in memory.
        source=tx.ROOT/'tmp/e3c-live-20261121-wifi-network-live2/snapshot-32m.bin'
        cls.snapshot=cls.snapshot[:0x8000]+source.read_bytes()[0x8000:0x9000]+cls.snapshot[0x9000:]
        assert app.sha(cls.snapshot[0x8000:0x8c00])==TABLE_SHA
        cls.pins=dict(original_static_sha256=app.sha(cls.snapshot[:0x9000]+cls.snapshot[0xD000:]),
            candidate_sha256=app.sha(cls.candidate),candidate_bytes=len(cls.candidate),original_bytes=4096,
            identity_sha256=tx.IDENTITY, app_elf_sha256='a'*64,original_app_elf_sha256='b'*64)
        cls.report=dict(image_sha256=app.sha(cls.image),download_sha256={'deskbot-roles.json':'c'*64})

    def folder(self):
        temp=tempfile.TemporaryDirectory(dir=tx.ROOT/'tmp',prefix='pair-test-');self.addCleanup(temp.cleanup)
        path=Path(temp.name)
        plan=tx.prepare(self.snapshot,self.candidate,self.image,self.pins,self.report)
        payloads={'candidate-app.bin':self.candidate,'candidate-assets.bin':self.image}
        for name,raw in {**payloads,'plan.json':app.canonical(plan),'snapshot-32m.bin':self.snapshot}.items():
            (path/name).write_bytes(raw)
        return path,plan,payloads

    def test_install_and_restore_both_targets(self):
        folder,plan,_=self.folder();device=MemoryDevice(self.snapshot)
        self.assertEqual(tx.execute(folder,device)['phase'],'installed')
        self.assertEqual(device.writes,[0x10000,0x1000000])
        device.flash=device.flash[:0x9000]+b'N'*0x4000+device.flash[0xD000:]
        self.assertEqual(tx.execute(folder,device,recover=True)['phase'],'restored')
        self.assertEqual(device.flash[0x9000:0xD000],b'N'*0x4000)
        self.assertTrue(tx.outside_equal(plan,self.snapshot,device.flash))
        self.assertEqual(device.flash[0x10000:0x12000],self.snapshot[0x10000:0x12000])
        self.assertEqual(device.flash[tx.START:tx.START+tx.SIZE],self.snapshot[tx.START:tx.START+tx.SIZE])

    def test_partial_write_resumes_recovery_without_candidate_files(self):
        folder,_,_=self.folder();device=MemoryDevice(self.snapshot,fail_write=True)
        with self.assertRaises(OSError):tx.execute(folder,device)
        self.assertEqual(json.loads((folder/'state.json').read_bytes())['phase'],'needs_recovery')
        (folder/'candidate-app.bin').unlink();(folder/'candidate-assets.bin').unlink()
        device.fail_write=False
        self.assertEqual(tx.execute(folder,device,recover=True)['phase'],'restored')
        self.assertEqual(device.flash,self.snapshot)

    def test_bad_boot_preserves_recoverable_state(self):
        folder,_,_=self.folder();device=MemoryDevice(self.snapshot,boot_ok=False)
        with self.assertRaisesRegex(ValueError,'boot_incomplete'):tx.execute(folder,device)
        device.boot_ok=True
        self.assertEqual(tx.execute(folder,device,recover=True)['phase'],'restored')

    def test_changed_target_range_and_payload_are_rejected(self):
        folder,plan,payloads=self.folder()
        plan['targets'][1]['offset']=0x9000
        with self.assertRaisesRegex(ValueError,'ranges'):tx.validate(plan,self.snapshot,payloads)
        (folder/'candidate-app.bin').write_bytes(b'x'*8192)
        device=MemoryDevice(self.snapshot)
        with self.assertRaisesRegex(ValueError,'payload'):tx.execute(folder,device)
        self.assertEqual(device.writes,[])

    def test_changed_outside_target_stops_before_write(self):
        folder,_,_=self.folder();device=MemoryDevice(self.snapshot)
        device.flash=device.flash[:0x600000]+b'X'+device.flash[0x600001:]
        with self.assertRaises(ValueError):tx.execute(folder,device)
        self.assertEqual(device.writes,[])
        self.assertEqual(device.boots,[self.pins['original_app_elf_sha256']])

    def test_nvs_change_between_prepare_and_install_is_retained(self):
        folder,_,_=self.folder();device=MemoryDevice(self.snapshot)
        device.flash=device.flash[:0x9000]+b'N'*0x4000+device.flash[0xD000:]
        self.assertEqual(tx.execute(folder,device)['phase'],'installed')
        self.assertEqual(device.flash[0x9000:0xD000],b'N'*0x4000)


class MemoryDevice:
    def __init__(self,flash,fail_write=False,boot_ok=True):
        self.flash,self.fail_write,self.boot_ok=flash,fail_write,boot_ok
        self.writes=[];self.boots=[]

    def arm(self,plan,payloads):self.plan,self.payloads=plan,payloads
    def read_verified(self,reference,targets=False):return self.flash
    def boot(self,elf):self.boots.append(elf);return self.boot_ok
    def write(self,payloads):
        for target in self.plan['targets']:
            self.writes.append(target['offset'])
            self.flash=tx.patched({'targets':[target]},self.flash,payloads)
            if self.fail_write:raise OSError('simulated USB interruption')


if __name__=='__main__':unittest.main()
