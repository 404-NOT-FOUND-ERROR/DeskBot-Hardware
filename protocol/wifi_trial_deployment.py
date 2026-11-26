"""Bind a network-specific build to the existing App-only transaction."""
import json
from pathlib import Path

from protocol import airboat_role_deployment as paired
from protocol import studio_app_deployment as app
from protocol import display_clock_e3f9d3 as display
from protocol.storage_capacity import image_facts


class ReviewedTrial:
    def __init__(self, root, pins):
        self.root, self.pins = Path(root), pins
        self.EDITION = pins['edition']
        self.ROLLBACK_TARGET = pins['rollback_edition']
        self.IDENTITY_SHA = pins['identity_sha256']
        self.ORIGINAL_SIZE = pins['original_bytes']
        self.SOURCE = Path(pins['profile']) / 'build/xiaozhi.bin'
        self.DISPLAY_ERRORS = display.DISPLAY_ERRORS

    def check_current(self, raw, reference=None):
        if (len(raw) != app.FLASH
                or app.sha(raw[:0x9000] + raw[0xD000:]) != self.pins['original_static_sha256']
                or app.sha(raw[app.START:app.START + self.ORIGINAL_SIZE]) != self.pins['original_sha256']):
            raise ValueError('installed_wifi_trial_baseline_changed')

    def reviewed_candidate(self, raw=None, root=None):
        raw = (self.root / self.SOURCE).read_bytes() if raw is None else raw
        if len(raw) != self.pins['candidate_bytes'] or app.sha(raw) != self.pins['candidate_sha256']:
            raise ValueError('wifi_trial_candidate_changed')
        facts = image_facts(raw)
        start = facts['segments'][0]['file_offset']
        if facts['configured_flash_bytes'] != app.FLASH or raw[start + 144:start + 176].hex() != self.pins['app_elf_sha256']:
            raise ValueError('wifi_trial_hardware_profile_changed')
        return raw

    def check_plan(self, plan, *, candidate=True, root=None):
        plan.validate()
        m = plan.metadata
        if (m['edition'] != 'E3c' or m['identity_sha256'] != self.IDENTITY_SHA
                or m['original_bytes'] != self.ORIGINAL_SIZE
                or m['candidate_bytes'] != self.pins['candidate_bytes']
                or m['candidate_sha256'] != self.pins['candidate_sha256']):
            raise ValueError('wifi_trial_plan_changed')
        self.check_current(plan.snapshot)
        if candidate:
            self.reviewed_candidate(plan.image)
        return plan

    def load_reference(self, root=None):
        folder = self.root / self.pins['baseline_run']
        resources = 'two-role-v1'
        if self.pins.get('paired_baseline'):
            raw, _, _, resources = installed_pair(folder)
        else:
            saved = app.load(folder)
            state = app.Journal(folder, saved).load()
            if not state or state['phase'] != 'installed':
                raise ValueError('installed_baseline_required')
            raw = saved.snapshot[:app.START] + saved.candidate + saved.snapshot[app.START + len(saved.candidate):]
        self.check_current(raw)
        return dict(snapshot=raw, identity_sha256=self.IDENTITY_SHA,
                    report=dict(edition=self.ROLLBACK_TARGET, resources=resources))

    def prepare_plan(self, snapshot, identity, image, root=None):
        self.check_current(snapshot)
        return self.check_plan(app.prepare(snapshot, self.reviewed_candidate(image), identity,
                                           self.ORIGINAL_SIZE, edition='E3c'))

    def check_build_evidence(self, root=None):
        raw = (self.root / self.pins['profile'] / 'build/report.json').read_bytes()
        if app.sha(raw) != self.pins['build_report_sha256']:
            raise ValueError('wifi_trial_build_report_changed')
        report = json.loads(raw)
        if (report['edition'] != self.EDITION or report['lab'] is not False
                or report['effective_config_equal'] is not True or not all(report['protected_equal'].values())
                or report['app'] != dict(bytes=self.pins['candidate_bytes'], sha256=self.pins['candidate_sha256'])
                or report['app_elf_sha256'] != self.pins['app_elf_sha256']):
            raise ValueError('wifi_trial_verified_build_required')
        if self.EDITION in ('E3f-9d.6-body-wifi', 'E3f-9d.7-network-settings') and (
                report.get('network_workers') != 1 or report.get('worker_stack_bytes') != 6144
                or set(report.get('features', [])) != {'wifi_expression', 'wifi_head_touch'}):
            raise ValueError('combined_wifi_single_worker_required')
        if self.EDITION == 'E3f-9d.7-network-settings' and (
                report.get('network_settings') is not True or report.get('nvs_namespace') != 'deskbot_net'
                or any('main/boards/esp_vocat/' + name not in report.get('source_sha256', {}) for name in
                    ('body_network_config.h', 'body_network_config.inc', 'studio_usb_session.h', 'studio_usb_diagnostic.cc'))):
            raise ValueError('persisted_network_settings_build_required')
        if self.pins.get('paired_baseline') and (report.get('layered_face') is not True
                or report.get('role_assets') != 'airboat-face-layered-v1'):
            raise ValueError('layered_resource_compatible_app_required')
        return dict(image=self.reviewed_candidate(), candidate=dict(variant=self.EDITION,
                    bytes=self.pins['candidate_bytes'], sha256=self.pins['candidate_sha256'],
                    app_elf_sha256=self.pins['app_elf_sha256']),
                    build_report_sha256=app.sha(raw), offline_report_sha256=None, path_audit_sha256=None)

    def check_display_boot(self, log):
        display.check_display_boot(log)
        tag = ('DeskBotBodyWifi' if self.EDITION in ('E3f-9d.6-body-wifi', 'E3f-9d.7-network-settings') else
               'DeskBotTouchWifi' if self.EDITION.endswith('touch-wifi') else 'DeskBotExprWifi')
        if tag + ': ready' not in log:
            raise ValueError('wifi_trial_worker_not_started')


