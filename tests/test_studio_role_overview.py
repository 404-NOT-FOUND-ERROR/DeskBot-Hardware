"""Whole-role resource/readiness analysis, with no command construction."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from protocol import studio_role_overview as p
from protocol import studio_installed_catalog as installed

ROOT = Path(__file__).resolve().parents[1]


class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = p.expression.packages.load_catalog()
        cls.reference = json.loads((ROOT / 'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json').read_bytes())

    def setUp(self):
        self.draft = deepcopy(self.catalog['initial'])
        self.context = dict(connection_id='connection-overview', target_id='device-'+'a'*32,
                            boot_id='boot-'+'b'*32, session_id='session-'+'b'*32+'-1')
        self.control = dict(**self.context, enabled=True, can_submit=True, age_ms=50,
            display=dict(expression_semantics=['neutral', 'embarrassed']),
            catalog=dict(status='complete', manifest=deepcopy(self.reference),
                **{k:self.context[k] for k in ('target_id','boot_id','session_id')}))

    def overview(self, source='device', **kwargs):
        return p.overview(self.draft, self.catalog, self.reference, source=source,
            control=self.control, context=self.context, request_id='overview-one',
            issued_ms=100, now_ms=kwargs.pop('now_ms', 150), **kwargs)

    def test_snapshot_counts_and_voice_status_are_explicit(self):
        result = self.overview('saved_snapshot')
        self.assertEqual((result['total'], result['expression_count'], result['status_count']), (29,28,1))
        self.assertEqual((result['counts']['matched'],result['counts']['state_managed']), (28,1))
        self.assertEqual(result['resource_matches'], 29)
        self.assertIsNone(result['context'])

    def test_device_capabilities_do_not_confuse_resources_with_playback(self):
        result = self.overview()
        self.assertEqual(result['counts'], dict(ready=2, matched=0, needs_publish=0, missing=0,
            interface_pending=26, state_managed=1, waiting=0))
        self.assertEqual(result['resource_matches'],29)
        self.assertEqual(result['context'], self.context)

    def test_analysis_never_constructs_or_returns_commands(self):
        with patch.object(p.expression, 'validate_command', side_effect=AssertionError('command constructed')):
            result = self.overview()
            row = p.expression.resolve(self.draft,'neutral',self.catalog,self.reference,control=self.control,
                source='device',context=self.context,request_id='one',issued_ms=1,now_ms=2,emit_command=False)
        self.assertEqual(row['status'],'ready')
        self.assertIsNone(row['command'])
        self.assertTrue(all('command' not in row for row in result['entries']))

    def test_default_single_expression_still_emits_same_command(self):
        args=dict(control=self.control,source='device',context=self.context,request_id='one',issued_ms=1,now_ms=2)
        row=p.expression.resolve(self.draft,'neutral',self.catalog,self.reference,**args)
        self.assertEqual(row['command']['operation'],'neutral')
        self.assertEqual(row['command'],p.expression.resolve(self.draft,'neutral',self.catalog,self.reference,**args)['command'])
        with self.assertRaisesRegex(ValueError,'analysis_mode'):
            p.expression.resolve(self.draft,'neutral',self.catalog,self.reference,**args,emit_command='false')

    def test_candidate_rewire_changes_one_semantic_and_playback(self):
        self.draft['roles'][0]['bindings']['embarrassed']='shy_candidate.eaf'
        result=self.overview('saved_snapshot')
        self.assertEqual(result['counts']['needs_publish'],1)
        row=next(r for r in result['entries'] if r['semantic_id']=='embarrassed')
        self.assertEqual((row['installed_resource_id'],row['resource_id']),('Happy.eaf','shy_candidate.eaf'))
        self.assertEqual(row['playback'],dict(layout_id='eye_anim',fps=25,loop=True))
        self.assertEqual((row['implicit_action_id'],row['action_completion']),('cat_nuzzle','fact_only'))

    def test_published_candidate_matches_installed_metadata(self):
        self.draft['roles'][0]['bindings']['embarrassed']='shy_candidate.eaf'
        entries=deepcopy(self.reference['entries'])
        row=next(r for r in entries if r['kind']=='expression' and r['id']=='embarrassed')
        asset=self.catalog['assets']['shy_candidate.eaf']
        row.update(resource_id=asset['id'],resource_sha256=asset['deployed_sha256'],bytes=asset['bytes'],fps=25,loop=True)
        self.control['catalog']['manifest']=installed.build_manifest(entries,'c'*64,'d'*64)
        result=self.overview()
        self.assertEqual(result['counts']['needs_publish'],0)
        self.assertEqual(next(r for r in result['entries'] if r['semantic_id']=='embarrassed')['status'],'ready')

    def test_missing_binding_keeps_current_resource_visible(self):
        self.draft['roles'][0]['bindings']['neutral']=None
        result=self.overview()
        row=next(r for r in result['entries'] if r['semantic_id']=='neutral')
        self.assertEqual((row['status'],row['installed_resource_id']),('missing','winking.eaf'))
        self.assertIsNone(row['playback'])

    def test_completely_unwired_role_still_shows_checked_device_resources(self):
        self.draft['roles'][0]['bindings']=dict.fromkeys(self.catalog['semantics'])
        result=self.overview()
        self.assertEqual(result['counts']['missing'],29)
        self.assertEqual(result['catalog_id'],self.reference['catalog_id'])
        self.assertTrue(all(row['installed_resource_id'] for row in result['entries']))

    def test_disconnected_incomplete_and_other_session_are_waiting(self):
        for changes in ({'enabled':False},{'catalog':dict(status='reading')},
                        {'catalog':{**self.control['catalog'],'session_id':'session-other'}}):
            with self.subTest(changes=changes):
                original=self.control
                self.control={**original,**changes}
                result=self.overview()
                self.assertEqual(result['counts']['waiting'],29)
                self.assertEqual(result['resource_matches'],0)
                self.assertIsNone(result['catalog_id'])
                self.assertTrue(all(r['installed_resource_id'] is None for r in result['entries']))
                self.control=original

    def test_busy_and_expired_state_preserve_binding_facts(self):
        for changes in ({'can_submit':False},{'age_ms':2000}):
            self.control.update(changes)
            result=self.overview()
            self.assertEqual(result['counts']['ready'],0)
            self.assertEqual(result['counts']['state_managed'],1)
            self.assertEqual(result['resource_matches'],29)

    def test_expired_intent_has_no_trusted_device_directory(self):
        result=self.overview(now_ms=2100)
        self.assertEqual(result['counts']['waiting'],29)
        self.assertIsNone(result['catalog_id'])

    def test_role_selection_and_input_objects_remain_unchanged(self):
        self.draft['selected_role_id']=self.draft['roles'][1]['id']
        before=deepcopy((self.draft,self.catalog,self.reference,self.control,self.context))
        result=self.overview('saved_snapshot')
        self.assertEqual(result['role_id'],self.draft['selected_role_id'])
        self.assertEqual(sum(result['counts'].values()),29)
        self.assertEqual((self.draft,self.catalog,self.reference,self.control,self.context),before)

    def test_invalid_manifest_and_context_are_not_presented_as_current(self):
        self.control['catalog']['manifest']['catalog_id']='f'*64
        with self.assertRaisesRegex(ValueError,'catalog_digest'):
            self.overview()
        self.control['catalog']['manifest']=deepcopy(self.reference)
        self.context['connection_id']='connection-changed'
        self.assertEqual(self.overview()['counts']['waiting'],29)


if __name__=='__main__':
    unittest.main()
