"""Mirror options integration tests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from protocol import studio_mirror_options as mirrors
from protocol import studio_resource_package as packages
from protocol import studio_role_layout as layouts
from tests.test_studio_role_layout_e3f6 import LayoutRoleTests

ROOT = Path(__file__).resolve().parents[1]


def v2(catalog):
    draft = deepcopy(catalog['initial'])
    draft['schema'] = mirrors.SCHEMA
    for role in draft['roles']:
        role['mirror_overrides'] = {}
    return draft


class MirrorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = packages.load_catalog()
        cls.requirements = layouts.load_requirements(cls.catalog)

    def test_defaults_and_manual_both_directions(self):
        for resource, semantic, default in [('eat.eaf','eat','full_frame'),('Happy.eaf','happy','eye_anim')]:
            self.assertEqual(layouts.playback(self.catalog,semantic,resource,self.requirements)['layout_id'],default)
            for override, wanted in [(True,'eye_anim'),(False,'full_frame')]:
                self.assertEqual(layouts.playback(self.catalog,semantic,resource,self.requirements,mirror_override=override)['layout_id'],wanted)

    def test_override_validation_and_schema_agree(self):
        import jsonschema
        schema=json.loads((ROOT/'protocol/deskbot-studio-draft-v2.schema.json').read_bytes())
        draft=v2(self.catalog)
        jsonschema.validate(draft,schema)
        for resource in self.catalog['assets']:
            if self.catalog['assets'][resource]['object_kind']!='listen':
                draft['roles'][0]['mirror_overrides']={resource:False}
                packages.validate_draft(draft,self.catalog)
                jsonschema.validate(draft,schema)
        for overrides in [None,[],{'listen.eaf':False},{'unknown.eaf':True},{'eat.eaf':0},{'eat.eaf':'false'}]:
            draft['roles'][0]['mirror_overrides']=overrides
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                packages.validate_draft(draft,self.catalog)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(draft,schema)

    def test_v1_v2_fields_are_versioned(self):
        draft=deepcopy(self.catalog['initial'])
        packages.validate_draft(draft,self.catalog)
        draft['roles'][0]['mirror_overrides']={}
        with self.assertRaises(ValueError): packages.validate_draft(draft,self.catalog)
        draft=v2(self.catalog)
        del draft['roles'][0]['mirror_overrides']
        with self.assertRaises(ValueError): packages.validate_draft(draft,self.catalog)

    def test_manifest_shares_resource_choice_and_omits_unreferenced_override(self):
        role=v2(self.catalog)['roles'][0]
        role['bindings']['happy']='eat.eaf'
        role['mirror_overrides']={'eat.eaf':True,'shy_candidate.eaf':False}
        value=mirrors.manifest(role,self.catalog)
        eat=[a for a in value['assets'] if a['resource_id']=='eat.eaf']
        self.assertEqual(len(eat),1)
        self.assertEqual((eat[0]['layout_id'],eat[0]['mirror'],eat[0]['source']),('eye_anim',True,'manual'))
        self.assertNotIn('shy_candidate.eaf',[a['resource_id'] for a in value['assets']])

    def test_listen_and_nonboolean_override_rejected_by_player_planning(self):
        for resource,semantic,override in [('listen.eaf','listen',False),('eat.eaf','eat',1)]:
            with self.assertRaises(ValueError):
                layouts.playback(self.catalog,semantic,resource,self.requirements,mirror_override=override)


class MirrorPlannerTests(LayoutRoleTests):
    # Reuse the current catalog fixture, including real installed layout profiles.
    def setUp(self):
        super().setUp()
        self.draft['schema']=mirrors.SCHEMA
        for role in self.draft['roles']: role['mirror_overrides']={}

    def test_manual_mismatch_requires_publish_and_has_no_command(self):
        self.draft['roles'][0]['mirror_overrides']['eat.eaf']=True
        result=self.resolve('eat')
        self.assertEqual((result['status'],result['playback']['layout_id']),('needs_publish','eye_anim'))
        self.assertIsNone(result['command'])

    def test_restoring_auto_restores_ready_plan(self):
        self.draft['roles'][0]['mirror_overrides']['eat.eaf']=True
        self.draft['roles'][0]['mirror_overrides'].pop('eat.eaf')
        self.assertEqual(self.resolve('eat')['status'],'ready')


@unittest.skipUnless(packages.BASE_IMAGE.is_file(),'retained E1.18 image required')
class MirrorPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=packages.load_catalog()
        cls.target=packages.load_target()
        cls.baseline=packages.BASE_IMAGE.read_bytes()

    def build(self,draft):
        return packages.assemble(draft,self.catalog,self.target,self.baseline,{})

    def test_legacy_remains_byte_exact(self):
        report,package,image=self.build(self.catalog['initial'])
        self.assertEqual(image,self.baseline)
        self.assertNotIn('layout_manifest',report)

    def test_actual_binary_contains_selected_mirror_and_unchanged_eaf(self):
        draft=v2(self.catalog)
        draft['roles'][0]['mirror_overrides']={'eat.eaf':True,'Happy.eaf':False}
        report,package,image=self.build(draft)
        resources,_=packages.load_legacy_tools()
        decoded=resources.parse_package_bytes(package,modern=False)['members']
        value=json.loads(decoded[mirrors.FILE])
        self.assertEqual(value,mirrors.manifest(draft['roles'][0],self.catalog))
        self.assertEqual(packages.sha(decoded[mirrors.FILE]),report['layout_manifest']['sha256'])
        entries={a['resource_id']:a for a in value['assets']}
        self.assertTrue(entries['eat.eaf']['mirror'])
        self.assertFalse(entries['Happy.eaf']['mirror'])
        old=resources.parse_package_bytes(packages.extract_package(self.baseline,len(image)),modern=False)['members']
        self.assertEqual(decoded['eat.eaf'],old['eat.eaf'])
        self.assertEqual(report['required_capabilities'],[mirrors.CAPABILITY])
        self.assertFalse(report['device_compatible'])
        self.assertTrue(report['checks']['layout_manifest_round_trip'])
        self.assertEqual(report['members']['added'],[mirrors.FILE])
        with tempfile.TemporaryDirectory(prefix='mirror-package-',dir=ROOT/'tmp') as directory:
            folder=packages.persist(report,package,image,directory)
            self.assertEqual((folder/'expression-package.bin').read_bytes(),package)

    def test_manual_changes_package_digest_and_auto_reset_reproduces_package(self):
        draft=v2(self.catalog)
        a,pa,_=self.build(draft)
        draft['roles'][0]['mirror_overrides']['eat.eaf']=True
        b,pb,_=self.build(draft)
        self.assertNotEqual(a['package_id'],b['package_id'])
        self.assertNotEqual(pa,pb)
        draft['roles'][0]['mirror_overrides'].clear()
        self.assertEqual(self.build(draft)[1],pa)