def installed_pair(folder):
    """Read the installed App and resources, including the bytes outside both writes."""
    plan_raw = (folder / 'plan.json').read_bytes()
    plan = json.loads(plan_raw)
    state = json.loads((folder / 'state.json').read_bytes())
    if (state.get('phase') != 'installed' or state.get('plan_sha256') != app.sha(plan_raw)
            or not all(state.get(check) is True for check in
                       ('readback_equal', 'outside_targets_equal', 'nvs_equal', 'boot_verified'))):
        raise ValueError('verified_installed_pair_required')
    snapshot = (folder / 'snapshot-32m.bin').read_bytes()
    paired.validate(plan, snapshot)
    payloads = {target['file']: (folder / target['file']).read_bytes() for target in plan['targets']}
    paired.validate(plan, snapshot, payloads)
    candidate = payloads['candidate-app.bin']
    pins = json.loads((folder / 'pins.json').read_bytes())
    size = pins['candidate_bytes']
    if type(size) is not int or not 0 < size <= len(candidate):
        raise ValueError('installed_pair_app_size_changed')
    image = candidate[:size]
    segment = image_facts(image)['segments'][0]['file_offset']
    if (app.sha(image) != pins['candidate_sha256']
            or image[segment + 144:segment + 176].hex() != plan['app_elf_sha256']):
        raise ValueError('installed_pair_app_identity_changed')
    return paired.patched(plan, snapshot, payloads), image, plan['identity_sha256'], plan['graph_sha256']


def pin(root, profile, baseline_run, rollback_edition, *, paired_baseline=False):
    root, profile, baseline_run = Path(root), Path(profile), Path(baseline_run)
    baseline = root / baseline_run
    if paired_baseline:
        snapshot, original, identity, _ = installed_pair(baseline)
    else:
        saved = app.load(baseline)
        state = app.Journal(baseline, saved).load()
        receipt = json.loads((baseline / 'install-result.json').read_bytes())
        if (not state or state['phase'] != 'installed' or receipt['result'] != 'candidate_installed'
                or not receipt['readback']['app_byte_equal'] or not receipt['boot']['firmware_matches_candidate']):
            raise ValueError('verified_installed_baseline_required')
        snapshot = saved.snapshot[:app.START] + saved.candidate + saved.snapshot[app.START + len(saved.candidate):]
        original, identity = saved.image, saved.metadata['identity_sha256']
    report_raw = (root / profile / 'build/report.json').read_bytes()
    report = json.loads(report_raw)
    if report['edition'] not in ('E3f-9d.5-touch-wifi', 'E3f-9d.5-expression-wifi', 'E3f-9d.6-body-wifi',
                                'E3f-9d.7-network-settings'):
        raise ValueError('known_wifi_trial_required')
    pins = dict(edition=report['edition'], rollback_edition=rollback_edition,
                profile=profile.as_posix(), baseline_run=baseline_run.as_posix(),
                identity_sha256=identity, original_bytes=len(original),
                original_sha256=app.sha(original), original_static_sha256=app.sha(snapshot[:0x9000] + snapshot[0xD000:]),
                candidate_bytes=report['app']['bytes'], candidate_sha256=report['app']['sha256'],
                app_elf_sha256=report['app_elf_sha256'], build_report_sha256=app.sha(report_raw))
    if paired_baseline:
        pins['paired_baseline'] = True
    reviewed = ReviewedTrial(root, pins)
    reviewed.check_build_evidence()
    reviewed.load_reference()
    return pins
