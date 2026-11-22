"""Pinned E3f-9d App and two-role resources, with separate recovery records."""
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path

from protocol import studio_app_deployment as app
from protocol import studio_resource_installation as resources
from protocol import studio_common_e3f7_deployment as baseline
from protocol.storage_capacity import image_facts

ROOT=Path(__file__).resolve().parents[1]
EDITION,ROLLBACK_TARGET='E3f-9d','E3f-7'
SOURCE=Path('tmp/studio-role-activation-e3f9d/on/xiaozhi.bin')
BUILD_REPORT=Path('artifacts/studio-role-activation-e3f9d/firmware-build-report.json')
RELEASE=BUILD_REPORT.with_name('release-evidence.json')
RELEASE_SHA='1268fdbb976011ebbb10216a2e59d666ac5aa54fac2d642c02d0ce90e35c0e1e'
APP_SIZE=4540304
APP_SHA='e49290b865e0632a1bf62b7a0633ea640541e8188345197411b3918510b95f00'
APP_ELF_SHA='baaf874bf092127ccab84e0b0428edc5a61f6e737d222d3da0a40a174ae280ba'
ORIGINAL_SIZE,ORIGINAL_SHA=baseline.APP_SIZE,baseline.APP_SHA
IDENTITY_SHA=baseline.IDENTITY_SHA
RESOURCE_SHA='c187661fbd02a0c007ea9ebe5efc691766735926d7f3113ea2c58cb5c4d0a959'
ORIGINAL_RESOURCE_SHA='e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748'
RESOURCE=Path('tmp/deskbot-role-packages-e3f9b/3bee228329bc22ebb45893618aea25a14165b804e18870156e4aa66ad9224f28/assets-8m.bin')
RESOURCE_SCHEMA='deskbot.resource_installation_plan.e3f9d/v1'
STATIC_BASELINE_SHA='fe48e9ebfde714b8432ff3130c94da652f3b63ffa9b64ec3fa2ab5b165563dbd'
RESOURCE_STATIC_SHA='f073f58d36ed9bd36613cccaf950d6a7d99f308ac61c1a7eefd1c976e53cb15e'


def load_reference(root=ROOT):
    spec=importlib.util.spec_from_file_location('roles9d_original',root/'scripts/prepare-studio-layout-release-e3f9a.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    return dict(snapshot=original.reference(),identity_sha256=IDENTITY_SHA,
        report=dict(edition=ROLLBACK_TARGET,resources='E1.45'))


