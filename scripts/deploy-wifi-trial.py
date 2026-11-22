"""Install/recover a pinned private-network App using the accepted COM4 transaction."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol.wifi_trial_deployment import ReviewedTrial, pin


def inside(path):
    path = path.resolve()
    if path.is_symlink() or not path.is_relative_to((ROOT / 'tmp').resolve()):
        raise ValueError('trial_files_must_be_inside_workspace_tmp')
    return path


def configure(reviewed):
    spec = importlib.util.spec_from_file_location('wifi_app_transport', ROOT / 'scripts/deploy-display-clock-e3f9d3.py')
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    for module in (parent, parent.parent, parent.parent.entry, parent.parent.entry.base, parent.parent.entry.base.entry):
        module.reviewed = reviewed
    parent.parent.entry.base.entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET
    class TrialDevice(parent.DisplayDevice):
        def boot(self, candidate):
            if self.deployment_plan is not None:
                return super().boot(candidate)
            # A read-only preflight can fail before a new plan exists. Start
            # and verify the retained current App in that case.
            reference = reviewed.load_reference()['snapshot']
            image = reference[tx.START:tx.START + reviewed.ORIGINAL_SIZE]
            log = self._serial('preflight-baseline-boot', 45)
            return parent.parent.entry.base.entry.parent.inspect_boot(log, image, self._identity.mac,
                                                                       identity_required=True)
    parent.parent.entry.base.MenuTestDevice = TrialDevice
    return parent, TrialDevice


def run(args):
    if not re.fullmatch(r'e3c-live-[0-9]{8}-wifi-[a-z0-9-]{1,24}', args.run):
        raise ValueError('invalid_wifi_trial_run_label')
    folder = inside(ROOT / 'tmp' / args.run)
    if args.phase == 'prepare':
        if folder.exists():
            raise FileExistsError('use_a_fresh_trial_run')
        if not args.profile or not args.baseline_run or not args.rollback_edition:
            raise ValueError('prepare_requires_profile_and_installed_baseline')
        profile = inside(ROOT / args.profile).relative_to(ROOT)
        baseline = inside(ROOT / 'tmp' / args.baseline_run).relative_to(ROOT)
        pins = pin(ROOT, profile, baseline, args.rollback_edition)
    else:
        if args.profile or args.baseline_run or args.rollback_edition:
            raise ValueError('saved_pins_required_after_prepare')
        source = inside(ROOT / 'tmp' / args.from_run) if args.phase == 'prepare-live' else folder
        pins = json.loads((source / 'wifi-pins.json').read_bytes())
    reviewed = ReviewedTrial(ROOT, pins)
    parent, _ = configure(reviewed)
    source = inside(ROOT / 'tmp' / args.from_run) if args.from_run else None
    result = parent.parent.entry.base.run(args.phase, folder, source=source)
    if args.phase in ('prepare', 'prepare-live'):
        tx.write_new(folder / 'wifi-pins.json', tx.canonical(pins))
    return {key: value for key, value in result.items() if key in
            ('phase', 'result', 'edition', 'plan_id', 'erase_bytes', 'hardware_accessed', 'hardware_acceptance')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'check', 'prepare-live', 'install', 'recover'))
    parser.add_argument('--run', required=True)
    parser.add_argument('--from-run')
    parser.add_argument('--profile')
    parser.add_argument('--baseline-run')
    parser.add_argument('--rollback-edition')
    arguments = parser.parse_args()
    if (arguments.phase == 'prepare-live') != bool(arguments.from_run):
        parser.error('prepare-live requires --from-run')
    print(json.dumps(run(arguments)), flush=True)
