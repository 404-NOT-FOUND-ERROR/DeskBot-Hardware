"""Check the production COM4 adapter without opening a serial port."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from protocol import resource_read_specialty as task
from protocol import studio_app_deployment as tx

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("resource_read_adapter_test",ROOT/"scripts/run-resource-read-specialty.py")
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ResourceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs=task.load_inputs()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="resource-adapter-test-",dir=ROOT/"tmp")
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.device=runner.Device.__new__(runner.Device)
        self.device.identity=Mock(return_value=task.catalog.IDENTITY_SHA)
        self.device._identity=Mock(mac="00:11:22:33:44:55")
        self.device._capture=Mock(return_value=self.inputs["snapshot"][:tx.START])
        self.device._new_path=Mock(return_value=self.folder/"reference.bin")
        self.device._invoke=Mock(return_value="-- verify OK (digest matched)")
        self.device.deployment_plan=tx.prepare(self.inputs["snapshot"],self.inputs["image"],
                task.catalog.IDENTITY_SHA,task.catalog.APP_SIZE,edition="E3c")

    def test_reference_uses_fresh_prefix_and_full_raw_device_digest(self):
        raw=self.device.read_reference(self.inputs)
        self.assertEqual(raw,self.inputs["snapshot"])
        self.device._capture.assert_called_once_with("current-prefix",0,tx.START)
        args=self.device._invoke.call_args.args[1]
        self.assertEqual(args,("verify_flash","--flash_mode","keep","--flash_size","keep",
                              "--flash_freq","keep","0x0",str(self.folder/"reference.bin")))
        self.assertEqual((self.folder/"reference.bin").read_bytes(),raw)

    def test_bad_digest_and_static_prefix_change_stop_preparation(self):
        self.device._invoke.return_value="digest mismatch"
        with self.assertRaisesRegex(ValueError,"digest"):
            self.device.read_reference(self.inputs)
        prefix=bytearray(self.inputs["snapshot"][:tx.START]);prefix[0x8000]^=1
        self.device._capture.return_value=bytes(prefix)
        self.device._invoke.reset_mock()
        with self.assertRaisesRegex(ValueError,"Flash differs"):
            self.device.read_reference(self.inputs)
        self.device._invoke.assert_not_called()

    def test_probe_capture_is_automatic_and_original_boot_uses_saved_image(self):
        self.device._serial=Mock(return_value="captured")
        with patch.object(runner.base.parent,"inspect_boot",return_value={"normal_boot":True}) as inspect:
            self.device.boot(True)
            self.assertEqual(self.device.probe_log,"captured")
            self.device._serial.assert_called_with("mapping-automatic",450)
            self.assertEqual(inspect.call_args.args[1],self.inputs["image"])
            self.device.boot(False)
            self.device._serial.assert_called_with("catalog-restored",45)
            self.assertEqual(inspect.call_args.args[1],self.inputs["original"])

    def test_run_names_reject_traversal_and_other_ports(self):
        for label in ("../other","resource-read-20261108-COM8/other","C:/outside","other"):
            with self.assertRaises(ValueError):runner.folder_for(label,phase="prepare")


if __name__=="__main__":unittest.main()