def check_current(raw,reference):
    old=reference['snapshot']
    if (type(raw) is not bytes or len(raw)!=app.FLASH or len(old)!=app.FLASH
            or raw[:0x9000]!=old[:0x9000] or raw[0xD000:]!=old[0xD000:]
            or app.sha(raw[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA
            or app.sha(raw[:0x9000]+raw[0xD000:])!=STATIC_BASELINE_SHA):
        raise ValueError('installed_E3f7_E145_required')


def reviewed_candidate(raw=None,root=ROOT):
    raw=(root/SOURCE).read_bytes() if raw is None else raw
    if len(raw)!=APP_SIZE or app.sha(raw)!=APP_SHA: raise ValueError('reviewed_role_App_changed')
    facts=image_facts(raw);start=facts['segments'][0]['file_offset']
    if facts['configured_flash_bytes']!=app.FLASH or raw[start+144:start+176].hex()!=APP_ELF_SHA:
        raise ValueError('role_App_profile_changed')
    return raw


def check_build_evidence(root=ROOT):
    raw=(root/RELEASE).read_bytes()
    if app.sha(raw)!=RELEASE_SHA: raise ValueError('reviewed_role_release_changed')
    release=json.loads(raw)
    if release.get('result')!='approved' or release.get('edition')!=EDITION:
        raise ValueError('reviewed_role_release_required')
    for name,digest in release['files'].items():
        if app.sha((root/name).read_bytes())!=digest: raise ValueError('role_release_input_changed: '+name)
    build=json.loads((root/BUILD_REPORT).read_bytes())
    if (build['result']!='off_on_builds_verified' or build['edition']!=EDITION
            or build['disabled_comparison']['executable_and_data_equal'] is not True
            or build['new_tasks']!=0 or build['usb_stack_bytes']!=6144
            or build['builds'][1]['artifacts']['xiaozhi.bin']!=dict(bytes=APP_SIZE,sha256=APP_SHA)):
        raise ValueError('paired_role_build_required')
    return dict(image=reviewed_candidate(root=root),candidate=dict(variant='roles-on',bytes=APP_SIZE,
        sha256=APP_SHA,app_elf_sha256=APP_ELF_SHA),build_report_sha256=app.sha((root/BUILD_REPORT).read_bytes()),
        offline_report_sha256=release['files'][str(BUILD_REPORT.with_name('offline-validation.json')).replace('\\','/')],
        path_audit_sha256=release['files'][str(BUILD_REPORT.with_name('peer-review.json')).replace('\\','/')])


def prepare_plan(snapshot,identity,image,root=ROOT):
    check_current(snapshot,load_reference(root))
    if identity!=IDENTITY_SHA: raise ValueError('role_plan_device_changed')
    return check_plan(app.prepare(snapshot,reviewed_candidate(image,root),identity,ORIGINAL_SIZE,edition='E3c'),root=root)


def check_plan(plan,*,candidate=True,root=ROOT):
    plan.validate();m=plan.metadata
    if (m['edition']!='E3c' or m['identity_sha256']!=IDENTITY_SHA or m['original_bytes']!=ORIGINAL_SIZE
            or m['candidate_bytes']!=APP_SIZE or m['candidate_sha256']!=APP_SHA
            or app.sha(plan.snapshot[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA
            or app.sha(plan.snapshot[resources.START:resources.START+resources.SIZE])!=ORIGINAL_RESOURCE_SHA
            or app.sha(plan.snapshot[:0x9000]+plan.snapshot[0xD000:])!=STATIC_BASELINE_SHA):
        raise ValueError('role_App_plan_changed')
    if candidate:
        reviewed_candidate(plan.image,root);check_current(plan.snapshot,load_reference(root))
    return plan


def validate_resource_snapshot(raw):
    if (type(raw) is not bytes or len(raw)!=app.FLASH
            or app.sha(raw[0x8000:0x8C00])!=resources.TABLE_SHA
            or raw[0x8C00:0x9000]!=b'\xff'*1024
            or app.sha(raw[app.START:app.START+APP_SIZE])!=APP_SHA
            or app.sha(raw[resources.START:resources.START+resources.SIZE])!=ORIGINAL_RESOURCE_SHA
            or app.sha(raw[:0x9000]+raw[0xD000:])!=RESOURCE_STATIC_SHA):
        raise ValueError('role_App_with_E145_resources_required')


@dataclass(frozen=True)
class ResourceRecovery(resources.RecoveryPlan):
    def validate(self):
        m=self.metadata
        keys={'schema','mode','offset','bytes','identity_sha256','snapshot_sha256','rollback_sha256','candidate_sha256'}
        if (type(m) is not dict or set(m)!=keys or m['schema']!=RESOURCE_SCHEMA or m['mode'] not in ('offline','live')
                or type(m['offset']) is not int or type(m['bytes']) is not int
                or (m['offset'],m['bytes'])!=(resources.START,resources.SIZE)
                or m['identity_sha256']!=IDENTITY_SHA or m['candidate_sha256']!=RESOURCE_SHA):
            raise ValueError('role_resource_plan_changed')
        validate_resource_snapshot(self.snapshot)
        if app.sha(self.snapshot)!=m['snapshot_sha256'] or app.sha(self.rollback)!=m['rollback_sha256']:
            raise ValueError('role_resource_backup_changed')


@dataclass(frozen=True)
class ResourcePlan(resources.Plan):
    def validate(self):
        ResourceRecovery(self.metadata,self.snapshot).validate()
        if type(self.image) is not bytes or len(self.image)!=resources.SIZE or app.sha(self.image)!=RESOURCE_SHA:
            raise ValueError('role_resource_candidate_changed')


def prepare_resources(snapshot,image,*,mode='live'):
    plan=ResourcePlan(dict(schema=RESOURCE_SCHEMA,mode=mode,offset=resources.START,bytes=resources.SIZE,
        identity_sha256=IDENTITY_SHA,snapshot_sha256=app.sha(snapshot),
        rollback_sha256=app.sha(snapshot[resources.START:resources.START+resources.SIZE]),candidate_sha256=app.sha(image)),snapshot,image)
    plan.validate();return plan


def load_resources(folder,*,recovery=False):
    raw=json.loads(resources._safe(folder,'plan.json').read_bytes(),object_pairs_hook=app._unique)
    if type(raw) is not dict or set(raw)!={'metadata','plan_id'}: raise ValueError('role_resource_record_changed')
    original=ResourceRecovery(raw['metadata'],resources._safe(folder,'snapshot-32m.bin').read_bytes());original.validate()
    if original.id!=raw['plan_id'] or resources._safe(folder,'rollback-assets-8m.bin').read_bytes()!=original.rollback:
        raise ValueError('role_resource_saved_backup_changed')
    if recovery:return original
    plan=ResourcePlan(original.metadata,original.snapshot,resources._safe(folder,'candidate-assets-8m.bin').read_bytes())
    plan.validate();return plan
