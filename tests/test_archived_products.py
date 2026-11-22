"""Archive verification must bind both outputs and retain report meaning."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import archived_products as a


def encode(value):
    return json.dumps(value,sort_keys=True).encode()


class ArchivedProductTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.path=self.root/'artifact.html'
        self.saved,self.fresh=b'archived',b'generated'
        self.row=dict(path='artifact.html',variant='public',archived_sha256=a.sha(self.saved),generated_sha256=a.sha(self.fresh))
        self.records=dict(schema='deskbot.archived_regeneration/v1',products=[self.row])
        self.patch=patch.object(a,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.write()

    def write(self):
        path=self.root/a.RECORD;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(self.records))

    def test_both_endpoints_are_required(self):
        self.assertTrue(a.product_matches(self.saved,self.fresh,self.path))
        self.assertFalse(a.product_matches(b'changed',self.fresh,self.path))
        self.assertFalse(a.product_matches(self.saved,b'changed',self.path))
        self.assertFalse(a.product_matches(self.saved,self.fresh,self.path,local=True))
        self.assertFalse(a.product_matches(self.fresh,self.fresh,self.path))

    def test_image_archive_checks_bytes_and_report_scope(self):
        builder = "scripts/build-deskbot-studio-e3e.py"
        scope = a.IMAGE_REQUIRED["e3e"]
        report = dict(scope, hardware_accessed=False)
        (self.root / "index.html").write_bytes(self.saved)
        (self.root / "report.json").write_bytes(encode(report))
        rows = {
            (name, "public"): dict(builder=builder,
                archived_sha256=a.sha((self.root / name).read_bytes()), generated_sha256="a" * 64)
            for name in ("index.html", "report.json")
        }
        self.assertEqual(len(a.check_image_archive(rows, builder, self.root, scope)), 2)
        (self.root / "index.html").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "archived endpoint differs"):
            a.check_image_archive(rows, builder, self.root, scope)
        (self.root / "index.html").write_bytes(self.saved)
        altered = encode(dict(report, hardware_accessed=True))
        (self.root / "report.json").write_bytes(altered)
        rows[("report.json", "public")]["archived_sha256"] = a.sha(altered)
        with self.assertRaisesRegex(ValueError, "report scope differs"):
            a.check_image_archive(rows, builder, self.root, scope)

    def test_missing_and_duplicate_rows_fail(self):
        self.records['products']=[];self.write()
        self.assertFalse(a.product_matches(self.saved,self.fresh,self.path))
        self.records['products']=[self.row,self.row];self.write()
        with self.assertRaisesRegex(ValueError,'duplicate'):a.product_matches(self.saved,self.fresh,self.path)

    def test_report_results_cannot_change(self):
        old={'result':'passed','sources':[]};new={'result':'failed','sources':[]}
        with self.assertRaisesRegex(ValueError,'result or scope'):a.report_delta(encode(old),encode(new))

    def test_source_digests_must_match_current_file(self):
        (self.root/'source.py').write_bytes(b'current')
        old={'result':'passed','sources':[{'path':'source.py','sha256':a.sha(b'old')}]}
        new=deepcopy(old);new['sources'][0]['sha256']=a.sha(b'current')
        delta=a.report_delta(encode(old),encode(new));self.assertEqual(len(delta['sources']),1)
        (self.root/'source.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source digest'):a.report_delta(encode(old),encode(new))

    def test_unregistered_source_removal_and_addition_fail(self):
        old={'sources':[{'path':'source.py','sha256':'a'*64}]}
        with self.assertRaisesRegex(ValueError,'removed'):a.report_delta(encode(old),encode({'sources':[]}))
        with self.assertRaisesRegex(ValueError,'addition'):a.report_delta(encode({'sources':[]}),encode(old))

    def test_duplicate_source_rows_fail(self):
        row={'path':'source.py','sha256':'a'*64}
        with self.assertRaisesRegex(ValueError,'duplicate source'):a.report_delta(encode({'sources':[row,row]}),encode({'sources':[row]}))


if __name__=='__main__':unittest.main()
